from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, TypeAlias

Message: TypeAlias = dict[str, Any]
ToolSchema: TypeAlias = dict[str, Any]


@dataclass(slots=True)
class TextChunk:
    content: str


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(slots=True)
class Done:
    input_tokens: int = 0
    output_tokens: int = 0


LLMEvent: TypeAlias = TextChunk | ToolCall | Done


class LLMProvider(ABC):
    """Abstract base for LLM streaming providers.

    Implementations stream LLM responses as a sequence of typed events:
    :class:`TextChunk` (incremental text), :class:`ToolCall` (parsed tool
    invocation), and :class:`Done` (usage statistics).

    Providers may optionally implement ``tool_schemas(tools)`` to format
    tool definitions in the provider's native format, and
    ``tool_result_messages(tool_call, result)`` to produce the follow-up
    messages after tool execution.
    """

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
        system: str,
    ) -> AsyncIterator[LLMEvent]:
        """Return an async iterator of LLM events.

        This is intentionally a regular `def`, not `async def`. An `async def`
        without `yield` is typed as returning a Coroutine, which makes callers see
        `provider.stream(...)` as non-iterable in `async for`. Concrete providers
        may implement this as an async generator function.
        """
        ...
