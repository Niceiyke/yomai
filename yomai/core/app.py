from __future__ import annotations

import asyncio
import datetime
import enum
import hmac
import inspect
import os
import re
import signal
from collections.abc import Callable
from typing import Any
from uuid import UUID

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.status import HTTP_401_UNAUTHORIZED
from starlette.routing import Route, Router

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

    @classmethod
    def depends(cls, func: Callable[..., Any]) -> "Depends":
        return cls(func)


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
            ]
        )
        self._starlette.add_middleware(LoggingMiddleware, enabled=self.config.dev.log_usage)
        self._starlette.add_middleware(ErrorMiddleware)
        self._setup_signal_handlers()
        self._cors_config: dict[str, Any] = {}

    def _build_memory(self, cfg: MemoryConfig) -> MemoryBackend:
        if cfg.backend == "dict":
            return DictMemory(max_messages=cfg.max_messages, ttl_hours=cfg.ttl_hours)
        if cfg.backend == "sqlite":
            return SqliteMemory(db_path=cfg.db_path, max_messages=cfg.max_messages, ttl_hours=cfg.ttl_hours)
        raise YomaiConfigError(f"Unknown memory backend: {cfg.backend!r}")

    async def _playground(self, request: Request) -> Response:
        if os.environ.get("YOMAI_ENV") == "production" or not self.config.dev.ui:
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
        title = os.environ.get("YOMAI_APP_TITLE", "Yomai Agent API")
        api_key = self.config.dev.api_key
        schema = build_openapi(self._routes_meta, title=title, api_key=api_key)
        return JSONResponse(schema)

    def _metadata_auth_error(self, request: Request) -> JSONResponse | None:
        if os.environ.get("YOMAI_ENV") != "production":
            return None
        if not self.config.dev.api_key:
            return JSONResponse({"error": "Metadata endpoint disabled"}, status_code=HTTP_401_UNAUTHORIZED)
        if not hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {self.config.dev.api_key}"):
            return JSONResponse({"error": "Invalid or missing API key"}, status_code=HTTP_401_UNAUTHORIZED)
        return None

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
        self._validate_new_path(path)
        path_params = self._extract_path_params(path)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            from yomai.core.router import AgentRoute

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
            tool_schemas = [getattr(t, "schema") for t in (tools or []) if isinstance(getattr(t, "schema", None), dict)]
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
            setattr(fn, "_yomai_app", self)
            setattr(fn, "_yomai_tools", tools or [])
            setattr(fn, "_yomai_agent_config", self.config.agent)
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
        if mode != "stream":
            raise YomaiRouteError(
                f"Workflow mode {mode!r} is not available in V1.",
                hint="Async workflow mode ships in V2.",
                docs="https://yomai.dev/roadmap",
            )
        self._validate_new_path(path)
        path_params = self._extract_path_params(path)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            from yomai.core.router import WorkflowRoute

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
            setattr(fn, "_yomai_app", self)
            setattr(fn, "_is_workflow", True)
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
        self._validate_new_path(path)
        path_params = self._extract_path_params(path)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
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
        self._validate_new_path(path)
        path_params = self._extract_path_params(path)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
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
        self._validate_new_path(path)
        path_params = self._extract_path_params(path)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            from yomai.core.router import HeadRoute

            route = HeadRoute(
                path,
                fn,
                self.memory,
                self._stream_started,
                self._stream_finished,
                self._accepting_connections,
                path_params,
                cors or {},
                dependencies or [],
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
        self._validate_new_path(path)
        path_params = self._extract_path_params(path)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            from yomai.core.router import OptionsRoute

            route = OptionsRoute(
                path,
                fn,
                self._stream_started,
                self._stream_finished,
                self._accepting_connections,
                path_params,
                cors or {},
                dependencies or [],
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
        self._validate_new_path(path)
        path_params = self._extract_path_params(path)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
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
        self._validate_new_path(path)
        path_params = self._extract_path_params(path)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
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

    @property
    def active_connections(self) -> int:
        return self._active_connections

    def _setup_signal_handlers(self) -> None:
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
