from __future__ import annotations

import inspect
import warnings
from collections.abc import Callable
from typing import Any, Literal, TypeVar, get_args, get_origin, get_type_hints, overload

from yomai.tools.registry import ToolSchema, _registry

F = TypeVar("F", bound=Callable[..., Any])

_TYPE_MAP: dict[type[Any], str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _json_schema_for_annotation(annotation: Any) -> ToolSchema:
    if annotation is inspect.Signature.empty:
        return {"type": "string"}

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Literal:
        values = list(args)
        base = type(values[0]) if values else str
        schema = {"type": _TYPE_MAP.get(base, "string"), "enum": values}
        return schema

    if origin is list or annotation is list:
        item_schema = _json_schema_for_annotation(args[0]) if args else {}
        schema: ToolSchema = {"type": "array"}
        if item_schema:
            schema["items"] = item_schema
        return schema

    if origin is dict or annotation is dict:
        return {"type": "object"}

    if origin is not None and type(None) in args:
        non_none = [arg for arg in args if arg is not type(None)]
        if non_none:
            return _json_schema_for_annotation(non_none[0])

    return {"type": _TYPE_MAP.get(annotation, "string")}


def _build_schema(fn: Callable[..., Any]) -> ToolSchema:
    signature = inspect.signature(fn)
    type_hints = get_type_hints(fn)
    properties: dict[str, ToolSchema] = {}
    required: list[str] = []

    for name, param in signature.parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        properties[name] = _json_schema_for_annotation(type_hints.get(name, param.annotation))
        if param.default is inspect.Signature.empty:
            required.append(name)

    return {
        "name": fn.__name__,
        "description": inspect.getdoc(fn) or "",
        "type": "object",
        "properties": properties,
        "required": required,
    }


@overload
def tool(fn: F, *, cache_ttl: int | None = None) -> F: ...


@overload
def tool(fn: None = None, *, cache_ttl: int | None = None) -> Callable[[F], F]: ...


def tool(fn: F | None = None, *, cache_ttl: int | None = None) -> F | Callable[[F], F]:
    """Mark a sync or async Python function as LLM-callable while preserving its type."""
    if cache_ttl is not None:
        warnings.warn(
            "cache_ttl has no effect in V1. Redis-backed caching ships in V2.",
            DeprecationWarning,
            stacklevel=2,
        )

    def decorate(func: F) -> F:
        func.schema = _build_schema(func)
        func.tool_name = func.__name__
        _registry.register(func)
        return func

    if fn is None:
        return decorate
    return decorate(fn)
