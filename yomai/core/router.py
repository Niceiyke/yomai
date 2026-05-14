from __future__ import annotations

import asyncio
import datetime
import enum
import inspect
import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any, Union, get_type_hints
from uuid import UUID

from pydantic import BaseModel, TypeAdapter, ValidationError
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.websockets import WebSocket, WebSocketDisconnect

from yomai import env
from yomai._types import Request, read_json_body
from yomai.auth import AuthBackend
from yomai.config import AgentConfig, LLMConfig
from yomai.core._base_route import AcceptCallback, BaseRoute, LifecycleCallback
from yomai.core.agent import AgentLoop, _message_text
from yomai.core.schemas import AgentRequest
from yomai.llm import LLMProvider
from yomai.memory import MemoryBackend
from yomai.middleware.logging import StreamLog
from yomai.streaming.sse import heartbeat, sse_done, sse_error
from yomai.tools.registry import ToolFunction
from yomai.workflow.events import sse_result
from yomai.workflow.runner import WorkflowRunner


def _format_validation_error(exc: Exception) -> dict[str, str]:
    """Produce a user-friendly error dict from a Pydantic ValidationError."""
    from pydantic import ValidationError

    if isinstance(exc, ValidationError):
        messages: list[str] = []
        for err in exc.errors():
            loc = " → ".join(str(p) for p in err["loc"])
            messages.append(f"{loc}: {err['msg']}")
        return {"error": "; ".join(messages)}
    return {"error": str(exc)}


# Reusable type adapters for common types
_UUID_ADAPTER = TypeAdapter(UUID)
_DATETIME_ADAPTER = TypeAdapter(datetime.datetime)


def _handler_type_hints(handler: Callable[..., Any]) -> dict[str, Any]:
    localns: dict[str, Any] = dict(getattr(handler, "_yomai_type_locals", {}) or {})
    closure = getattr(handler, "__closure__", None)
    if closure:
        for cell in closure:
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            name = getattr(value, "__name__", None)
            if isinstance(name, str):
                localns[name] = value
    try:
        return get_type_hints(handler, globalns=getattr(handler, "__globals__", {}), localns=localns)
    except Exception:
        return getattr(handler, "__annotations__", {})


def _get_annotation(handler: Callable[..., Any], name: str, param: inspect.Parameter) -> Any:
    return _handler_type_hints(handler).get(name, param.annotation)


_SAFE_ANNOTATIONS = frozenset({str, int, float, bool, dict, UUID})


def _coerce_value(value: Any, annotation: Any, name: str) -> Any:
    """Coerce a JSON value to the annotated Python type."""
    if annotation is inspect.Signature.empty:
        return value
    if isinstance(annotation, str):
        builtin = {"str": str, "int": int, "float": float, "bool": bool, "list": list, "dict": dict}.get(annotation)
        if builtin is not None:
            annotation = builtin
        else:
            return value

    # Handle typing generics (e.g., list[str], dict[str, int])
    origin = getattr(annotation, "__origin__", None)

    # Resolve Optional/Union
    args = getattr(annotation, "__args__", ())
    if origin is not None:
        # list[T]
        if origin is list and args:
            item_type = args[0]
            return [_coerce_value(v, item_type, f"{name}[{i}]") for i, v in enumerate(value)]
        # dict[T, U]
        if origin is dict and len(args) == 2:
            if not isinstance(value, dict):
                raise ValueError(f"Invalid field {name}: expected dict, got {type(value).__name__}")
            key_type, val_type = args
            return {
                _coerce_value(k, key_type, f"{name}[key]"): _coerce_value(v, val_type, f"{name}[{k}]")
                for k, v in value.items()
            }
        # Union types (including Optional)
        if origin is Union and args:
            # Try each union member
            for arg in args:
                if arg is type(None):
                    if value is None:
                        return None
                    continue
                try:
                    return _coerce_value(value, arg, name)
                except (ValueError, TypeError):
                    continue
            raise ValueError(f"Invalid field {name}: could not match {value!r} to any union type")
        # Callable, Awaitable, etc. - just return raw
        return value

    # Handle bare types
    if annotation is UUID:
        if isinstance(value, UUID):
            return value
        return _UUID_ADAPTER.validate_python(value)
    if annotation is not None and inspect.isclass(annotation):
        # Handle Enum
        if issubclass(annotation, BaseModel):
            return annotation.model_validate(value)
        if issubclass(annotation, enum.Enum):
            if isinstance(value, str):
                return annotation(value)
            return annotation(value)
        # datetime
        if annotation is datetime.datetime or (
            inspect.isclass(annotation) and issubclass(annotation, datetime.datetime)
        ):
            return _DATETIME_ADAPTER.validate_python(value)
        # Literal
        literals = getattr(annotation, "__values__", None)
        if literals is not None:
            if value in literals:
                return value
            raise ValueError(f"Invalid field {name}: must be one of {literals}")
        # Allowlist: only validate safe primitive types and Pydantic models via TypeAdapter
        if annotation in _SAFE_ANNOTATIONS or issubclass(annotation, (BaseModel, enum.Enum)):
            try:
                return TypeAdapter(annotation).validate_python(value)
            except ValidationError:
                raise ValueError(
                    f"Invalid field {name}: {value!r} is not valid for type {annotation.__name__}"
                ) from None
        raise ValueError(f"Unsupported field type for {name}: {annotation.__name__}")

    return value


SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}
ProviderFactory = Callable[[], LLMProvider]


class AgentRoute(BaseRoute):
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
        system: str = "",
        required_api_key: str = "",
        path_params: set[str] | None = None,
        cors: dict[str, Any] | None = None,
        dependencies: list[Any] | None = None,
        auth: AuthBackend | None = None,
        response_model: type[BaseModel] | None = None,
        guardrails: list[str] | None = None,
    ) -> None:
        super().__init__(
            path=path,
            handler=handler,
            memory=memory,
            on_stream_start=on_stream_start,
            on_stream_end=on_stream_end,
            should_accept=should_accept,
            log_usage=log_usage,
            required_api_key=required_api_key,
            path_params=path_params,
            cors=cors,
            dependencies=dependencies,
            auth=auth,
        )
        self.tools = tools or []
        self.llm_config = llm_config
        self.agent_config = agent_config
        self.provider_factory = provider_factory
        self.heartbeat_secs = heartbeat_secs
        self.system = system
        self.response_model = response_model
        self.guardrails = [re.compile(p) for p in (guardrails or [])]
        self._budget_tracker: Any = None

    async def handle(self, request: Request) -> StreamingResponse | JSONResponse:
        auth_error = await self._check_auth(request)
        if auth_error is not None:
            return auth_error

        path_kwargs = self._extract_path_kwargs(request)
        await self._run_dependencies(request, path_kwargs)

        try:
            raw: Any = await read_json_body(request)
        except Exception:
            return JSONResponse({"error": "Request body must be valid JSON"}, status_code=400)

        try:
            body = AgentRequest.model_validate(raw)
        except Exception as exc:
            return JSONResponse(_format_validation_error(exc), status_code=400)

        message = body.message

        session_id = request.headers.get("X-Session-Id") or str(uuid.uuid4())
        try:
            handler_kwargs = self._build_kwargs(body.model_dump(), message, session_id, path_kwargs, request)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        headers = {**SSE_HEADERS, "X-Session-Id": session_id, **self._cors_headers(request)}
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        stream_log = StreamLog(request.method, self.path, session_id, "agent") if self.log_usage else None

        async def put_sse(sse: str) -> None:
            if stream_log is not None:
                stream_log.observe_sse(sse)
            await queue.put(sse)

        async def run_agent() -> None:
            agent_loop: AgentLoop | None = None
            try:
                handler_result = self.handler(**handler_kwargs)
                if inspect.isawaitable(handler_result):
                    handler_result = await handler_result

                # Handler can return a dict to dynamically override system/context/message
                system = self.system
                user_message = message
                if isinstance(handler_result, dict):
                    if handler_result.get("system"):
                        system = str(handler_result["system"])
                    if handler_result.get("context"):
                        user_message = f"{handler_result['context']}\n\n---\n{message}"
                    if handler_result.get("message"):
                        user_message = str(handler_result["message"])

                # Guardrails: strip prompt injection patterns
                for pattern in self.guardrails:
                    user_message = pattern.sub("[filtered]", user_message)

                history = await self.memory.load(session_id)  # type: ignore[union-attr]
                agent_loop = AgentLoop(
                    self.provider_factory(),
                    self.tools,
                    self.agent_config,
                    self.llm_config,
                    budget_tracker=getattr(self, "_budget_tracker", None),
                    session_id=session_id,
                    hooks=getattr(self, "_hooks", None),
                    tool_cache=getattr(self, "_tool_cache", None),
                )
                async for sse in agent_loop.run(user_message, history=history, system=system):
                    await put_sse(sse)

                # Structured output: validate and retry on JSON mismatch
                if self.response_model is not None and agent_loop is not None:
                    for retry in range(3):
                        try:
                            validated = self._extract_json(agent_loop.last_reply, self.response_model)
                            await put_sse(sse_result(json.dumps(validated.model_dump())))
                            break
                        except Exception:
                            if retry < 2:
                                retry_prompt = (
                                    f"Your last response was not valid JSON matching the required schema. "
                                    f"Please respond with ONLY a JSON object. Schema: {json.dumps(self.response_model.model_json_schema())}\n"
                                )
                                async for sse in agent_loop.run(retry_prompt, history=history, system=system):
                                    await put_sse(sse)
                            else:
                                await put_sse(
                                    sse_error(
                                        "Failed to produce valid structured output after 3 attempts", "schema_error"
                                    )
                                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message_out = "Internal server error" if env.YOMAI_ENV == "production" else str(exc)
                await put_sse(sse_error(message_out, exc.__class__.__name__))
            finally:
                if agent_loop is not None:
                    await self.memory.save(session_id, _message_text(message), agent_loop.last_reply or "")  # type: ignore[union-attr]
                await queue.put(None)

        async def generate() -> AsyncIterator[str]:
            agent_task = asyncio.create_task(run_agent())
            _stream_tasks = getattr(self, "_stream_tasks", None)
            _stream_tasks_lock = getattr(self, "_stream_tasks_lock", None)
            if _stream_tasks is not None and _stream_tasks_lock is not None:
                async with _stream_tasks_lock:
                    _stream_tasks[session_id] = agent_task
            heartbeat_task = asyncio.create_task(heartbeat(queue, self.heartbeat_secs))
            started_at = time.monotonic()
            seq = 0
            if self.on_stream_start is not None:
                self.on_stream_start()
            hooks = getattr(self, "_hooks", None)
            if hooks is not None:
                hooks.emit_background("stream.start", session_id=session_id, path=self.path)
            try:
                while True:
                    if time.monotonic() - started_at > self.agent_config.timeout_secs:
                        if not agent_task.done():
                            agent_task.cancel()
                        timeout_sse = sse_error("Agent request timed out", "timeout")
                        if stream_log is not None:
                            stream_log.observe_sse(timeout_sse)
                        seq += 1
                        yield f"id: {seq}\n{timeout_sse}"
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
                    seq += 1
                    yield f"id: {seq}\n{item}"
            finally:
                if _stream_tasks is not None and _stream_tasks_lock is not None:
                    async with _stream_tasks_lock:
                        _stream_tasks.pop(session_id, None)
                heartbeat_task.cancel()
                if not agent_task.done():
                    agent_task.cancel()
                if self.on_stream_end is not None:
                    self.on_stream_end()
                if hooks is not None:
                    hooks.emit_background("stream.end", session_id=session_id, path=self.path)
                if stream_log is not None:
                    stream_log.emit()

        return StreamingResponse(generate(), media_type="text/event-stream", headers=headers)

    def _extract_json(self, text: str, model: type[BaseModel]) -> BaseModel:
        """Try to extract a JSON object from LLM output and validate against the model.

        Strategy (in order):
        1. Extract from ```json fences first (most common LLM format).
        2. Scan backward from each '{' for the rightmost full JSON object.
        3. Parse entire text as JSON.
        """
        import json as json_lib

        decoder = json_lib.JSONDecoder()

        fence_pattern = re.compile(r"```(?:json)?\s*\n(.*?)\n\s*```", re.DOTALL)
        for match in fence_pattern.finditer(text):
            try:
                block = match.group(1).strip()
                obj, _end = decoder.raw_decode(block)
                return model.model_validate(obj)
            except (json_lib.JSONDecodeError, ValidationError, ValueError):
                continue

        positions = [m.start() for m in re.finditer(r"\{", text)]
        for pos in reversed(positions):
            try:
                obj, _end = decoder.raw_decode(text[pos:])
                return model.model_validate(obj)
            except (json_lib.JSONDecodeError, ValidationError):
                continue
        return model.model_validate(json_lib.loads(text))

    def _build_kwargs(
        self,
        body: dict[str, Any],
        message: str,
        session_id: str,
        path_kwargs: dict[str, Any],
        request: Request | None = None,
    ) -> dict[str, Any]:
        signature = inspect.signature(self.handler)
        kwargs: dict[str, Any] = {}
        for name, param in signature.parameters.items():
            if name in path_kwargs:
                kwargs[name] = _coerce_value(path_kwargs[name], _get_annotation(self.handler, name, param), name)
            elif name == "session_id":
                kwargs[name] = session_id
            elif name == "message":
                kwargs[name] = message
            elif name == "request":
                kwargs[name] = request
            elif name in body:
                kwargs[name] = _coerce_value(body[name], _get_annotation(self.handler, name, param), name)
            elif param.default is inspect.Signature.empty:
                raise ValueError(f"Missing required agent field: {name}")
        return kwargs


class WorkflowRoute(BaseRoute):
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
        required_api_key: str = "",
        path_params: set[str] | None = None,
        cors: dict[str, Any] | None = None,
        dependencies: list[Any] | None = None,
        auth: AuthBackend | None = None,
    ) -> None:
        super().__init__(
            path=path,
            handler=handler,
            memory=memory,
            on_stream_start=on_stream_start,
            on_stream_end=on_stream_end,
            should_accept=should_accept,
            log_usage=log_usage,
            required_api_key=required_api_key,
            path_params=path_params,
            cors=cors,
            dependencies=dependencies,
            auth=auth,
        )
        self.app = app

    async def handle(self, request: Request) -> StreamingResponse | JSONResponse:
        auth_error = await self._check_auth(request)
        if auth_error is not None:
            return auth_error

        path_kwargs = self._extract_path_kwargs(request)
        await self._run_dependencies(request, path_kwargs)

        try:
            body: Any = await read_json_body(request)
        except Exception:
            return JSONResponse({"error": "Request body must be valid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)

        session_id = request.headers.get("X-Session-Id") or str(uuid.uuid4())
        headers = {**SSE_HEADERS, "X-Session-Id": session_id, **self._cors_headers(request)}
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        stream_log = StreamLog(request.method, self.path, session_id, "workflow") if self.log_usage else None

        async def put_sse(sse: str) -> None:
            if stream_log is not None:
                stream_log.observe_sse(sse)
            await queue.put(sse)

        async def run_workflow() -> None:
            try:
                runner = WorkflowRunner(queue, session_id, self.memory, self.app)  # type: ignore[arg-type]
                kwargs = self._build_kwargs(body, runner, path_kwargs, request, session_id=session_id)
                result = self.handler(**kwargs)
                if inspect.isawaitable(result):
                    result = await result
                await put_sse(sse_result(result if result is not None else ""))
                await put_sse(sse_done())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message_out = "Internal server error" if env.YOMAI_ENV == "production" else str(exc)
                await put_sse(sse_error(message_out, exc.__class__.__name__))
                await put_sse(sse_done())
            finally:
                await queue.put(None)

        async def generate() -> AsyncIterator[str]:
            task = asyncio.create_task(run_workflow())
            _stream_tasks = getattr(self, "_stream_tasks", None)
            _stream_tasks_lock = getattr(self, "_stream_tasks_lock", None)
            if _stream_tasks is not None and _stream_tasks_lock is not None:
                async with _stream_tasks_lock:
                    _stream_tasks[session_id] = task
            heartbeat_task = asyncio.create_task(heartbeat(queue, self.app.config.streaming.heartbeat_secs))
            started_at = time.monotonic()
            seq = 0
            if self.on_stream_start is not None:
                self.on_stream_start()
            hooks = getattr(self, "_hooks", None)
            if hooks is not None:
                hooks.emit_background("stream.start", session_id=session_id, path=self.path)
            try:
                while True:
                    if time.monotonic() - started_at > self.app.config.streaming.max_duration_secs:
                        if not task.done():
                            task.cancel()
                        timeout_sse = sse_error("Workflow request timed out", "timeout")
                        if stream_log is not None:
                            stream_log.observe_sse(timeout_sse)
                        seq += 1
                        yield f"id: {seq}\n{timeout_sse}"
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
                    seq += 1
                    yield f"id: {seq}\n{item}"
            finally:
                if _stream_tasks is not None and _stream_tasks_lock is not None:
                    async with _stream_tasks_lock:
                        _stream_tasks.pop(session_id, None)
                heartbeat_task.cancel()
                if not task.done():
                    task.cancel()
                if self.on_stream_end is not None:
                    self.on_stream_end()
                if hooks is not None:
                    hooks.emit_background("stream.end", session_id=session_id, path=self.path)
                if stream_log is not None:
                    stream_log.emit()

        return StreamingResponse(generate(), media_type="text/event-stream", headers=headers)

    def _build_kwargs(
        self,
        body: dict[str, Any],
        runner: WorkflowRunner,
        path_kwargs: dict[str, Any],
        request: Request | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        signature = inspect.signature(self.handler)
        kwargs: dict[str, Any] = {}
        for name, param in signature.parameters.items():
            if name == "runner":
                kwargs[name] = runner
            elif name == "request":
                kwargs[name] = request
            elif name == "session_id":
                kwargs[name] = session_id
            elif name in path_kwargs:
                kwargs[name] = _coerce_value(path_kwargs[name], _get_annotation(self.handler, name, param), name)
            elif name in body:
                kwargs[name] = _coerce_value(body[name], _get_annotation(self.handler, name, param), name)
            elif param.default is inspect.Signature.empty:
                raise ValueError(f"Missing required workflow field: {name}")
        return kwargs


class GetRoute(BaseRoute):
    """Non-streaming GET handler for reading data (e.g., session history)."""

    async def handle(self, request: Request) -> JSONResponse:
        auth_error = await self._check_auth(request)
        if auth_error is not None:
            return auth_error

        path_kwargs = self._extract_path_kwargs(request)
        await self._run_dependencies(request, path_kwargs)

        signature = inspect.signature(self.handler)
        kwargs: dict[str, Any] = {}
        for name, param in signature.parameters.items():
            if name == "request":
                kwargs[name] = request
            elif name in path_kwargs:
                kwargs[name] = _coerce_value(path_kwargs[name], _get_annotation(self.handler, name, param), name)
            elif name == "session_id" and "session_id" not in path_kwargs:
                # Only use header-based session_id if not a path parameter
                kwargs[name] = request.headers.get("X-Session-Id", "")
            elif name in request.query_params:
                kwargs[name] = _coerce_value(
                    request.query_params[name], _get_annotation(self.handler, name, param), name
                )
            elif param.default is not inspect.Signature.empty:
                kwargs[name] = param.default

        result = self.handler(**kwargs)
        if inspect.isawaitable(result):
            result = await result

        result = self._maybe_validate_response(result, self.response_model)

        headers = {**self._cors_headers(request)}
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result, headers=headers)


class DeleteRoute(BaseRoute):
    """Non-streaming DELETE handler (e.g., clear session)."""

    async def handle(self, request: Request) -> JSONResponse:
        auth_error = await self._check_auth(request)
        if auth_error is not None:
            return auth_error

        path_kwargs = self._extract_path_kwargs(request)
        await self._run_dependencies(request, path_kwargs)

        signature = inspect.signature(self.handler)
        kwargs: dict[str, Any] = {}
        for name, param in signature.parameters.items():
            if name == "request":
                kwargs[name] = request
            elif name in path_kwargs:
                kwargs[name] = _coerce_value(path_kwargs[name], _get_annotation(self.handler, name, param), name)
            elif name == "session_id":
                # Use header-based session_id if not a path parameter
                kwargs[name] = request.headers.get("X-Session-Id", "")
            elif name in request.query_params:
                kwargs[name] = _coerce_value(
                    request.query_params[name], _get_annotation(self.handler, name, param), name
                )
            elif param.default is not inspect.Signature.empty:
                kwargs[name] = param.default

        result = self.handler(**kwargs)
        if inspect.isawaitable(result):
            result = await result

        result = self._maybe_validate_response(result, self.response_model)

        headers = {**self._cors_headers(request)}
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result, headers=headers)


class HeadRoute(BaseRoute):
    """HEAD endpoint (e.g., check session exists)."""

    async def handle(self, request: Request) -> Response:
        from starlette.responses import Response

        auth_error = await self._check_auth(request)
        if auth_error is not None:
            return auth_error

        path_kwargs = self._extract_path_kwargs(request)
        await self._run_dependencies(request, path_kwargs)

        signature = inspect.signature(self.handler)
        kwargs: dict[str, Any] = {}
        for name, param in signature.parameters.items():
            if name == "request":
                kwargs[name] = request
            elif name in path_kwargs:
                kwargs[name] = _coerce_value(path_kwargs[name], _get_annotation(self.handler, name, param), name)
            elif param.default is not inspect.Signature.empty:
                kwargs[name] = param.default

        result = self.handler(**kwargs)
        if inspect.isawaitable(result):
            result = await result

        headers = self._cors_headers(request)
        if isinstance(result, Response):
            return result
        return Response(status_code=200, headers=headers)


class PatchRoute(BaseRoute):
    """Non-streaming PATCH handler for partial updates."""

    async def handle(self, request: Request) -> JSONResponse:
        auth_error = await self._check_auth(request)
        if auth_error is not None:
            return auth_error

        path_kwargs = self._extract_path_kwargs(request)
        await self._run_dependencies(request, path_kwargs)

        try:
            body: Any = await read_json_body(request)
        except Exception:
            return JSONResponse({"error": "Request body must be valid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)

        signature = inspect.signature(self.handler)
        kwargs: dict[str, Any] = {}
        for name, param in signature.parameters.items():
            if name == "request":
                kwargs[name] = request
            elif name in path_kwargs:
                kwargs[name] = _coerce_value(path_kwargs[name], _get_annotation(self.handler, name, param), name)
            elif name == "session_id":
                # Use header-based session_id if not a path parameter
                kwargs[name] = request.headers.get("X-Session-Id", "")
            elif name in body:
                kwargs[name] = _coerce_value(body[name], _get_annotation(self.handler, name, param), name)
            elif param.default is not inspect.Signature.empty:
                kwargs[name] = param.default

        result = self.handler(**kwargs)
        if inspect.isawaitable(result):
            result = await result

        result = self._maybe_validate_response(result, self.response_model)

        headers = {**self._cors_headers(request)}
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result, headers=headers)


class PutRoute(BaseRoute):
    """Non-streaming PUT handler for full replacement."""

    async def handle(self, request: Request) -> JSONResponse:
        auth_error = await self._check_auth(request)
        if auth_error is not None:
            return auth_error

        path_kwargs = self._extract_path_kwargs(request)
        await self._run_dependencies(request, path_kwargs)

        try:
            body: Any = await read_json_body(request)
        except Exception:
            return JSONResponse({"error": "Request body must be valid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)

        signature = inspect.signature(self.handler)
        kwargs: dict[str, Any] = {}
        for name, param in signature.parameters.items():
            if name == "request":
                kwargs[name] = request
            elif name in path_kwargs:
                kwargs[name] = _coerce_value(path_kwargs[name], _get_annotation(self.handler, name, param), name)
            elif name == "session_id":
                kwargs[name] = request.headers.get("X-Session-Id", "")
            elif name in body:
                kwargs[name] = _coerce_value(body[name], _get_annotation(self.handler, name, param), name)
            elif param.default is not inspect.Signature.empty:
                kwargs[name] = param.default

        result = self.handler(**kwargs)
        if inspect.isawaitable(result):
            result = await result

        result = self._maybe_validate_response(result, self.response_model)

        headers = {**self._cors_headers(request)}
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result, headers=headers)


class OptionsRoute(BaseRoute):
    """OPTIONS endpoint for CORS preflight."""

    async def handle(self, request: Request) -> Response:
        from starlette.responses import Response

        if self.should_accept is not None and not self.should_accept():
            return Response(status_code=503)
        origin = request.headers.get("Origin", "")
        allow_origins = self.cors.get("allow_origins", [])
        if isinstance(allow_origins, str):
            allow_origins = [allow_origins]
        headers: dict[str, str] = {
            "Access-Control-Allow-Methods": ", ".join(self.cors.get("allow_methods", ["GET", "POST", "OPTIONS"])),
            "Access-Control-Allow-Headers": ", ".join(
                self.cors.get("allow_headers", ["Content-Type", "Authorization", "X-Session-Id"])
            ),
            "Access-Control-Max-Age": "3600",
        }
        if origin in allow_origins:
            headers["Access-Control-Allow-Origin"] = origin
        elif "*" in allow_origins:
            headers["Access-Control-Allow-Origin"] = "*"
        elif not origin and len(allow_origins) == 1:
            headers["Access-Control-Allow-Origin"] = allow_origins[0]
        if self.cors.get("allow_credentials"):
            headers["Access-Control-Allow-Credentials"] = "true"
        return Response(status_code=200, headers=headers)


class AgentWSRoute(AgentRoute):
    """WebSocket variant of AgentRoute for bidirectional real-time streaming.

    Uses the same agent loop as SSE but sends events as JSON frames
    over a WebSocket connection. Supports client-to-server messages
    for follow-up interactions on the same connection.
    """

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
        **kwargs: Any,
    ) -> None:
        super().__init__(
            path=path,
            handler=handler,
            tools=tools,
            llm_config=llm_config,
            agent_config=agent_config,
            memory=memory,
            provider_factory=provider_factory,
            heartbeat_secs=heartbeat_secs,
            **kwargs,
        )

    async def handle(self, request: Request) -> StreamingResponse | JSONResponse:
        return await super().handle(request)

    async def handle_ws(self, websocket: WebSocket) -> None:
        from yomai.streaming.ws import (
            ws_chunk,
            ws_done,
            ws_error,
            ws_graph_edge,
            ws_graph_update,
            ws_graph_upsert,
            ws_ping,
            ws_tool_end,
            ws_tool_progress,
            ws_tool_start,
            ws_usage,
            parse_ws_message,
        )

        await websocket.accept()
        session_id = websocket.headers.get("X-Session-Id") or str(uuid.uuid4())
        await websocket.send_json({"type": "connected", "session_id": session_id})

        self._active: bool = True
        heartbeat_task = asyncio.create_task(self._ws_heartbeat(websocket))

        try:
            while self._active:
                try:
                    raw = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
                except asyncio.TimeoutError:
                    continue

                msg = parse_ws_message(raw)
                msg_type = msg.get("type", "message")

                if msg_type == "ping":
                    await websocket.send_text(ws_ping())
                    continue

                if msg_type == "stop":
                    await websocket.send_text(ws_done())
                    break

                message = msg.get("content", msg.get("message", ""))
                if not message and isinstance(raw, str):
                    message = raw

                if not message:
                    continue

                history = await self.memory.load(session_id)
                agent_loop = AgentLoop(
                    self.provider_factory(),
                    self.tools,
                    self.agent_config,
                    self.llm_config,
                    budget_tracker=getattr(self, "_budget_tracker", None),
                    session_id=session_id,
                    hooks=getattr(self, "_hooks", None),
                    tool_cache=getattr(self, "_tool_cache", None),
                )

                async for sse_event in agent_loop.run(message, history=history, system=self.system):
                    await asyncio.sleep(0)

                    try:
                        parsed = json.loads(
                            sse_event.removeprefix("event: chunk\ndata: ")
                            .removeprefix("event: tool_start\ndata: ")
                            .removesuffix("\n")
                        )
                    except json.JSONDecodeError:
                        try:
                            lines = sse_event.split("\n")
                            event_type = ""
                            data_str = ""
                            for line in lines:
                                if line.startswith("event:"):
                                    event_type = line.removeprefix("event:").strip()
                                elif line.startswith("data:"):
                                    data_str = line.removeprefix("data:").strip() + "\n" + data_str
                            data_str = data_str.strip()
                            parsed = json.loads(data_str) if data_str else {"type": event_type}
                        except Exception:
                            continue

                    ev_type = parsed.get("type", "")
                    if ev_type == "chunk":
                        await websocket.send_text(ws_chunk(str(parsed.get("content", ""))))
                    elif ev_type == "tool_start":
                        await websocket.send_text(
                            ws_tool_start(
                                str(parsed.get("name", "")),
                                dict(parsed.get("args", {})),
                                str(parsed.get("id", "")),
                            )
                        )
                    elif ev_type == "tool_end":
                        await websocket.send_text(
                            ws_tool_end(
                                str(parsed.get("id", "")),
                                str(parsed.get("result", "")),
                                int(parsed.get("duration_ms", 0)),
                            )
                        )
                    elif ev_type == "tool_progress":
                        await websocket.send_text(
                            ws_tool_progress(
                                str(parsed.get("id", "")),
                                str(parsed.get("message", "")),
                            )
                        )
                    elif ev_type == "usage":
                        await websocket.send_text(
                            ws_usage(
                                int(parsed.get("input_tokens", 0)),
                                int(parsed.get("output_tokens", 0)),
                                float(parsed.get("cost_usd", 0)),
                            )
                        )
                    elif ev_type == "graph":
                        action = parsed.get("action", "")
                        if action == "upsert":
                            await websocket.send_text(
                                ws_graph_upsert(
                                    str(parsed.get("id", "")),
                                    str(parsed.get("label", "")),
                                    str(parsed.get("kind", "")),
                                    str(parsed.get("status", "running")),
                                    parent=parsed.get("parent"),
                                    meta=parsed.get("meta"),
                                )
                            )
                        elif action == "edge":
                            await websocket.send_text(
                                ws_graph_edge(
                                    str(parsed.get("from", "")),
                                    str(parsed.get("to", "")),
                                    str(parsed.get("label", "")),
                                )
                            )
                        elif action == "update":
                            await websocket.send_text(
                                ws_graph_update(
                                    str(parsed.get("id", "")),
                                    str(parsed.get("status", "")),
                                    meta=parsed.get("meta"),
                                )
                            )
                    elif ev_type == "error":
                        await websocket.send_text(
                            ws_error(
                                str(parsed.get("message", "")),
                                str(parsed.get("code", "error")),
                            )
                        )

                await websocket.send_text(ws_done())
                await self.memory.save(session_id, message, agent_loop.last_reply or "")

        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            pass
        except Exception:
            try:
                await websocket.send_text(ws_error("Internal server error", "server_error"))
            except Exception:
                pass
        finally:
            self._active = False
            heartbeat_task.cancel()
            try:
                await websocket.close()
            except Exception:
                pass

    async def _ws_heartbeat(self, websocket: WebSocket) -> None:
        from yomai.streaming.ws import ws_ping

        while getattr(self, "_active", True):
            await asyncio.sleep(self.heartbeat_secs)
            if not getattr(self, "_active", True):
                break
            try:
                await websocket.send_text(ws_ping())
            except Exception:
                break
