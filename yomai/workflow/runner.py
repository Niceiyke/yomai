from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from yomai.core.agent import AgentLoop
from yomai.jobs.checkpoints import StepCheckpoint
from yomai.memory import MemoryBackend
from yomai.streaming.sse import sse_error
from yomai.tools.registry import ToolFunction
from yomai.workflow.events import sse_step_done, sse_step_start

if TYPE_CHECKING:
    from yomai.core.app import Yomai


class WorkflowRunner:
    def __init__(
        self,
        sse_queue: asyncio.Queue[str | None],
        session_id: str,
        memory: MemoryBackend,
        app: Yomai,
        job_id: str | None = None,
    ):
        self.sse_queue = sse_queue
        self.session_id = session_id
        self.memory = memory
        self.app = app
        self.job_id = job_id
        self._step_index = 0

    async def cancelled(self) -> bool:
        if self.job_id is None:
            return False
        job = await self.app.jobs.get(self.job_id)
        return bool(job is not None and job.status == "cancelled")

    async def raise_if_cancelled(self) -> None:
        if await self.cancelled():
            raise asyncio.CancelledError("Workflow job cancelled")

    async def step(self, name: str, agent_fn: Callable[..., Any], input: Any) -> str:
        await self.raise_if_cancelled()
        self._step_index += 1
        index = self._step_index
        input_hash = self._input_hash(input)
        if self.job_id is not None:
            existing = await self.app.checkpoints.get(self.job_id, name, input_hash)
            if existing is not None and existing.status == "succeeded":
                await self.sse_queue.put(sse_step_start(name, index, None))
                await self.sse_queue.put(sse_step_done(name, 0))
                return str(existing.result or "")

        await self.sse_queue.put(sse_step_start(name, index, None))
        start = time.monotonic()

        tools = getattr(agent_fn, "_yomai_tools", [])
        if not isinstance(tools, list):
            tools = []
        typed_tools: list[ToolFunction] = tools

        history = await self.memory.load(self.session_id)
        agent_loop = AgentLoop(self.app._build_provider(), typed_tools, self.app.config.agent, self.app.config.llm)

        try:
            async for sse in agent_loop.run(str(input), history=history, system=""):
                await self.raise_if_cancelled()
                await self.sse_queue.put(sse)
        except Exception as exc:
            await self.sse_queue.put(sse_error(str(exc), exc.__class__.__name__))
            raise

        await self.memory.save(self.session_id, str(input), agent_loop.last_reply)
        duration_ms = int((time.monotonic() - start) * 1000)
        if self.job_id is not None:
            await self.app.checkpoints.save(
                StepCheckpoint(
                    job_id=self.job_id,
                    step=name,
                    input_hash=input_hash,
                    result=agent_loop.last_reply,
                    duration_ms=duration_ms,
                )
            )
        await self.sse_queue.put(sse_step_done(name, duration_ms))
        return agent_loop.last_reply

    def _input_hash(self, input: Any) -> str:
        try:
            payload = json.dumps(input, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            payload = str(input)
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def parallel(self, steps: list[Awaitable[Any]]) -> list[Any]:
        return list(await asyncio.gather(*steps))
