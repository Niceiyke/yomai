from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any

from yomai.config import QueueConfig
from yomai.exceptions import YomaiConfigError
from yomai.queue.base import QueuedWorkflow

if TYPE_CHECKING:
    from yomai.core.app import Yomai


class SwiftQQueueBackend:
    """swiftQ-backed queue adapter for async Yomai workflows.

    The adapter registers one internal task, `yomai.workflow.run`, on the
    swiftQ queue. Worker processes import the same Yomai app and therefore
    register the same task before calling `work()`.
    """

    task_name = "yomai.workflow.run"

    def __init__(self, app: Yomai, config: QueueConfig) -> None:
        self.app = app
        self.config = config
        try:
            from swiftq import Queue  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001 - optional dependency guard
            raise YomaiConfigError(
                "Queue backend 'swiftq' requires swiftQ to be installed.",
                hint="Install Yomai with queue extras or install swiftq[redis].",
                docs="https://yomai.dev/roadmap",
            ) from exc

        if not config.url:
            raise YomaiConfigError("QueueConfig.url is required for swiftQ backend.")

        redis_kwargs: dict[str, Any] = {
            "signing_key": config.signing_key,
            "prefix": config.prefix,
        }
        if "result_ttl" in inspect.signature(Queue.redis).parameters:
            redis_kwargs["result_ttl"] = config.job_ttl_secs
        self.queue = Queue.redis(config.url, **redis_kwargs)
        self._task = self._register_task()

    def _register_task(self) -> Any:
        @self.queue.task(
            name=self.task_name,
            retries=self.config.retries,
            retry_delay=self.config.retry_delay_secs,
            timeout=self.config.timeout_secs,
            queue=self.config.default_queue,
        )
        def run_workflow(
            job_id: str,
            path: str,
            body: dict[str, Any],
            session_id: str,
            path_kwargs: dict[str, Any],
        ) -> None:
            handler = self.app._workflow_handlers.get(path)
            if handler is None:
                raise RuntimeError(f"Unknown workflow route: {path}")
            asyncio.run(
                self.app._run_inline_workflow_job(
                    job_id=job_id,
                    path=path,
                    handler=handler,
                    body=body,
                    session_id=session_id,
                    path_kwargs=path_kwargs,
                )
            )

        return run_workflow

    async def enqueue_workflow(self, workflow: QueuedWorkflow) -> str:
        await asyncio.to_thread(
            self._task.apply_async,
            kwargs={
                "job_id": workflow.job_id,
                "path": workflow.route,
                "body": workflow.payload,
                "session_id": workflow.session_id or "",
                "path_kwargs": (workflow.metadata or {}).get("path_kwargs", {}),
            },
            unique_key=f"yomai:workflow:{workflow.job_id}",
        )
        return workflow.job_id

    async def cancel(self, job_id: str) -> None:
        # Yomai owns user-facing job records. swiftQ cancellation is best-effort
        # because the swiftQ job id is distinct from the Yomai job id in V2's
        # initial adapter.
        await self.app.jobs.update_status(job_id, "cancelled", error="Job cancelled")

    async def get_status(self, job_id: str):
        return await self.app.jobs.get(job_id)

    def work(
        self,
        *,
        queue: str | None = None,
        concurrency: int = 1,
        burst: bool = False,
        with_scheduler: bool = False,
        worker_id: str | None = None,
    ) -> None:
        self.queue.work(
            queue=queue or self.config.default_queue,
            concurrency=concurrency,
            burst=burst,
            with_scheduler=with_scheduler,
            worker_id=worker_id,
        )

    async def close(self) -> None:
        conn = getattr(self.queue, "connection", None)
        if conn is not None:
            await conn.aclose()
        self.queue = None
