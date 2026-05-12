from __future__ import annotations

import asyncio
import contextlib
import datetime
import enum
import hmac
import inspect
import json
import re
import signal
import uuid
from collections import Counter
from collections.abc import Callable
from typing import Any
from uuid import UUID

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.status import HTTP_401_UNAUTHORIZED

from yomai import env
from yomai._types import Request, read_json_body
from yomai.config import (
    AgentConfig,
    BudgetConfig,
    Config,
    DevConfig,
    LLMConfig,
    MemoryConfig,
    QueueConfig,
    RateLimitConfig,
    StreamingConfig,
)
from yomai.core.router import AgentRoute, WorkflowRoute
from yomai.devui.playground import get_playground_html
from yomai.exceptions import YomaiConfigError, YomaiRouteError
from yomai.hooks import HookHandler, HookRegistry
from yomai.jobs import (
    InMemoryCheckpointStore,
    InMemoryJobEventStore,
    InMemoryJobStore,
    JobRecord,
    RedisCheckpointStore,
    RedisJobEventStore,
    RedisJobStore,
)
from yomai.limits import InMemoryRateLimiter, RedisRateLimiter
from yomai.llm import LLMProvider
from yomai.llm.anthropic import AnthropicProvider
from yomai.llm.openai import OpenAIProvider
from yomai.memory import DictMemory, MemoryBackend, RedisMemory, SqliteMemory
from yomai.middleware.errors import ErrorMiddleware
from yomai.middleware.logging import LoggingMiddleware
from yomai.openapi.schema import build_openapi
from yomai.queue.base import QueuedWorkflow
from yomai.streaming.sse import format_sse_with_id
from yomai.tools.registry import ToolFunction
from yomai.workflow.events import sse_result
from yomai.workflow.runner import WorkflowRunner


class Depends:
    """Dependency injection for route-level auth, rate-limiting, etc."""

    def __init__(
        self,
        callable: Callable[..., Any],
        *,
        use_cache: bool = True,
    ) -> None:
        self.callable = callable
        self.use_cache = use_cache
        self._cache: Any = ...  # sentinel

    def __repr__(self) -> str:
        return f"Depends({self.callable.__name__})"


