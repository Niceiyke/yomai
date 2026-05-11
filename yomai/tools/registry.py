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


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolFunction] = {}

    def register(self, fn: ToolFunction) -> None:
        name = getattr(fn, "tool_name", getattr(fn, "__name__", None))
        if not isinstance(name, str) or not name:
            raise ValueError("Tool functions must have a name")
        self._tools[name] = fn

    def get(self, name: str) -> ToolFunction | None:
        return self._tools.get(name)

    def get_schemas_for_anthropic(self, tools: list[ToolFunction]) -> list[ToolSchema]:
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

    def get_schemas_for_openai(self, tools: list[ToolFunction]) -> list[ToolSchema]:
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


_registry: ToolRegistry = ToolRegistry()
