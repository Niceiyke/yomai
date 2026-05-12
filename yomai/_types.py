"""Type extensions for external classes used throughout yomai."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, TypeAlias

from starlette.requests import Request as _StarletteRequest

if TYPE_CHECKING:
    from starlette.requests import Request as _StarletteRequest

__all__ = ["YomaiRequest", "Request", "read_json_body"]

MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB


class YomaiRequest(_StarletteRequest):
    """Request with Yomai-specific attributes."""

    _yomai_path_kwargs: dict[str, Any]


# Re-export as the default Request type for type hints
Request: TypeAlias = YomaiRequest


async def read_json_body(request: _StarletteRequest, max_size: int = MAX_BODY_SIZE) -> dict[str, Any]:
    """Read and parse JSON request body with a size limit."""
    body_bytes = b""
    async for chunk in request.stream():
        body_bytes += chunk
        if len(body_bytes) > max_size:
            raise ValueError("Request body too large")
    if not body_bytes:
        raise ValueError("Request body must not be empty")
    return json.loads(body_bytes.decode())