class RouteGroup:
    """Route group for grouping agents/workflows under a prefix with shared config."""

    def __init__(
        self,
        prefix: str = "",
        *,
        tags: list[str] | None = None,
        middleware: list[tuple[type[Any], dict[str, Any]]] | None = None,
        cors: dict[str, Any] | None = None,
        deprecated: bool = False,
    ) -> None:
        self.prefix = prefix.rstrip("/")
        self.tags = tags or []
        self.middleware = middleware or []
        self.cors = cors
        self.deprecated = deprecated
        self._agents: list[tuple[Callable[..., Any], dict[str, Any]]] = []
        self._workflows: list[tuple[Callable[..., Any], dict[str, Any]]] = []
        self._gets: list[tuple[Callable[..., Any], dict[str, Any]]] = []

    def get(
        self,
        path: str,
        *,
        api_key: str | None = None,
        tags: list[str] | None = None,
        summary: str | None = None,
        description: str | None = None,
        deprecated: bool | None = None,
        cors: dict[str, Any] | None = None,
        dependencies: list[Depends] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        full_path = f"{self.prefix}{path}" if (self.prefix and path) else path
        opts = {
            "_full_path": full_path,
            "api_key": api_key,
            "tags": (tags or []) + self.tags,
            "summary": summary,
            "description": description,
            "deprecated": deprecated if deprecated is not None else self.deprecated,
            "cors": cors if cors is not None else self.cors,
            "dependencies": dependencies or [],
        }

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            caller_frame: Any = inspect.currentframe()
            if caller_frame is not None and caller_frame.f_back is not None:
                fn._yomai_type_locals = dict(caller_frame.f_back.f_locals)
            self._gets.append((fn, opts))
            return fn

        return decorator

    def agent(
        self,
        path: str,
        tools: list[ToolFunction] | None = None,
        *,
        system: str = "",
        api_key: str | None = None,
        tags: list[str] | None = None,
        summary: str | None = None,
        description: str | None = None,
        deprecated: bool | None = None,
        cors: dict[str, Any] | None = None,
        dependencies: list[Depends] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        # Determine the full path for this route.
        # For RouteGroup decorators: path is relative (e.g. "/chat"), prefix is applied here.
        # For Yomai direct calls: path is the full path, prefix is "".
        full_path = f"{self.prefix}{path}" if (self.prefix and path) else path
        opts = {
            "_full_path": full_path,
            "tools": tools,
            "system": system,
            "api_key": api_key,
            "tags": (tags or []) + self.tags,
            "summary": summary,
            "description": description,
            "deprecated": deprecated if deprecated is not None else self.deprecated,
            "cors": cors if cors is not None else self.cors,
            "dependencies": dependencies or [],
        }

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            caller_frame: Any = inspect.currentframe()
            if caller_frame is not None and caller_frame.f_back is not None:
                fn._yomai_type_locals = dict(caller_frame.f_back.f_locals)
            self._agents.append((fn, opts))
            return fn

        return decorator

    def workflow(
        self,
        path: str,
        *,
        mode: str = "stream",
        api_key: str | None = None,
        tags: list[str] | None = None,
        summary: str | None = None,
        description: str | None = None,
        deprecated: bool | None = None,
        cors: dict[str, Any] | None = None,
        dependencies: list[Depends] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        full_path = f"{self.prefix}{path}" if (self.prefix and path) else path
        opts = {
            "_full_path": full_path,
            "mode": mode,
            "api_key": api_key,
            "tags": (tags or []) + self.tags,
            "summary": summary,
            "description": description,
            "deprecated": deprecated if deprecated is not None else self.deprecated,
            "cors": cors if cors is not None else self.cors,
            "dependencies": dependencies or [],
        }

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            caller_frame: Any = inspect.currentframe()
            if caller_frame is not None and caller_frame.f_back is not None:
                fn._yomai_type_locals = dict(caller_frame.f_back.f_locals)
            self._workflows.append((fn, opts))
            return fn

        return decorator


class Yomai:
    def __init__(
        self,
        llm: LLMConfig | None = None,
        memory: MemoryConfig | None = None,
        agent: AgentConfig | None = None,
        streaming: StreamingConfig | None = None,
        queue: QueueConfig | None = None,
        rate_limits: RateLimitConfig | None = None,
        budgets: BudgetConfig | None = None,
        dev: DevConfig | None = None,
    ) -> None:
        self.config = Config(
            llm=llm or LLMConfig(),
            memory=memory or MemoryConfig(),
            agent=agent or AgentConfig(),
            streaming=streaming or StreamingConfig(),
            queue=queue or QueueConfig(),
            rate_limits=rate_limits or RateLimitConfig(),
            budgets=budgets or BudgetConfig(),
            dev=dev or DevConfig(),
        )
        self.memory: MemoryBackend = self._build_memory(self.config.memory)
        self.jobs = self._build_job_store()
        self.job_events = self._build_job_event_store()
        self.checkpoints = self._build_checkpoint_store()
        self.hooks = HookRegistry()
        self.rate_limiter = self._build_rate_limiter()
        self._metrics_counters: Counter[str] = Counter()
        self._metrics_lock = asyncio.Lock()
        self._active_lock = asyncio.Lock()
        self._queue_backend: Any | None = None
        self._workflow_handlers: dict[str, Callable[..., Any]] = {}
        self._active_connections = 0
        self._draining = False
        self._routes_meta: list[dict[str, Any]] = []
        self._paths: set[str] = set()
        self._route_groups: list[RouteGroup] = []
        self._starlette = Starlette(
            routes=[
                Route("/dev", self._playground, methods=["GET"]),
                Route("/__yomai__", self._playground, methods=["GET"]),
                Route("/__yomai__/", self._playground, methods=["GET"]),
                Route("/__yomai__/health", self._health, methods=["GET"]),
                Route("/__yomai__/routes", self._routes, methods=["GET"]),
                Route(
                    "/__yomai__/openapi.json",
                    self._openapi,
                    methods=["GET"],
                ),
                Route("/__yomai__/jobs/{job_id}", self._job_status, methods=["GET"]),
                Route("/__yomai__/jobs/{job_id}/stream", self._job_stream, methods=["GET"]),
                Route("/__yomai__/jobs/{job_id}/cancel", self._job_cancel, methods=["POST"]),
                Route("/__yomai__/metrics", self._metrics, methods=["GET"]),
            ]
        )
        self._starlette.add_middleware(LoggingMiddleware, enabled=self.config.dev.log_usage)
        self._starlette.add_middleware(ErrorMiddleware)
        self._setup_signal_handlers()
        self._cors_config: dict[str, Any] = {}

    def _build_rate_limiter(self) -> InMemoryRateLimiter | RedisRateLimiter:
        if self.config.queue.backend == "swiftq" and self.config.queue.url:
            return RedisRateLimiter(self.config.queue.url, prefix=f"{self.config.queue.prefix}:limits")
        return InMemoryRateLimiter()

    def _build_checkpoint_store(self) -> InMemoryCheckpointStore | RedisCheckpointStore:
        if self.config.queue.backend == "swiftq" and self.config.queue.url:
            return RedisCheckpointStore(
                self.config.queue.url,
                prefix=self.config.queue.prefix,
                ttl_secs=self.config.queue.job_ttl_secs,
            )
        return InMemoryCheckpointStore()

    def _build_job_store(self) -> InMemoryJobStore | RedisJobStore:
        if self.config.queue.backend == "swiftq" and self.config.queue.url:
            return RedisJobStore(
                self.config.queue.url,
                prefix=self.config.queue.prefix,
                ttl_secs=self.config.queue.job_ttl_secs,
            )
        return InMemoryJobStore()

    def _build_job_event_store(self) -> InMemoryJobEventStore | RedisJobEventStore:
        if self.config.queue.backend == "swiftq" and self.config.queue.url:
            return RedisJobEventStore(
                self.config.queue.url,
                prefix=self.config.queue.prefix,
                ttl_secs=self.config.queue.event_ttl_secs,
            )
        return InMemoryJobEventStore()

    def _build_memory(self, cfg: MemoryConfig) -> MemoryBackend:
        if cfg.backend == "dict":
            return DictMemory(max_messages=cfg.max_messages, ttl_hours=cfg.ttl_hours)
        if cfg.backend == "sqlite":
            return SqliteMemory(db_path=cfg.db_path, max_messages=cfg.max_messages, ttl_hours=cfg.ttl_hours)
        if cfg.backend == "redis":
            return RedisMemory(
                url=cfg.url or "redis://localhost:6379/0",
                max_messages=cfg.max_messages,
                ttl_hours=cfg.ttl_hours,
                prefix=cfg.prefix,
            )
        raise YomaiConfigError(f"Unknown memory backend: {cfg.backend!r}")

    async def _playground(self, request: Request) -> Response:
        if env.YOMAI_ENV == "production" or not self.config.dev.ui:
            return Response(status_code=404)
        return HTMLResponse(get_playground_html(self._routes_meta))

    async def _health(self, request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": "0.1.0"})

    async def _routes(self, request: Request) -> JSONResponse:
        auth_error = self._metadata_auth_error(request)
        if auth_error is not None:
            return auth_error
        return JSONResponse(self._routes_meta)

    async def _openapi(self, request: Request) -> JSONResponse:
        auth_error = self._metadata_auth_error(request)
        if auth_error is not None:
            return auth_error
        title = env.YOMAI_APP_TITLE
        api_key = self.config.dev.api_key
        schema = build_openapi(self._routes_meta, title=title, api_key=api_key)
        return JSONResponse(schema)

    async def _job_status(self, request: Request) -> JSONResponse:
        auth_error = self._metadata_auth_error(request)
        if auth_error is not None:
            return auth_error
        job_id = request.path_params["job_id"]
        job = await self.jobs.get(job_id)
        if job is None:
            return JSONResponse({"error": "Job not found"}, status_code=404)
        return JSONResponse(job.to_dict())

    async def _metrics(self, request: Request) -> JSONResponse:
        auth_error = self._metadata_auth_error(request)
        if auth_error is not None:
            return auth_error
        jobs = list(await self.jobs.list())
        by_status = Counter(job.status for job in jobs)
        metrics = await self._get_metrics_snapshot()
        active = await self._get_active_connections()
        return JSONResponse(
            {
                "active_connections": active,
                "jobs_total": len(jobs),
                "jobs_queued": by_status.get("queued", 0),
                "jobs_running": by_status.get("running", 0),
                "jobs_retrying": by_status.get("retrying", 0),
                "jobs_succeeded": by_status.get("succeeded", 0),
                "jobs_failed": by_status.get("failed", 0),
                "jobs_cancelled": by_status.get("cancelled", 0),
                "jobs_expired": by_status.get("expired", 0),
                "requests_total": metrics.get("requests_total", 0),
                "workflow_jobs_total": metrics.get("workflow_jobs_total", 0),
                "errors_total": metrics.get("errors_total", 0),
            }
        )

    async def _job_cancel(self, request: Request) -> JSONResponse:
        auth_error = self._metadata_auth_error(request)
        if auth_error is not None:
            return auth_error
        job_id = request.path_params["job_id"]
        job = await self.jobs.update_status(job_id, "cancelled", error="Job cancelled")
        if job is None:
            return JSONResponse({"error": "Job not found"}, status_code=404)
        await self._incr_metric("errors_total")
        await self.job_events.append(
            job_id,
            "error",
            {"type": "error", "code": "cancelled", "message": "Job cancelled"},
        )
        await self.hooks.emit("job.cancelled", job_id=job_id, route=job.route)
        await self.job_events.append(job_id, "done", {"type": "done"})
        return JSONResponse(job.to_dict())

    async def _job_stream(self, request: Request) -> StreamingResponse | JSONResponse:
        auth_error = self._metadata_auth_error(request)
        if auth_error is not None:
            return auth_error
        job_id = request.path_params["job_id"]
        if await self.jobs.get(job_id) is None:
            return JSONResponse({"error": "Job not found"}, status_code=404)
        last_event_id = request.headers.get("Last-Event-ID")
        after_id: str | None = last_event_id if last_event_id else None

        async def stream():
            async for event in self.job_events.subscribe(
                job_id,
                after_id,
                heartbeat_secs=self.config.streaming.heartbeat_secs,
            ):
                if await request.is_disconnected():
                    break
                if event is None:
                    yield ": ping\n\n"
                    continue
                yield format_sse_with_id(event.id, event.event, event.data)
                if event.data.get("type") == "done":
                    break

        return StreamingResponse(stream(), media_type="text/event-stream")

    def _get_queue_backend(self) -> Any | None:
        if self.config.queue.backend == "none":
            return None
        if self.config.queue.backend == "inline":
            return None
        if self.config.queue.backend == "swiftq":
            if self._queue_backend is None:
                from yomai.queue.swiftq import SwiftQQueueBackend

                self._queue_backend = SwiftQQueueBackend(self, self.config.queue)
            return self._queue_backend
        raise YomaiConfigError(f"Unknown queue backend: {self.config.queue.backend!r}")

    async def create_job(self, job_id: str, route: str) -> JobRecord:
        record = JobRecord(
            id=job_id,
            route=route,
            status_url=f"/__yomai__/jobs/{job_id}",
            stream_url=f"/__yomai__/jobs/{job_id}/stream",
        )
        return await self.jobs.create(record)

    async def _append_job_sse(self, job_id: str, sse: str) -> None:
        event_type = "message"
        data: dict[str, Any] = {"type": "message", "raw": sse}
        for line in sse.splitlines():
            if line.startswith("event: "):
                event_type = line[len("event: ") :]
            elif line.startswith("data: "):
                try:
                    parsed = json.loads(line[len("data: ") :])
                    if isinstance(parsed, dict):
                        data = parsed
                except json.JSONDecodeError:
                    data = {"type": event_type, "content": line[len("data: ") :]}
        await self.job_events.append(job_id, event_type, data)

    async def _run_inline_workflow_job(
        self,
        *,
        job_id: str,
        path: str,
        handler: Callable[..., Any],
        body: dict[str, Any],
        session_id: str,
        path_kwargs: dict[str, Any],
    ) -> None:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        current = await self.jobs.get(job_id)
        if current is not None and current.status == "cancelled":
            return
        await self.jobs.update_status(job_id, "running")
        await self.hooks.emit("job.started", job_id=job_id, route=path)
        await self.hooks.emit("workflow.start", job_id=job_id, route=path)

        async def consume_events() -> None:
            while True:
                item = await queue.get()
                if item is None:
                    break
                await self._append_job_sse(job_id, item)

        consumer = asyncio.create_task(consume_events())
        try:
            runner = WorkflowRunner(queue, session_id, self.memory, self, job_id=job_id)
            from yomai.core.router import WorkflowRoute

            route = WorkflowRoute(path, handler, self, self.memory)
            kwargs = route._build_kwargs(body, runner, path_kwargs, session_id=session_id)
            result = handler(**kwargs)
            if inspect.isawaitable(result):
                result = await result
            await queue.put(sse_result(result if result is not None else ""))
            await queue.put(self._done_sse())
            await self.jobs.update_status(job_id, "succeeded", result=result)
            await self.hooks.emit("workflow.done", job_id=job_id, route=path, result=result)
            await self.hooks.emit("job.succeeded", job_id=job_id, route=path, result=result)
            released = self.rate_limiter.release_concurrent(session_id)
            if inspect.isawaitable(released):
                await released
        except asyncio.CancelledError:
            from yomai.streaming.sse import sse_error

            await self._incr_metric("errors_total")
            await self.jobs.update_status(job_id, "cancelled", error="Job cancelled")
            await queue.put(sse_error("Job cancelled", "cancelled"))
            await self.hooks.emit("job.cancelled", job_id=job_id, route=path)
            released = self.rate_limiter.release_concurrent(session_id)
            if inspect.isawaitable(released):
                await released
            await queue.put(self._done_sse())
        except Exception as exc:  # noqa: BLE001 - jobs must persist failures
            message_out = "Internal server error" if env.YOMAI_ENV == "production" else str(exc)
            from yomai.streaming.sse import sse_error

            await queue.put(sse_error(message_out, exc.__class__.__name__))
            await queue.put(self._done_sse())
            await self._incr_metric("errors_total")
            await self.jobs.update_status(job_id, "failed", error=message_out)
            await self.hooks.emit("workflow.failed", job_id=job_id, route=path, error=message_out)
            await self.hooks.emit("job.failed", job_id=job_id, route=path, error=message_out)
            await self.hooks.emit("error", job_id=job_id, route=path, error=message_out)
            released = self.rate_limiter.release_concurrent(session_id)
            if inspect.isawaitable(released):
                await released
        finally:
            with contextlib.suppress(Exception):
                await queue.put(None)
            try:
                await asyncio.wait_for(consumer, timeout=10.0)
            except TimeoutError:
                consumer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await consumer

    def _done_sse(self) -> str:
        from yomai.streaming.sse import sse_done

        return sse_done()

    def _metadata_auth_error(self, request: Request) -> JSONResponse | None:
        if env.YOMAI_ENV != "production":
            return None
        if not self.config.dev.api_key:
            return JSONResponse({"error": "Metadata endpoint disabled"}, status_code=HTTP_401_UNAUTHORIZED)
        if not hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {self.config.dev.api_key}"):
            return JSONResponse({"error": "Invalid or missing API key"}, status_code=HTTP_401_UNAUTHORIZED)
        return None

    def on(self, name: str) -> Callable[[HookHandler], HookHandler]:
        """Register a lifecycle hook.

        Example:
            @app.on("job.succeeded")
            async def on_done(event): ...
        """

        def decorator(fn: HookHandler) -> HookHandler:
            self.hooks.on(name, fn)
            return fn

        return decorator

    def include_router(self, group: RouteGroup) -> None:
        """Include a route group, registering all agents and workflows."""
        if group.prefix and not group.prefix.startswith("/"):
            raise YomaiRouteError("Route group prefix must start with '/' or be empty.")
        self._route_groups.append(group)

        for fn, opts in group._agents:
            self.agent(
                opts["_full_path"],
                tools=opts["tools"],
                system=opts["system"],
                api_key=opts["api_key"],
                tags=opts["tags"],
                summary=opts.get("summary"),
                description=opts.get("description"),
                deprecated=opts["deprecated"],
                cors=opts.get("cors"),
                dependencies=opts.get("dependencies", []),
            )(fn)

        for fn, opts in group._workflows:
            self.workflow(
                opts["_full_path"],
                mode=opts["mode"],
                api_key=opts["api_key"],
                tags=opts["tags"],
                summary=opts.get("summary"),
                description=opts.get("description"),
                deprecated=opts["deprecated"],
                cors=opts.get("cors"),
                dependencies=opts.get("dependencies", []),
            )(fn)

        for fn, opts in group._gets:
            self.get(
                opts["_full_path"],
                api_key=opts["api_key"],
                tags=opts["tags"],
                summary=opts.get("summary"),
                description=opts.get("description"),
                deprecated=opts["deprecated"],
                cors=opts.get("cors"),
                dependencies=opts.get("dependencies", []),
            )(fn)

    def agent(
        self,
        path: str,
        tools: list[ToolFunction] | None = None,
        *,
        system: str = "",
        api_key: str | None = None,
        tags: list[str] | None = None,
        summary: str | None = None,
        description: str | None = None,
        deprecated: bool = False,
        cors: dict[str, Any] | None = None,
        dependencies: list[Depends] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        self._validate_new_path(path, method="POST")  # POST for agent
        path_params = self._extract_path_params(path)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            caller_frame: Any = inspect.currentframe()
            if caller_frame is not None and caller_frame.f_back is not None:
                fn._yomai_type_locals = dict(caller_frame.f_back.f_locals)

            route = AgentRoute(
                path,
                fn,
                tools or [],
                self.config.llm,
                self.config.agent,
                self.memory,
                self._build_provider,
                self.config.streaming.heartbeat_secs,
                self._stream_started,
                self._stream_finished,
                self._accepting_connections,
                self.config.dev.log_usage,
                system,
                self.config.dev.api_key if api_key is None else api_key,
                path_params,
                cors or {},
                dependencies or [],
            )
            self._starlette.router.routes.append(Route(path, route.handle, methods=["POST"]))
            self._paths.add(path)
            tool_names = [getattr(t, "tool_name", getattr(t, "__name__", str(t))) for t in (tools or [])]
            tool_schemas = [t.schema for t in (tools or []) if isinstance(getattr(t, "schema", None), dict)]
            params = self._route_params(fn, injected={"session_id", "request"}, path_params=path_params)
            body_params = [p["name"] for p in params if p["name"] not in path_params]
            self._routes_meta.append(
                {
                    "path": path,
                    "type": "agent",
                    "tools": tool_names,
                    "tool_schemas": tool_schemas,
                    "params": params,
                    "body_params": body_params,
                    "path_params": list(path_params),
                    "injected_params": ["session_id"],
                    "system": system or None,
                    "tags": tags or ["agents"],
                    "summary": summary,
                    "description": description,
                    "deprecated": deprecated,
                    "cors": cors,
                }
            )
            fn._yomai_app = self
            fn._yomai_tools = tools or []
            fn._yomai_agent_config = self.config.agent
            return fn

        return decorator

    def workflow(
        self,
        path: str,
        *,
        mode: str = "stream",
        api_key: str | None = None,
        tags: list[str] | None = None,
        summary: str | None = None,
        description: str | None = None,
        deprecated: bool = False,
        cors: dict[str, Any] | None = None,
        dependencies: list[Depends] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        if mode not in {"stream", "async"}:
            raise YomaiRouteError(
                f"Unknown workflow mode {mode!r}.",
                hint="Valid options: 'stream', 'async'.",
                docs="https://yomai.dev/roadmap",
            )
        self._validate_new_path(path, method="POST")  # workflow
        path_params = self._extract_path_params(path)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            caller_frame: Any = inspect.currentframe()
            if caller_frame is not None and caller_frame.f_back is not None:
                fn._yomai_type_locals = dict(caller_frame.f_back.f_locals)

            self._workflow_handlers[path] = fn

            if mode == "async":
                required_api_key = self.config.dev.api_key if api_key is None else api_key

                async def handle_async_workflow(request: Request) -> JSONResponse:
                    if not self._accepting_connections():
                        return JSONResponse({"error": "Server is shutting down"}, status_code=503)
                    if required_api_key:
                        auth = request.headers.get("Authorization", "")
                        if not hmac.compare_digest(auth, f"Bearer {required_api_key}"):
                            return JSONResponse({"error": "Invalid or missing API key"}, status_code=401)
                    path_kwargs = {
                        param_name: request.path_params[param_name]
                        for param_name in path_params
                        if param_name in request.path_params
                    }
                    request._yomai_path_kwargs = path_kwargs
                    for dep in dependencies or []:
                        if hasattr(dep, "callable"):
                            result = dep.callable(request)
                            if inspect.isawaitable(result):
                                await result
                    try:
                        body: Any = await read_json_body(request)
                    except Exception:
                        return JSONResponse({"error": "Request body must be valid JSON"}, status_code=400)
                    if not isinstance(body, dict):
                        return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)

                    session_id = request.headers.get("X-Session-Id") or str(uuid.uuid4())
                    retry_after = self.rate_limiter.check_request(
                        session_id,
                        self.config.rate_limits.requests_per_minute,
                    )
                    if inspect.isawaitable(retry_after):
                        retry_after = await retry_after
                    if retry_after is not None:
                        await self._incr_metric("errors_total")
                        return JSONResponse(
                            {"error": "Rate limit exceeded", "code": "rate_limited", "retry_after": retry_after},
                            status_code=429,
                        )
                    acquired = self.rate_limiter.acquire_concurrent(
                        session_id,
                        self.config.rate_limits.max_concurrent_per_session,
                    )
                    if inspect.isawaitable(acquired):
                        acquired = await acquired
                    if not acquired:
                        await self._incr_metric("errors_total")
                        return JSONResponse(
                            {"error": "Too many concurrent requests", "code": "rate_limited"},
                            status_code=429,
                        )
                    job_id = f"job_{uuid.uuid4().hex}"
                    await self._incr_metric("requests_total")
                    await self._incr_metric("workflow_jobs_total")
                    job = await self.create_job(job_id, path)
                    await self.job_events.append(job_id, "job_queued", {"type": "job_queued", "job_id": job_id})
                    await self.hooks.emit("job.queued", job_id=job_id, route=path)
                    queue_backend = self._get_queue_backend()
                    if queue_backend is None:
                        asyncio.create_task(
                            self._run_inline_workflow_job(
                                job_id=job_id,
                                path=path,
                                handler=fn,
                                body=body,
                                session_id=session_id,
                                path_kwargs=path_kwargs,
                            )
                        )
                    else:
                        await queue_backend.enqueue_workflow(
                            QueuedWorkflow(
                                job_id=job_id,
                                route=path,
                                payload=body,
                                session_id=session_id,
                                metadata={"path_kwargs": path_kwargs},
                            )
                        )
                    headers = {"X-Session-Id": session_id}
                    return JSONResponse(
                        {
                            "job_id": job.id,
                            "status_url": job.status_url,
                            "stream_url": job.stream_url,
                        },
                        status_code=202,
                        headers=headers,
                    )

                self._starlette.router.routes.append(Route(path, handle_async_workflow, methods=["POST"]))
                self._paths.add(path)
                body_params = self._route_params(fn, injected={"runner", "request"}, path_params=path_params)
                self._routes_meta.append(
                    {
                        "path": path,
                        "type": "workflow",
                        "mode": "async",
                        "tools": [],
                        "params": body_params,
                        "body_params": [p["name"] for p in body_params],
                        "path_params": list(path_params),
                        "injected_params": ["runner"],
                        "tags": tags or ["workflows"],
                        "summary": summary,
                        "description": description,
                        "deprecated": deprecated,
                        "cors": cors,
                    }
                )
                fn._yomai_app = self
                fn._is_workflow = True
                return fn

            route = WorkflowRoute(
                path,
                fn,
                self,
                self.memory,
                self._stream_started,
                self._stream_finished,
                self._accepting_connections,
                self.config.dev.log_usage,
                self.config.dev.api_key if api_key is None else api_key,
                path_params,
                cors or {},
                dependencies or [],
            )
            self._starlette.router.routes.append(Route(path, route.handle, methods=["POST"]))
            self._paths.add(path)
            body_params = self._route_params(fn, injected={"runner", "request"}, path_params=path_params)
            self._routes_meta.append(
                {
                    "path": path,
                    "type": "workflow",
                    "tools": [],
                    "params": body_params,
                    "body_params": [p["name"] for p in body_params],
                    "path_params": list(path_params),
                    "injected_params": ["runner"],
                    "tags": tags or ["workflows"],
                    "summary": summary,
                    "description": description,
                    "deprecated": deprecated,
                    "cors": cors,
                }
            )
            fn._yomai_app = self
            fn._is_workflow = True
            return fn

        return decorator

    def get(
        self,
        path: str,
        *,
        api_key: str | None = None,
        tags: list[str] | None = None,
        summary: str | None = None,
        description: str | None = None,
        deprecated: bool = False,
        cors: dict[str, Any] | None = None,
        dependencies: list[Depends] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Non-streaming GET endpoint for reading data (e.g., session history)."""
        self._validate_new_path(path, method="GET")
        path_params = self._extract_path_params(path)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            caller_frame: Any = inspect.currentframe()
            if caller_frame is not None and caller_frame.f_back is not None:
                fn._yomai_type_locals = dict(caller_frame.f_back.f_locals)
            from yomai.core.router import GetRoute

            route = GetRoute(
                path,
                fn,
                self.memory,
                self._stream_started,
                self._stream_finished,
                self._accepting_connections,
                self.config.dev.log_usage,
                self.config.dev.api_key if api_key is None else api_key,
                path_params,
                cors or {},
                dependencies or [],
            )
            self._starlette.router.routes.append(Route(path, route.handle, methods=["GET"]))
            self._paths.add(path)
            params = self._route_params(fn, injected={"request"}, path_params=path_params)
            self._routes_meta.append(
                {
                    "path": path,
                    "type": "get",
                    "params": params,
                    "path_params": list(path_params),
                    "injected_params": [],
                    "tags": tags or ["get"],
                    "summary": summary,
                    "description": description,
                    "deprecated": deprecated,
                    "cors": cors,
                }
            )
            return fn

        return decorator

    def delete(
        self,
        path: str,
        *,
        api_key: str | None = None,
        tags: list[str] | None = None,
        summary: str | None = None,
        description: str | None = None,
        deprecated: bool = False,
        cors: dict[str, Any] | None = None,
        dependencies: list[Depends] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Non-streaming DELETE endpoint (e.g., clear session)."""
        self._validate_new_path(path, method="DELETE")
        path_params = self._extract_path_params(path)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            caller_frame: Any = inspect.currentframe()
            if caller_frame is not None and caller_frame.f_back is not None:
                fn._yomai_type_locals = dict(caller_frame.f_back.f_locals)
            from yomai.core.router import DeleteRoute

            route = DeleteRoute(
                path,
                fn,
                self.memory,
                self._stream_started,
                self._stream_finished,
                self._accepting_connections,
                self.config.dev.log_usage,
                self.config.dev.api_key if api_key is None else api_key,
                path_params,
                cors or {},
                dependencies or [],
            )
            self._starlette.router.routes.append(Route(path, route.handle, methods=["DELETE"]))
            self._paths.add(path)
            params = self._route_params(fn, injected={"request"}, path_params=path_params)
            self._routes_meta.append(
                {
                    "path": path,
                    "type": "delete",
                    "params": params,
                    "path_params": list(path_params),
                    "injected_params": [],
                    "tags": tags or ["delete"],
                    "summary": summary,
                    "description": description,
                    "deprecated": deprecated,
                    "cors": cors,
                }
            )
            return fn

        return decorator

    def head(
        self,
        path: str,
        *,
        api_key: str | None = None,
        tags: list[str] | None = None,
        summary: str | None = None,
        description: str | None = None,
        deprecated: bool = False,
        cors: dict[str, Any] | None = None,
        dependencies: list[Depends] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """HEAD endpoint (e.g., check session exists)."""
        self._validate_new_path(path, method="HEAD")
        path_params = self._extract_path_params(path)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            caller_frame: Any = inspect.currentframe()
            if caller_frame is not None and caller_frame.f_back is not None:
                fn._yomai_type_locals = dict(caller_frame.f_back.f_locals)
            from yomai.core.router import HeadRoute

            route = HeadRoute(
                path=path,
                handler=fn,
                on_stream_start=self._stream_started,
                on_stream_end=self._stream_finished,
                should_accept=self._accepting_connections,
                path_params=path_params,
                cors=cors or {},
                dependencies=dependencies or [],
            )
            self._starlette.router.routes.append(Route(path, route.handle, methods=["HEAD"]))
            self._paths.add(path)
            return fn

        return decorator

    def options(
        self,
        path: str,
        *,
        api_key: str | None = None,
        tags: list[str] | None = None,
        summary: str | None = None,
        description: str | None = None,
        deprecated: bool = False,
        cors: dict[str, Any] | None = None,
        dependencies: list[Depends] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """OPTIONS endpoint for CORS preflight."""
        self._validate_new_path(path, method="OPTIONS")
        path_params = self._extract_path_params(path)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            caller_frame: Any = inspect.currentframe()
            if caller_frame is not None and caller_frame.f_back is not None:
                fn._yomai_type_locals = dict(caller_frame.f_back.f_locals)
            from yomai.core.router import OptionsRoute

            route = OptionsRoute(
                path=path,
                handler=fn,
                on_stream_start=self._stream_started,
                on_stream_end=self._stream_finished,
                should_accept=self._accepting_connections,
                path_params=path_params,
                cors=cors or {},
                dependencies=dependencies or [],
            )
            self._starlette.router.routes.append(Route(path, route.handle, methods=["OPTIONS"]))
            self._paths.add(path)
            return fn

        return decorator

    def put(
        self,
        path: str,
        *,
        api_key: str | None = None,
        tags: list[str] | None = None,
        summary: str | None = None,
        description: str | None = None,
        deprecated: bool = False,
        cors: dict[str, Any] | None = None,
        dependencies: list[Depends] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Non-streaming PUT endpoint for full replacement."""
        self._validate_new_path(path, method="PUT")
        path_params = self._extract_path_params(path)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            caller_frame: Any = inspect.currentframe()
            if caller_frame is not None and caller_frame.f_back is not None:
                fn._yomai_type_locals = dict(caller_frame.f_back.f_locals)
            from yomai.core.router import PutRoute

            route = PutRoute(
                path,
                fn,
                self.memory,
                self._stream_started,
                self._stream_finished,
                self._accepting_connections,
                self.config.dev.log_usage,
                self.config.dev.api_key if api_key is None else api_key,
                path_params,
                cors or {},
                dependencies or [],
            )
            self._starlette.router.routes.append(Route(path, route.handle, methods=["PUT"]))
            self._paths.add(path)
            params = self._route_params(fn, injected={"request"}, path_params=path_params)
            self._routes_meta.append(
                {
                    "path": path,
                    "type": "put",
                    "params": params,
                    "path_params": list(path_params),
                    "injected_params": [],
                    "tags": tags or ["put"],
                    "summary": summary,
                    "description": description,
                    "deprecated": deprecated,
                    "cors": cors,
                }
            )
            return fn

        return decorator

    def patch(
        self,
        path: str,
        *,
        api_key: str | None = None,
        tags: list[str] | None = None,
        summary: str | None = None,
        description: str | None = None,
        deprecated: bool = False,
        cors: dict[str, Any] | None = None,
        dependencies: list[Depends] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Non-streaming PATCH endpoint for partial updates."""
        self._validate_new_path(path, method="PATCH")
        path_params = self._extract_path_params(path)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            caller_frame: Any = inspect.currentframe()
            if caller_frame is not None and caller_frame.f_back is not None:
                fn._yomai_type_locals = dict(caller_frame.f_back.f_locals)
            from yomai.core.router import PatchRoute

            route = PatchRoute(
                path,
                fn,
                self.memory,
                self._stream_started,
                self._stream_finished,
                self._accepting_connections,
                self.config.dev.log_usage,
                self.config.dev.api_key if api_key is None else api_key,
                path_params,
                cors or {},
                dependencies or [],
            )
            self._starlette.router.routes.append(Route(path, route.handle, methods=["PATCH"]))
            self._paths.add(path)
            params = self._route_params(fn, injected={"request"}, path_params=path_params)
            self._routes_meta.append(
                {
                    "path": path,
                    "type": "patch",
                    "params": params,
                    "path_params": list(path_params),
                    "injected_params": [],
                    "tags": tags or ["patch"],
                    "summary": summary,
                    "description": description,
                    "deprecated": deprecated,
                    "cors": cors,
                }
            )
            return fn

        return decorator

    def _extract_path_params(self, path: str) -> set[str]:
        """Extract parameter names from a path like /chat/{session_id}."""
        return set(re.findall(r"\{(\w+)\}", path))

    def _route_params(
        self,
        fn: Callable[..., Any],
        *,
        injected: set[str],
        path_params: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        params: list[dict[str, Any]] = []
        signature = inspect.signature(fn)
        for name, param in signature.parameters.items():
            if name in injected:
                continue
            annotation = param.annotation
            type_name = self._schema_type(annotation)
            is_path = path_params and name in path_params
            params.append(
                {
                    "name": name,
                    "type": type_name,
                    "required": param.default is inspect.Signature.empty and not is_path,
                    "default": None if param.default is inspect.Signature.empty else param.default,
                    "in": "path" if is_path else "body",
                }
            )
        return params

    def _schema_type(self, annotation: Any) -> str:
        if annotation is inspect.Signature.empty:
            return "string"
        origin = getattr(annotation, "__origin__", None)
        if origin is not None:
            annotation = origin
        # Handle uuid, datetime, enum
        if annotation is UUID:
            return "string"  # UUID is a string in OpenAPI
        if inspect.isclass(annotation) and issubclass(annotation, datetime.datetime):
            return "string"  # ISO 8601
        if inspect.isclass(annotation) and issubclass(annotation, enum.Enum):
            return "string"
        # Handle typing.Literal
        literals = getattr(annotation, "__values__", None)
        if literals is not None:
            return "string"
        return {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object",
        }.get(annotation, "string")

    def _build_provider(self) -> LLMProvider:
        if self.config.llm.provider == "anthropic":
            return AnthropicProvider(self.config.llm)
        if self.config.llm.provider == "openai":
            return OpenAIProvider(self.config.llm)
        raise YomaiConfigError(f"Unknown provider: {self.config.llm.provider!r}")

    def _accepting_connections(self) -> bool:
        return not self._draining

    def _stream_started(self) -> None:
        self._active_connections += 1

    def _stream_finished(self) -> None:
        self._active_connections = max(0, self._active_connections - 1)

    async def _get_active_connections(self) -> int:
        async with self._active_lock:
            return self._active_connections

    async def _incr_metric(self, key: str, amount: int = 1) -> None:
        async with self._metrics_lock:
            self._metrics_counters[key] += amount

    async def _get_metrics_snapshot(self) -> dict[str, int]:
        async with self._metrics_lock:
            return dict(self._metrics_counters)

    def _setup_signal_handlers(self) -> None:
        if env.YOMAI_HANDLE_SIGTERM != "1":
            return
        try:
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGTERM, self._handle_sigterm)
        except (RuntimeError, NotImplementedError):
            return

    def _handle_sigterm(self) -> None:
        self._draining = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._drain_active_connections())

    async def _drain_active_connections(self) -> None:
        deadline = asyncio.get_running_loop().time() + 30
        while True:
            async with self._active_lock:
                if self._active_connections <= 0:
                    break
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(0.1)

    def _validate_new_path(self, path: str, method: str | None = None) -> None:
        """Validate that a route path+method is available.

        Allows multiple HTTP methods on the same path.
        """
        if not path.startswith("/"):
            raise YomaiRouteError("Route path must start with '/'.")
        # Check for existing route with same path+method
        for r in self._starlette.router.routes:
            route_path = getattr(r, 'path', '')
            if route_path == path:
                r_methods = getattr(r, 'methods', set())
                if method and method in r_methods:
                    raise YomaiRouteError(f"Route already registered: {path} ({method})")
                # Same path with different method is OK
        # Path is added to _paths for uniqueness tracking
        self._paths.add(path)

    def add_middleware(self, middleware_class: type[Any], **kwargs: Any) -> None:
        self._starlette.add_middleware(middleware_class, **kwargs)

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        await self._starlette(scope, receive, send)
