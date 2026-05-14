from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, TypeAlias

from yomai.llm.anthropic import AnthropicProvider
from yomai.llm.base import Done, LLMEvent, Message, TextChunk, ToolCall, ToolSchema
from yomai.llm.gemini import GeminiProvider
from yomai.llm.groq import GroqProvider
from yomai.llm.mistral import MistralProvider
from yomai.llm.openai import OpenAIProvider
from yomai.llm.vllm import VLLMProvider


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


class _MockStream:
    """Async iterator adapter for deterministic mock responses."""

    def __init__(self, items: list[MockResponseItem | Done]) -> None:
        self._items = items
        self._pos = 0

    def __aiter__(self) -> _MockStream:
        return self

    async def __anext__(self) -> LLMEvent:
        if self._pos >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._pos]
        self._pos += 1
        if isinstance(item, str):
            return TextChunk(item)
        if isinstance(item, Done):
            return item
        if isinstance(item, MockToolCall):
            return ToolCall(id=item.id, name=item.name, args=item.args)
        raise StopAsyncIteration


@contextmanager
def mock_llm(responses: list[MockResponseItem | MockTurn] | None = None):
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
    original_gemini_init = GeminiProvider.__init__
    original_gemini_stream = GeminiProvider.stream
    original_mistral_init = MistralProvider.__init__
    original_mistral_stream = MistralProvider.stream
    original_groq_init = GroqProvider.__init__
    original_groq_stream = GroqProvider.stream
    original_vllm_init = VLLMProvider.__init__
    original_vllm_stream = VLLMProvider.stream

    def fake_init(self: Any, config: Any) -> None:
        self.model = getattr(config, "model", "mock")
        self.max_tokens = getattr(config, "max_tokens", 1024)

    def fake_stream(
        self: Any,
        messages: list[Message],
        tools: list[ToolSchema],
        system: str,
    ) -> _MockStream:
        nonlocal index
        if index < len(turns):
            turn = turns[index]
            index += 1
        else:
            turn = []

        input_tokens = sum(len(str(message.get("content", "")).split()) for message in messages)
        output_tokens = sum(len(item.split()) for item in turn if isinstance(item, str))

        items: list[MockResponseItem | Done] = list(turn) + [
            Done(input_tokens=input_tokens, output_tokens=output_tokens)
        ]
        return _MockStream(items)

    AnthropicProvider.__init__ = fake_init  # type: ignore[method-assign]
    AnthropicProvider.stream = fake_stream  # type: ignore[method-assign]
    OpenAIProvider.__init__ = fake_init  # type: ignore[method-assign]
    OpenAIProvider.stream = fake_stream  # type: ignore[method-assign]
    GeminiProvider.__init__ = fake_init  # type: ignore[method-assign]
    GeminiProvider.stream = fake_stream  # type: ignore[method-assign]
    MistralProvider.__init__ = fake_init  # type: ignore[method-assign]
    MistralProvider.stream = fake_stream  # type: ignore[method-assign]
    GroqProvider.__init__ = fake_init  # type: ignore[method-assign]
    GroqProvider.stream = fake_stream  # type: ignore[method-assign]
    VLLMProvider.__init__ = fake_init  # type: ignore[method-assign]
    VLLMProvider.stream = fake_stream  # type: ignore[method-assign]
    try:
        yield
    finally:
        AnthropicProvider.__init__ = original_anthropic_init  # type: ignore[method-assign]
        AnthropicProvider.stream = original_anthropic_stream  # type: ignore[method-assign]
        OpenAIProvider.__init__ = original_openai_init  # type: ignore[method-assign]
        OpenAIProvider.stream = original_openai_stream  # type: ignore[method-assign]
        GeminiProvider.__init__ = original_gemini_init  # type: ignore[method-assign]
        GeminiProvider.stream = original_gemini_stream  # type: ignore[method-assign]
        MistralProvider.__init__ = original_mistral_init  # type: ignore[method-assign]
        MistralProvider.stream = original_mistral_stream  # type: ignore[method-assign]
        GroqProvider.__init__ = original_groq_init  # type: ignore[method-assign]
        GroqProvider.stream = original_groq_stream  # type: ignore[method-assign]
        VLLMProvider.__init__ = original_vllm_init  # type: ignore[method-assign]
        VLLMProvider.stream = original_vllm_stream  # type: ignore[method-assign]
