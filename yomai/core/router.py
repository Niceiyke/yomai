from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from yomai.config import AgentConfig, LLMConfig
from yomai.core.agent import AgentLoop
from yomai.llm import LLMProvider
from yomai.memory import MemoryBackend
from yomai.middleware.logging import StreamLog
from yomai.streaming.sse import heartbeat, sse_done, sse_error
from yomai.tools.registry import ToolFunction
from yomai.workflow.events import sse_result
from yomai.workflow.runner import WorkflowRunner


SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}
ProviderFactory = Callable[[], LLMProvider]
LifecycleCallback = Callable[[], None]
AcceptCallback = Callable[[], bool]


class AgentRoute:
    def __init__(
        self,
        path: str,
        handler: Callable[..., Any],
        tools: list[ToolFunction] | None,
        llm_config: LLMConfig,
        agent_config: AgentConfig,
        memory: MemoryBackend,
        provider_factory: ProviderFactory,
        heartbeat_secs: int = 15,
        on_stream_start: LifecycleCallback | None = None,
        on_stream_end: LifecycleCallback | None = None,
        should_accept: AcceptCallback | None = None,
        log_usage: bool = True,
    ) -> None:
        self.path = path
        self.handler = handler
        self.tools = tools or []
        self.llm_config = llm_config
        self.agent_config = agent_config
        self.memory = memory
        self.provider_factory = provider_factory
        self.heartbeat_secs = heartbeat_secs
        self.on_stream_start = on_stream_start
        self.on_stream_end = on_stream_end
        self.should_accept = should_accept
        self.log_usage = log_usage

    async def handle(self, request: Request) -> StreamingResponse | JSONResponse:
        if self.should_accept is not None and not self.should_accept():
            return JSONResponse({"error": "Server is shutting down"}, status_code=503)
        try:
            body: Any = await request.json()
        except Exception:
            return JSONResponse({"error": "Request body must be valid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        message = body.get("message")
        if not isinstance(message, str) or not message:
            return JSONResponse({"error": "Missing required string field: message"}, status_code=400)

        session_id = request.headers.get("X-Session-Id") or str(uuid.uuid4())
        headers = {**SSE_HEADERS, "X-Session-Id": session_id}
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        stream_log = StreamLog(request.method, self.path, session_id, "agent") if self.log_usage else None

        async def put_sse(sse: str) -> None:
            if stream_log is not None:
                stream_log.observe_sse(sse)
            await queue.put(sse)

        async def run_agent() -> None:
            agent_loop: AgentLoop | None = None
            completed = False
            try:
                history = await self.memory.load(session_id)
                agent_loop = AgentLoop(self.provider_factory(), self.tools, self.agent_config, self.llm_config)
                async for sse in agent_loop.run(message, history=history, system=""):
                    await put_sse(sse)
                completed = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await put_sse(await sse_error(str(exc), exc.__class__.__name__))
            finally:
                if completed and agent_loop is not None:
                    await self.memory.save(session_id, message, agent_loop.last_reply)
                await queue.put(None)

        async def generate() -> AsyncIterator[str]:
            agent_task = asyncio.create_task(run_agent())
            heartbeat_task = asyncio.create_task(heartbeat(queue, self.heartbeat_secs))
            started_at = time.monotonic()
            if self.on_stream_start is not None:
                self.on_stream_start()
            try:
                while True:
                    if time.monotonic() - started_at > self.agent_config.timeout_secs:
                        agent_task.cancel()
                        timeout_sse = await sse_error("Agent request timed out", "timeout")
                        if stream_log is not None:
                            stream_log.observe_sse(timeout_sse)
                        yield timeout_sse
                        break
                    if await request.is_disconnected():
                        agent_task.cancel()
                        break
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=0.1)
                    except TimeoutError:
                        continue
                    if item is None:
                        break
                    yield item
            finally:
                heartbeat_task.cancel()
                if not agent_task.done():
                    agent_task.cancel()
                if self.on_stream_end is not None:
                    self.on_stream_end()
                if stream_log is not None:
                    stream_log.emit()

        return StreamingResponse(generate(), media_type="text/event-stream", headers=headers)


class WorkflowRoute:
    def __init__(
        self,
        path: str,
        handler: Callable[..., Any],
        app: Any,
        memory: MemoryBackend,
        on_stream_start: LifecycleCallback | None = None,
        on_stream_end: LifecycleCallback | None = None,
        should_accept: AcceptCallback | None = None,
        log_usage: bool = True,
    ) -> None:
        self.path = path
        self.handler = handler
        self.app = app
        self.memory = memory
        self.on_stream_start = on_stream_start
        self.on_stream_end = on_stream_end
        self.should_accept = should_accept
        self.log_usage = log_usage

    async def handle(self, request: Request) -> StreamingResponse | JSONResponse:
        if self.should_accept is not None and not self.should_accept():
            return JSONResponse({"error": "Server is shutting down"}, status_code=503)
        try:
            body: Any = await request.json()
        except Exception:
            return JSONResponse({"error": "Request body must be valid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)

        session_id = request.headers.get("X-Session-Id") or str(uuid.uuid4())
        headers = {**SSE_HEADERS, "X-Session-Id": session_id}
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        stream_log = StreamLog(request.method, self.path, session_id, "workflow") if self.log_usage else None

        async def put_sse(sse: str) -> None:
            if stream_log is not None:
                stream_log.observe_sse(sse)
            await queue.put(sse)

        async def run_workflow() -> None:
            try:
                runner = WorkflowRunner(queue, session_id, self.memory, self.app)
                kwargs = self._build_kwargs(body, runner)
                result = self.handler(**kwargs)
                if inspect.isawaitable(result):
                    result = await result
                await put_sse(sse_result(result if result is not None else ""))
                await put_sse(await sse_done())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await put_sse(await sse_error(str(exc), exc.__class__.__name__))
                await put_sse(await sse_done())
            finally:
                await queue.put(None)

        async def generate() -> AsyncIterator[str]:
            task = asyncio.create_task(run_workflow())
            heartbeat_task = asyncio.create_task(heartbeat(queue, self.app.config.streaming.heartbeat_secs))
            started_at = time.monotonic()
            if self.on_stream_start is not None:
                self.on_stream_start()
            try:
                while True:
                    if time.monotonic() - started_at > self.app.config.streaming.max_duration_secs:
                        task.cancel()
                        timeout_sse = await sse_error("Workflow request timed out", "timeout")
                        if stream_log is not None:
                            stream_log.observe_sse(timeout_sse)
                        yield timeout_sse
                        break
                    if await request.is_disconnected():
                        task.cancel()
                        break
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=0.1)
                    except TimeoutError:
                        continue
                    if item is None:
                        break
                    yield item
            finally:
                heartbeat_task.cancel()
                if not task.done():
                    task.cancel()
                if self.on_stream_end is not None:
                    self.on_stream_end()
                if stream_log is not None:
                    stream_log.emit()

        return StreamingResponse(generate(), media_type="text/event-stream", headers=headers)

    def _build_kwargs(self, body: dict[str, Any], runner: WorkflowRunner) -> dict[str, Any]:
        signature = inspect.signature(self.handler)
        kwargs: dict[str, Any] = {}
        for name, param in signature.parameters.items():
            if name == "runner":
                kwargs[name] = runner
            elif name in body:
                kwargs[name] = body[name]
            elif param.default is inspect.Signature.empty:
                raise ValueError(f"Missing required workflow field: {name}")
        return kwargs
