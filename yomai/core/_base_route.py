"""Shared base class for all Yomai route types."""
from __future__ import annotations

import hmac
import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from starlette.responses import JSONResponse

from yomai._types import Request
from yomai.auth import NoAuth
from yomai.memory import MemoryBackend

if TYPE_CHECKING:
    from yomai.auth import AuthBackend

LifecycleCallback = Callable[[], None]
AcceptCallback = Callable[[], bool]


_DEP_CACHE_ATTR = "_yomai_dep_cache"


def get_dep(request: Request, dep: Any) -> Any:
    """Retrieve the cached result of a :class:`Depends` callable within a request."""
    cache = getattr(request.state, _DEP_CACHE_ATTR, None)
    if cache is not None:
        return cache.get(id(dep))
    return None


async def resolve_dependency(dep: Any, request: Request) -> None:
    """Resolve a single Depends item, storing cached results on request.state."""
    if not hasattr(dep, "callable"):
        return
    if getattr(dep, "use_cache", True):
        cache = getattr(request.state, _DEP_CACHE_ATTR, None)
        if cache is None:
            cache = {}
            setattr(request.state, _DEP_CACHE_ATTR, cache)
        dep_key = id(dep)
        if dep_key in cache:
            return
        result = dep.callable(request)
        if inspect.isawaitable(result):
            result = await result
        cache[dep_key] = result
    else:
        result = dep.callable(request)
        if inspect.isawaitable(result):
            await result


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
        auth: AuthBackend | None = None,
        response_model: type[BaseModel] | None = None,
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
        self.auth = auth
        self.response_model = response_model

    def _cors_headers(self, request: Request | None = None) -> dict[str, str]:
        """Build CORS headers from route-level cors config."""
        if not self.cors:
            return {}
        headers: dict[str, str] = {}
        allow_origins = self.cors.get("allow_origins", [])
        if isinstance(allow_origins, str):
            allow_origins = [allow_origins]
        if allow_origins:
            if request is not None:
                origin = request.headers.get("Origin", "")
                if origin in allow_origins:
                    headers["Access-Control-Allow-Origin"] = origin
                elif "*" in allow_origins:
                    headers["Access-Control-Allow-Origin"] = "*"
                elif not origin and len(allow_origins) == 1:
                    headers["Access-Control-Allow-Origin"] = allow_origins[0]
            else:
                if len(allow_origins) == 1:
                    headers["Access-Control-Allow-Origin"] = allow_origins[0]
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
            await resolve_dependency(dep, request)

    async def _check_auth(self, request: Request) -> JSONResponse | None:
        """Return error response if auth fails or server is draining, else None."""
        if self.should_accept is not None and not self.should_accept():
            return JSONResponse({"error": "Server is shutting down"}, status_code=503)

        # Custom auth backend (skip NoAuth — it's the sentinel for "no auth required")
        if self.auth is not None and not isinstance(self.auth, NoAuth):
            result = await self.auth.authenticate(request)
            if result is None:
                return JSONResponse({"error": "Authentication required"}, status_code=401)
            request.state.yomai_auth = result
            return None

        # Legacy API key check
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

    @staticmethod
    def _maybe_validate_response(result: Any, response_model: type[BaseModel] | None) -> Any:
        """Validate and serialize a handler result against an optional Pydantic model."""
        if response_model is None:
            return result
        if isinstance(result, BaseModel):
            return result.model_dump(mode="json")
        if isinstance(result, dict):
            return response_model.model_validate(result).model_dump(mode="json")
        return response_model.model_validate(result).model_dump(mode="json")
