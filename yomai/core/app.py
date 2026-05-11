from __future__ import annotations

import asyncio
import inspect
import os
import signal
from collections.abc import Callable
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from yomai.config import AgentConfig, Config, DevConfig, LLMConfig, MemoryConfig, StreamingConfig
from yomai.core.router import AgentRoute, WorkflowRoute
from yomai.devui.playground import get_playground_html
from yomai.exceptions import YomaiConfigError, YomaiRouteError
from yomai.llm import LLMProvider
from yomai.llm.anthropic import AnthropicProvider
from yomai.llm.openai import OpenAIProvider
from yomai.memory import DictMemory, MemoryBackend, SqliteMemory
from yomai.middleware.errors import ErrorMiddleware
from yomai.middleware.logging import LoggingMiddleware
from yomai.openapi.schema import build_openapi
from yomai.tools.registry import ToolFunction


class Yomai:
    def __init__(
        self,
        llm: LLMConfig | None = None,
        memory: MemoryConfig | None = None,
        agent: AgentConfig | None = None,
        streaming: StreamingConfig | None = None,
        dev: DevConfig | None = None,
    ) -> None:
        self.config = Config(
            llm=llm or LLMConfig(),
            memory=memory or MemoryConfig(),
            agent=agent or AgentConfig(),
            streaming=streaming or StreamingConfig(),
            dev=dev or DevConfig(),
        )
        self.memory: MemoryBackend = self._build_memory(self.config.memory)
        self._active_connections = 0
        self._draining = False
        self._routes_meta: list[dict[str, Any]] = []
        self._paths: set[str] = set()
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
            ]
        )
        self._starlette.add_middleware(LoggingMiddleware, enabled=self.config.dev.log_usage)
        self._starlette.add_middleware(ErrorMiddleware)
        self._setup_signal_handlers()

    def _build_memory(self, cfg: MemoryConfig) -> MemoryBackend:
        if cfg.backend == "dict":
            return DictMemory(max_messages=cfg.max_messages)
        if cfg.backend == "sqlite":
            return SqliteMemory(db_path=cfg.db_path, max_messages=cfg.max_messages)
        raise YomaiConfigError(f"Unknown memory backend: {cfg.backend!r}")

    async def _playground(self, request: Request) -> Response:
        if os.environ.get("YOMAI_ENV") == "production" or not self.config.dev.ui:
            return Response(status_code=404)
        return HTMLResponse(get_playground_html(self._routes_meta))

    async def _health(self, request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": "0.1.0"})

    async def _routes(self, request: Request) -> JSONResponse:
        return JSONResponse(self._routes_meta)

    async def _openapi(self, request: Request) -> JSONResponse:
        title = os.environ.get("YOMAI_APP_TITLE", "Yomai Agent API")
        api_key = self.config.dev.api_key
        schema = build_openapi(self._routes_meta, title=title, api_key=api_key)
        return JSONResponse(schema)

    def agent(
        self,
        path: str,
        tools: list[ToolFunction] | None = None,
        *,
        system: str = "",
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        self._validate_new_path(path)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
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
                self.config.dev.api_key,
            )
            self._starlette.router.routes.append(Route(path, route.handle, methods=["POST"]))
            self._paths.add(path)
            tool_names = [getattr(t, "tool_name", getattr(t, "__name__", str(t))) for t in (tools or [])]
            self._routes_meta.append(
                {
                    "path": path,
                    "type": "agent",
                    "tools": tool_names,
                    "params": self._route_params(fn, injected={"session_id"}),
                    "body_params": ["message"],
                    "injected_params": ["session_id"],
                    "system": system or None,
                }
            )
            setattr(fn, "_yomai_app", self)
            setattr(fn, "_yomai_tools", tools or [])
            setattr(fn, "_yomai_agent_config", self.config.agent)
            return fn

        return decorator

    def workflow(self, path: str, *, mode: str = "stream") -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        if mode != "stream":
            raise YomaiRouteError(
                f"Workflow mode {mode!r} is not available in V1.",
                hint="Async workflow mode ships in V2.",
                docs="https://yomai.dev/roadmap",
            )
        self._validate_new_path(path)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            route = WorkflowRoute(
                path,
                fn,
                self,
                self.memory,
                self._stream_started,
                self._stream_finished,
                self._accepting_connections,
                self.config.dev.log_usage,
                self.config.dev.api_key,
            )
            self._starlette.router.routes.append(Route(path, route.handle, methods=["POST"]))
            self._paths.add(path)
            body_params = self._route_params(fn, injected={"runner"})
            self._routes_meta.append(
                {
                    "path": path,
                    "type": "workflow",
                    "tools": [],
                    "params": body_params,
                    "body_params": [param["name"] for param in body_params],
                    "injected_params": ["runner"],
                }
            )
            setattr(fn, "_yomai_app", self)
            setattr(fn, "_is_workflow", True)
            return fn

        return decorator

    def _route_params(self, fn: Callable[..., Any], *, injected: set[str]) -> list[dict[str, Any]]:
        params: list[dict[str, Any]] = []
        signature = inspect.signature(fn)
        for name, param in signature.parameters.items():
            if name in injected:
                continue
            annotation = param.annotation
            type_name = "Any" if annotation is inspect.Signature.empty else getattr(annotation, "__name__", str(annotation))
            params.append(
                {
                    "name": name,
                    "type": type_name,
                    "required": param.default is inspect.Signature.empty,
                    "default": None if param.default is inspect.Signature.empty else param.default,
                }
            )
        return params

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

    @property
    def active_connections(self) -> int:
        return self._active_connections

    def _setup_signal_handlers(self) -> None:
        # Uvicorn and other ASGI servers already own SIGTERM. Installing an
        # application-level handler by default can prevent the server from
        # exiting. Enable this only for embedded/non-uvicorn use cases.
        if os.environ.get("YOMAI_HANDLE_SIGTERM") != "1":
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
        while self._active_connections > 0 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.1)

    def _validate_new_path(self, path: str) -> None:
        if not path.startswith("/"):
            raise YomaiRouteError("Route path must start with '/'.")
        if path in self._paths:
            raise YomaiRouteError(f"Route already registered: {path}")

    def add_middleware(self, middleware_class: type[Any], **kwargs: Any) -> None:
        self._starlette.add_middleware(middleware_class, **kwargs)

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        await self._starlette(scope, receive, send)
