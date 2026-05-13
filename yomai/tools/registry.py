from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypeAlias

ToolSchema: TypeAlias = dict[str, Any]
ToolFunction: TypeAlias = Callable[..., Any]


class RegisteredTool(Protocol):
    __name__: str
    tool_name: str
    schema: ToolSchema

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


def get_schemas_for_anthropic(tools: list[ToolFunction]) -> list[ToolSchema]:
    schemas: list[ToolSchema] = []
    for fn in tools:
        schema = getattr(fn, "schema", None)
        if not isinstance(schema, dict):
            continue
        schemas.append(
            {
                "name": getattr(fn, "tool_name", fn.__name__),
                "description": schema.get("description", ""),
                "input_schema": {
                    "type": "object",
                    "properties": schema.get("properties", {}),
                    "required": schema.get("required", []),
                },
            }
        )
    return schemas


def get_schemas_for_openai(tools: list[ToolFunction]) -> list[ToolSchema]:
    schemas: list[ToolSchema] = []
    for fn in tools:
        schema = getattr(fn, "schema", None)
        if not isinstance(schema, dict):
            continue
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": getattr(fn, "tool_name", fn.__name__),
                    "description": schema.get("description", ""),
                    "parameters": {
                        "type": "object",
                        "properties": schema.get("properties", {}),
                        "required": schema.get("required", []),
                    },
                },
            }
        )
    return schemas
