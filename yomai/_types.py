"""Type extensions for external classes used throughout yomai."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, TypeAlias

from starlette.requests import Request as _StarletteRequest

if TYPE_CHECKING:
    from starlette.requests import Request as _StarletteRequest

__all__ = ["YomaiRequest", "Request", "read_json_body"]

MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_FIELD_LENGTH = 100_000  # 100 KB per field
MAX_JSON_DEPTH = 20


class YomaiRequest(_StarletteRequest):
    """Request with Yomai-specific attributes."""

    _yomai_path_kwargs: dict[str, Any]


# Re-export as the default Request type for type hints
Request: TypeAlias = YomaiRequest


async def read_json_body(
    request: _StarletteRequest,
    max_size: int = MAX_BODY_SIZE,
    max_field_length: int = MAX_FIELD_LENGTH,
    max_depth: int = MAX_JSON_DEPTH,
) -> dict[str, Any]:
    """Read and parse JSON request body with size/depth/length limits."""
    body_bytes = b""
    async for chunk in request.stream():
        body_bytes += chunk
        if len(body_bytes) > max_size:
            raise ValueError("Request body too large")
    if not body_bytes:
        raise ValueError("Request body must not be empty")
    data = json.loads(body_bytes.decode())
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object")
    _validate_json_structure(data, max_field_length=max_field_length, max_depth=max_depth)
    return data


def _validate_json_structure(obj: Any, *, max_field_length: int, max_depth: int, _depth: int = 0) -> None:
    """Recursively validate JSON field lengths and nesting depth."""
    if _depth > max_depth:
        raise ValueError("Request body exceeds maximum nesting depth")
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and len(key) > max_field_length:
                raise ValueError("Field name too long")
            if isinstance(value, str) and len(value) > max_field_length:
                raise ValueError("Field value too long")
            _validate_json_structure(value, max_field_length=max_field_length, max_depth=max_depth, _depth=_depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, str) and len(item) > max_field_length:
                raise ValueError("Field value too long")
            _validate_json_structure(item, max_field_length=max_field_length, max_depth=max_depth, _depth=_depth + 1)
