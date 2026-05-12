"""Shared base class for all Yomai route types."""
from __future__ import annotations

import hmac
import inspect
from collections.abc import Callable
from typing import Any

from starlette.responses import JSONResponse

from yomai._types import Request
from yomai.memory import MemoryBackend

LifecycleCallback = Callable[[], None]
AcceptCallback = Callable[[], bool]


class BaseRoute:
    """Shared behaviour for all Yomai route types (agent, workflow, REST)."""

    def __init__(
        self,
        path: str,
        handler: Callable[..., Any],
        memory: MemoryBackend | None = None,
        on_stream_start: LifecycleCallback | None = None,
        on_stream_end: LifecycleCallback | None = None,
        should_accept: AcceptCallback | None = None,
        log_usage: bool = True,
        required_api_key: str = "",
        path_params: set[str] | None = None,
        cors: dict[str, Any] | None = None,
        dependencies: list[Any] | None = None,
    ) -> None:
        self.path = path
        self.handler = handler
        self.memory = memory
        self.on_stream_start = on_stream_start
        self.on_stream_end = on_stream_end
        self.should_accept = should_accept
        self.log_usage = log_usage
        self.required_api_key = required_api_key
        self.path_params = path_params or set()
        self.cors = cors or {}
        self.dependencies = dependencies or []

    def _cors_headers(self) -> dict[str, str]:
        """Build CORS headers from route-level cors config."""
        if not self.cors:
            return {}
        headers: dict[str, str] = {}
        allow_origins = self.cors.get("allow_origins", [])
        if isinstance(allow_origins, str):
            allow_origins = [allow_origins]
        if allow_origins:
            headers["Access-Control-Allow-Origin"] = ", ".join(allow_origins)
        if self.cors.get("allow_credentials"):
            headers["Access-Control-Allow-Credentials"] = "true"
        allow_methods = self.cors.get("allow_methods")
        if allow_methods:
            if isinstance(allow_methods, str):
                allow_methods = [allow_methods]
            headers["Access-Control-Allow-Methods"] = ", ".join(allow_methods)
        allow_headers = self.cors.get("allow_headers")
        if allow_headers:
            if isinstance(allow_headers, str):
                allow_headers = [allow_headers]
            headers["Access-Control-Allow-Headers"] = ", ".join(allow_headers)
        return headers

    async def _run_dependencies(self, request: Request, path_kwargs: dict[str, Any]) -> None:
        """Run dependency callables, injecting results into request state."""
        request._yomai_path_kwargs = path_kwargs
        for dep in self.dependencies:
            if hasattr(dep, "callable"):
                result = dep.callable(request)
                if inspect.isawaitable(result):
                    result = await result

    async def _check_auth(self, request: Request) -> JSONResponse | None:
        """Return error response if auth fails or server is draining, else None."""
        if self.should_accept is not None and not self.should_accept():
            return JSONResponse({"error": "Server is shutting down"}, status_code=503)
        if self.required_api_key:
            auth = request.headers.get("Authorization", "")
            expected = f"Bearer {self.required_api_key}"
            if not hmac.compare_digest(auth, expected):
                return JSONResponse({"error": "Invalid or missing API key"}, status_code=401)
        return None

    def _extract_path_kwargs(self, request: Request) -> dict[str, Any]:
        """Extract path parameters from the request."""
        kwargs: dict[str, Any] = {}
        if self.path_params:
            for param_name in self.path_params:
                value = request.path_params.get(param_name)
                if value is not None:
                    kwargs[param_name] = value
        return kwargs
