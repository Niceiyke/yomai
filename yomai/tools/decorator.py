from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, Literal, TypeVar, get_args, get_origin, get_type_hints, overload

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from yomai.tools.registry import ToolSchema

F = TypeVar("F", bound=Callable[..., Any])

_TYPE_MAP: dict[type[Any], str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _unwrap_annotated(annotation: Any) -> Any:
    """Unwrap Annotated[T, ...] to return T."""
    origin = get_origin(annotation)
    if origin is not None:
        try:
            if origin.__name__ == "Annotated":
                args = get_args(annotation)
                if args:
                    return args[0]
        except AttributeError:
            pass
    return annotation


def _extract_description(annotation: Any) -> str:
    """Extract Field description from Annotated[T, Field(description='...')]."""
    if hasattr(annotation, "__metadata__"):
        for meta in annotation.__metadata__:
            if isinstance(meta, FieldInfo):
                return meta.description or ""
    return ""


def _json_schema_for_annotation(annotation: Any) -> ToolSchema:
    if annotation is inspect.Signature.empty:
        return {"type": "string"}

    annotation = _unwrap_annotated(annotation)

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Literal:
        values = list(args)
        base = type(values[0]) if values else str
        return {"type": _TYPE_MAP.get(base, "string"), "enum": values}

    if origin is list or annotation is list:
        item_schema = _json_schema_for_annotation(args[0]) if args else {}
        schema: ToolSchema = {"type": "array"}
        if item_schema:
            schema["items"] = item_schema
        return schema

    if origin is set or annotation is set:
        item_schema = _json_schema_for_annotation(args[0]) if args else {}
        schema: ToolSchema = {"type": "array", "uniqueItems": True}
        if item_schema:
            schema["items"] = item_schema
        return schema

    if origin is tuple or annotation is tuple:
        if not args:
            return {"type": "array"}
        if len(args) == 2 and args[1] is Ellipsis:
            item_schema = _json_schema_for_annotation(args[0])
            schema: ToolSchema = {"type": "array"}
            if item_schema:
                schema["items"] = item_schema
            return schema
        prefix_items = [_json_schema_for_annotation(a) for a in args]
        return {
            "type": "array",
            "prefixItems": prefix_items,
            "minItems": len(prefix_items),
            "maxItems": len(prefix_items),
        }

    if origin is dict or annotation is dict:
        return {"type": "object"}

    if origin is not None and type(None) in args:
        non_none = [arg for arg in args if arg is not type(None)]
        if non_none:
            return _json_schema_for_annotation(non_none[0])

    if inspect.isclass(annotation):
        import datetime
        from uuid import UUID

        if issubclass(annotation, BaseModel):
            return annotation.model_json_schema()
        if issubclass(annotation, (datetime.datetime, datetime.date)):
            return {"type": "string", "format": "date-time"}
        if annotation is UUID:
            return {"type": "string", "format": "uuid"}

    return {"type": _TYPE_MAP.get(annotation, "string")}


def _build_schema(fn: Callable[..., Any]) -> ToolSchema:
    signature = inspect.signature(fn)
    type_hints = get_type_hints(fn)
    properties: dict[str, ToolSchema] = {}
    required: list[str] = []

    for name, param in signature.parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        annotation = type_hints.get(name, param.annotation)
        prop_schema = _json_schema_for_annotation(annotation)
        desc = _extract_description(annotation)
        if desc:
            prop_schema["description"] = desc
        properties[name] = prop_schema
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
def tool(fn: F, *, cache_ttl: int | None = None, timeout_secs: int | None = None, max_retries: int = 0) -> F: ...


@overload
def tool(
    fn: None = None, *, cache_ttl: int | None = None, timeout_secs: int | None = None, max_retries: int = 0
) -> Callable[[F], F]: ...


def tool(
    fn: F | None = None, *, cache_ttl: int | None = None, timeout_secs: int | None = None, max_retries: int = 0
) -> F | Callable[[F], F]:
    """Mark a sync or async Python function as LLM-callable while preserving its type.

    Args:
        cache_ttl: Seconds to cache the tool result for identical arguments. In-memory cache.
        timeout_secs: Max seconds a single tool invocation may run before being cancelled.
        max_retries: Number of retry attempts on tool failure (0 = no retry).
    """

    def decorate(func: F) -> F:
        func.schema = _build_schema(func)
        func.tool_name = func.__name__
        func._tool_timeout_secs = timeout_secs
        func._tool_max_retries = max_retries
        func._tool_cache_ttl = cache_ttl
        return func

    if fn is None:
        return decorate
    return decorate(fn)
