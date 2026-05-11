from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, TypeAlias

from yomai.llm.anthropic import AnthropicProvider
from yomai.llm.base import Done, LLMEvent, Message, TextChunk, ToolCall, ToolSchema
from yomai.llm.openai import OpenAIProvider


@dataclass(slots=True)
class MockToolCall:
    name: str
    args: dict[str, Any]
    id: str = "mock-tool-1"


MockResponseItem: TypeAlias = str | MockToolCall
MockTurn: TypeAlias = list[MockResponseItem]


def _normalise_turn(item: MockResponseItem | MockTurn) -> MockTurn:
    if isinstance(item, list):
        return item
    return [item]


@contextmanager
def mock_llm(responses: list[MockResponseItem | MockTurn] | None = None) -> Iterator[None]:
    """Replace provider streaming with deterministic scripted turns.

    Each provider.stream() call consumes one turn. A bare string or MockToolCall is
    treated as a one-item turn. Done is emitted automatically at the end of each turn.
    """
    turns = [_normalise_turn(item) for item in (responses or [])]
    index = 0

    original_anthropic_init = AnthropicProvider.__init__
    original_anthropic_stream = AnthropicProvider.stream
    original_openai_init = OpenAIProvider.__init__
    original_openai_stream = OpenAIProvider.stream

    def fake_init(self: Any, config: Any) -> None:
        self.model = getattr(config, "model", "mock")
        self.max_tokens = getattr(config, "max_tokens", 1024)

    async def fake_stream(
        self: Any,
        messages: list[Message],
        tools: list[ToolSchema],
        system: str,
    ) -> AsyncIterator[LLMEvent]:
        nonlocal index
        if index < len(turns):
            turn = turns[index]
            index += 1
        else:
            turn = []

        input_tokens = sum(len(str(message.get("content", "")).split()) for message in messages)
        output_tokens = 0
        for item in turn:
            if isinstance(item, str):
                output_tokens += len(item.split())
                yield TextChunk(item)
            else:
                yield ToolCall(id=item.id, name=item.name, args=item.args)
        yield Done(input_tokens=input_tokens, output_tokens=output_tokens)

    AnthropicProvider.__init__ = fake_init  # type: ignore[method-assign]
    AnthropicProvider.stream = fake_stream  # type: ignore[method-assign]
    OpenAIProvider.__init__ = fake_init  # type: ignore[method-assign]
    OpenAIProvider.stream = fake_stream  # type: ignore[method-assign]
    try:
        yield
    finally:
        AnthropicProvider.__init__ = original_anthropic_init  # type: ignore[method-assign]
        AnthropicProvider.stream = original_anthropic_stream  # type: ignore[method-assign]
        OpenAIProvider.__init__ = original_openai_init  # type: ignore[method-assign]
        OpenAIProvider.stream = original_openai_stream  # type: ignore[method-assign]
