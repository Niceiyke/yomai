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
    """Format tool definitions in Anthropic's JSON schema format.

    Each tool's schema is wrapped in an ``input_schema`` key as required
    by the Anthropic Messages API.
    """
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
    """Format tool definitions in OpenAI's function-calling JSON schema.

    Each tool is wrapped as ``{"type": "function", "function": {...}}``
    as required by the OpenAI Chat Completions API.
    """
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
