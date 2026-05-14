"""Tests for LLM provider implementations against mocked SDK streams."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from yomai.config import LLMConfig
from yomai.exceptions import YomaiLLMError
from yomai.llm.anthropic import AnthropicProvider
from yomai.llm.base import Done, TextChunk, ToolCall
from yomai.llm.openai import OpenAIProvider

# ---------------------------------------------------------------------------
# Mock Anthropic SDK types
# ---------------------------------------------------------------------------

class _MockAnthropicEvent:
    """Simulates an Anthropic stream event with configurable .type and .attr."""
    def __init__(self, etype: str, **attrs: Any) -> None:
        self.type = etype
        for k, v in attrs.items():
            setattr(self, k, v)


class _MockAnthropicStream:
    """Simulates Anthropic client.messages.stream() async context manager."""

    def __init__(self, events: list[_MockAnthropicEvent], final_usage: tuple[int, int]) -> None:
        self._events = events
        self._pos = 0
        self._final_usage = final_usage

    async def __aenter__(self) -> _MockAnthropicStream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def __aiter__(self) -> _MockAnthropicStream:
        return self

    async def __anext__(self) -> _MockAnthropicEvent:
        if self._pos >= len(self._events):
            raise StopAsyncIteration
        event = self._events[self._pos]
        self._pos += 1
        return event

    async def get_final_message(self) -> _MockAnthropicMessage:
        return _MockAnthropicMessage(*self._final_usage)


class _MockAnthropicMessage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.usage = _MockAnthropicUsage(input_tokens, output_tokens)


class _MockAnthropicUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


def _make_anthropic_stream(events: list[_MockAnthropicEvent], final: tuple[int, int]):
    """Duck-type as async context manager for the provider to use in `async with`."""
    stream = _MockAnthropicStream(events, final)
    ctx = _AsyncCtx(stream)
    return ctx


class _AsyncCtx:
    """Turns any async iterable into an async context manager."""
    def __init__(self, iterable: Any) -> None:
        self._iterable = iterable

    async def __aenter__(self) -> Any:
        return self._iterable

    async def __aexit__(self, *args: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Mock OpenAI SDK types
# ---------------------------------------------------------------------------

class _MockOpenAIChunk:
    def __init__(self, choices: list[Any] | None = None, usage: Any = None) -> None:
        self.choices = choices or []
        self.usage = usage


class _MockOpenAIChoice:
    def __init__(self, delta: Any, finish_reason: str | None = None) -> None:
        self.delta = delta
        self.finish_reason = finish_reason


class _MockOpenAIDelta:
    def __init__(self, content: str | None = None, tool_calls: list[Any] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []


class _MockOpenAIToolDelta:
    def __init__(self, index: int, id_: str | None = None, function: Any = None) -> None:
        self.index = index
        self.id = id_
        self.function = function


class _MockOpenAIFunction:
    def __init__(self, name: str | None = None, arguments: str | None = None) -> None:
        self.name = name
        self.arguments = arguments


class _MockOpenAIUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _provider(cfg: dict[str, Any] | None = None) -> Any:
    """Create a configured provider with defaults suitable for testing."""
    return LLMConfig(api_key="sk-test", max_retries=0, **(cfg or {}))


async def _collect(stream: AsyncIterator[Any]) -> list[Any]:
    return [item async for item in stream]


# ===========================================================================
# AnthropicProvider tests
# ===========================================================================


class TestAnthropicProviderConstruction:
    def test_requires_api_key(self) -> None:
        with pytest.raises(YomaiLLMError, match="api_key"):
            AnthropicProvider(LLMConfig(api_key=""))

    def test_stores_model_and_max_tokens(self) -> None:
        p = AnthropicProvider(LLMConfig(api_key="sk-test", model="claude-haiku", max_tokens=512))
        assert p.model == "claude-haiku"
        assert p.max_tokens == 512

    def test_passes_base_url_to_client(self) -> None:
        p = AnthropicProvider(LLMConfig(api_key="sk-test", base_url="https://proxy.example.com"))
        assert p.client.base_url == "https://proxy.example.com"


class TestAnthropicTextStreaming:
    @pytest.mark.asyncio
    async def test_streams_text_chunks(self) -> None:
        p = AnthropicProvider(_provider())
        events = [
            _MockAnthropicEvent("content_block_start"),
            _MockAnthropicEvent("content_block_delta",
                delta=_MockAnthropicEvent("text_delta", text="Hello")),
            _MockAnthropicEvent("content_block_delta",
                delta=_MockAnthropicEvent("text_delta", text=" world")),
        ]
        p.client.messages.stream = lambda **kw: _make_anthropic_stream(events, (10, 5))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        texts = [e for e in result if isinstance(e, TextChunk)]
        assert [t.content for t in texts] == ["Hello", " world"]

    @pytest.mark.asyncio
    async def test_emits_done_with_usage(self) -> None:
        p = AnthropicProvider(_provider())
        p.client.messages.stream = lambda **kw: _make_anthropic_stream([], (150, 42))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        done = [e for e in result if isinstance(e, Done)]
        assert len(done) == 1
        assert done[0].input_tokens == 150
        assert done[0].output_tokens == 42

    @pytest.mark.asyncio
    async def test_message_delta_output_tokens_fallback(self) -> None:
        """When get_final_message has no usage, message_delta output_tokens used."""
        p = AnthropicProvider(_provider())
        events = [
            _MockAnthropicEvent("message_delta",
                usage=_MockAnthropicUsage(0, 7)),
        ]
        p.client.messages.stream = lambda **kw: _make_anthropic_stream(events, (0, 0))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        done = [e for e in result if isinstance(e, Done)]
        assert done[0].output_tokens == 7

    @pytest.mark.asyncio
    async def test_includes_system_prompt(self) -> None:
        p = AnthropicProvider(_provider())
        captured_kwargs: dict[str, Any] = {}

        def fake_stream(**kw: Any):
            captured_kwargs.update(kw)
            return _make_anthropic_stream([], (0, 0))
        p.client.messages.stream = fake_stream

        await _collect(p.stream([], [], "You are helpful"))
        assert captured_kwargs["system"] == "You are helpful"

    @pytest.mark.asyncio
    async def test_includes_tools(self) -> None:
        p = AnthropicProvider(_provider())
        captured: dict[str, Any] = {}

        def fake_stream(**kw: Any):
            captured.update(kw)
            return _make_anthropic_stream([], (0, 0))
        p.client.messages.stream = fake_stream

        tools = [{"name": "search", "input_schema": {"type": "object"}}]
        await _collect(p.stream([], tools, ""))
        assert captured["tools"] == tools

    @pytest.mark.asyncio
    async def test_empty_text_delta_not_emitted(self) -> None:
        p = AnthropicProvider(_provider())
        events = [
            _MockAnthropicEvent("content_block_delta",
                delta=_MockAnthropicEvent("text_delta", text="")),
            _MockAnthropicEvent("content_block_delta",
                delta=_MockAnthropicEvent("text_delta", text="foo")),
        ]
        p.client.messages.stream = lambda **kw: _make_anthropic_stream(events, (0, 2))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        texts = [e for e in result if isinstance(e, TextChunk)]
        assert [t.content for t in texts] == ["foo"]


class TestAnthropicToolCalls:
    @pytest.mark.asyncio
    async def test_parses_tool_call_from_json_accumulation(self) -> None:
        p = AnthropicProvider(_provider())
        events = [
            _MockAnthropicEvent("content_block_start",
                content_block=_MockAnthropicEvent("tool_use", id="tc1", name="search",
                    input={"query": ""})),
            _MockAnthropicEvent("content_block_delta",
                delta=_MockAnthropicEvent("input_json_delta", partial_json='{"query":')),
            _MockAnthropicEvent("content_block_delta",
                delta=_MockAnthropicEvent("input_json_delta", partial_json='"cats"')),
            _MockAnthropicEvent("content_block_delta",
                delta=_MockAnthropicEvent("input_json_delta", partial_json="}")),
            _MockAnthropicEvent("content_block_stop"),
        ]
        p.client.messages.stream = lambda **kw: _make_anthropic_stream(events, (50, 30))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        tool_calls = [e for e in result if isinstance(e, ToolCall)]
        assert len(tool_calls) == 1
        assert tool_calls[0].name == "search"
        assert tool_calls[0].id == "tc1"
        assert tool_calls[0].args == {"query": "cats"}

    @pytest.mark.asyncio
    async def test_fallback_args_when_json_parse_fails(self) -> None:
        p = AnthropicProvider(_provider())
        events = [
            _MockAnthropicEvent("content_block_start",
                content_block=_MockAnthropicEvent("tool_use", id="t1", name="calc",
                    input={"expression": ""})),
            _MockAnthropicEvent("content_block_delta",
                delta=_MockAnthropicEvent("input_json_delta", partial_json="not json {{")),
            _MockAnthropicEvent("content_block_stop"),
        ]
        p.client.messages.stream = lambda **kw: _make_anthropic_stream(events, (10, 5))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        tool_calls = [e for e in result if isinstance(e, ToolCall)]
        assert tool_calls[0].args == {"expression": ""}

    @pytest.mark.asyncio
    async def test_non_tool_content_block_ignored(self) -> None:
        p = AnthropicProvider(_provider())
        events = [
            _MockAnthropicEvent("content_block_start",
                content_block=_MockAnthropicEvent("text", text="hello")),
            _MockAnthropicEvent("content_block_stop"),
        ]
        p.client.messages.stream = lambda **kw: _make_anthropic_stream(events, (1, 1))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        tool_calls = [e for e in result if isinstance(e, ToolCall)]
        assert len(tool_calls) == 0


class TestAnthropicRetries:
    @pytest.mark.asyncio
    async def test_retries_on_transient_error_and_succeeds(self) -> None:
        p = AnthropicProvider(LLMConfig(api_key="sk-test", max_retries=2,
            retry_backoff_secs=0.0, retry_backoff_multiplier=1.0))

        attempt = 0

        def fake_stream(**kw: Any) -> Any:
            nonlocal attempt
            attempt += 1
            if attempt < 3:
                raise ConnectionError("transient")
            return _make_anthropic_stream([], (1, 1))
        p.client.messages.stream = fake_stream

        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        assert attempt == 3
        assert any(isinstance(e, Done) for e in result)

    @pytest.mark.asyncio
    async def test_raises_after_max_retries_exhausted(self) -> None:
        p = AnthropicProvider(LLMConfig(api_key="sk-test", max_retries=1,
            retry_backoff_secs=0.0, retry_backoff_multiplier=1.0))
        p.client.messages.stream = lambda **kw: (_ for _ in ()).throw(ConnectionError("fail"))

        with pytest.raises(YomaiLLMError, match="fail"):
            await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))

    @pytest.mark.asyncio
    async def test_rate_limit_error_is_retried(self) -> None:
        p = AnthropicProvider(LLMConfig(api_key="sk-test", max_retries=1,
            retry_backoff_secs=0.0))
        p._anthropic.RateLimitError = type("RateLimitError", (Exception,), {})
        p._anthropic.AuthenticationError = type("AuthenticationError", (Exception,), {})

        attempt = 0

        def fake_stream(**kw: Any) -> Any:
            nonlocal attempt
            attempt += 1
            if attempt < 2:
                raise p._anthropic.RateLimitError("rate limited")
            return _make_anthropic_stream([], (0, 0))
        p.client.messages.stream = fake_stream

        await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        assert attempt == 2

    @pytest.mark.asyncio
    async def test_authentication_error_not_retried(self) -> None:
        p = AnthropicProvider(LLMConfig(api_key="sk-test", max_retries=2,
            retry_backoff_secs=0.0))
        p._anthropic.AuthenticationError = type("AuthenticationError", (Exception,), {})
        p._anthropic.RateLimitError = type("RateLimitError", (Exception,), {})
        p.client.messages.stream = lambda **kw: (_ for _ in ()).throw(
            p._anthropic.AuthenticationError("bad key"))

        with pytest.raises(YomaiLLMError, match="bad key"):
            await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))

    @pytest.mark.asyncio
    async def test_non_transient_error_not_retried(self) -> None:
        p = AnthropicProvider(LLMConfig(api_key="sk-test", max_retries=3,
            retry_backoff_secs=0.0))
        p.client.messages.stream = lambda **kw: (_ for _ in ()).throw(ValueError("bad input"))

        with pytest.raises(YomaiLLMError, match="bad input"):
            await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))


class TestAnthropicSchemaFormatting:
    def test_tool_schemas_wraps_in_input_schema(self) -> None:
        p = AnthropicProvider(_provider())
        result = p.tool_schemas([_simple_tool_fn()])
        assert result[0]["name"] == "search"
        assert "input_schema" in result[0]
        assert result[0]["input_schema"]["type"] == "object"

    def test_tool_result_messages_anthropic_format(self) -> None:
        p = AnthropicProvider(_provider())
        msgs = p.tool_result_messages(
            ToolCall(id="t1", name="search", args={"q": "cats"}), "result text")
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["content"][0]["type"] == "tool_use"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"][0]["type"] == "tool_result"
        assert msgs[1]["content"][0]["content"] == "result text"


# ===========================================================================
# OpenAIProvider tests
# ===========================================================================


class _FakeOpenAIStream:
    """Async generator that yields mock OpenAI chunks."""
    def __init__(self, chunks: list[_MockOpenAIChunk]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> _FakeOpenAIStream:
        return self

    async def __anext__(self) -> _MockOpenAIChunk:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class TestOpenAIProviderConstruction:
    def test_requires_api_key(self) -> None:
        with pytest.raises(YomaiLLMError, match="api_key"):
            OpenAIProvider(LLMConfig(api_key=""))

    def test_stores_model_and_max_tokens(self) -> None:
        p = OpenAIProvider(LLMConfig(api_key="sk-test", model="gpt-4", max_tokens=256))
        assert p.model == "gpt-4"
        assert p.max_tokens == 256

    def test_passes_base_url_to_client(self) -> None:
        p = OpenAIProvider(LLMConfig(api_key="sk-test", base_url="https://api.openai.com/v1"))
        assert str(p.client.base_url).rstrip("/") == "https://api.openai.com/v1"


class TestOpenAITextStreaming:
    @pytest.mark.asyncio
    async def test_streams_text_chunks(self) -> None:
        p = OpenAIProvider(_provider())
        chunks = [
            _MockOpenAIChunk(choices=[
                _MockOpenAIChoice(delta=_MockOpenAIDelta(content="Hello"))]),
            _MockOpenAIChunk(choices=[
                _MockOpenAIChoice(delta=_MockOpenAIDelta(content=" World"))]),
        ]
        stream = _FakeOpenAIStream(chunks)
        p.client.chat.completions.create = _acreate(stream)
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        texts = [e for e in result if isinstance(e, TextChunk)]
        assert [t.content for t in texts] == ["Hello", " World"]

    @pytest.mark.asyncio
    async def test_emits_done_with_usage(self) -> None:
        p = OpenAIProvider(_provider())
        chunks = [
            _MockOpenAIChunk(usage=_MockOpenAIUsage(100, 50)),
        ]
        stream = _FakeOpenAIStream(chunks)
        p.client.chat.completions.create = _acreate(stream)
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        done = [e for e in result if isinstance(e, Done)]
        assert len(done) == 1
        assert done[0].input_tokens == 100
        assert done[0].output_tokens == 50

    @pytest.mark.asyncio
    async def test_no_delta_skipped(self) -> None:
        p = OpenAIProvider(_provider())
        chunks = [
            _MockOpenAIChunk(choices=[
                _MockOpenAIChoice(delta=None, finish_reason="stop")]),
        ]
        stream = _FakeOpenAIStream(chunks)
        p.client.chat.completions.create = _acreate(stream)
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        texts = [e for e in result if isinstance(e, TextChunk)]
        assert len(texts) == 0

    @pytest.mark.asyncio
    async def test_empty_choices_skipped(self) -> None:
        p = OpenAIProvider(_provider())
        chunks = [
            _MockOpenAIChunk(choices=[]),
            _MockOpenAIChunk(choices=[
                _MockOpenAIChoice(delta=_MockOpenAIDelta(content="ok"))]),
        ]
        stream = _FakeOpenAIStream(chunks)
        p.client.chat.completions.create = _acreate(stream)
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        texts = [e for e in result if isinstance(e, TextChunk)]
        assert [t.content for t in texts] == ["ok"]

    @pytest.mark.asyncio
    async def test_prepends_system_message(self) -> None:
        """OpenAI requires system prompt as a message, not a top-level param."""
        p = OpenAIProvider(_provider())
        captured_kwargs: dict[str, Any] = {}

        async def fake_create(**kw: Any):
            captured_kwargs.update(kw)
            return _FakeOpenAIStream([])
        p.client.chat.completions.create = fake_create

        await _collect(p.stream([{"role": "user", "content": "hi"}], [], "be helpful"))
        assert captured_kwargs["messages"][0] == {"role": "system", "content": "be helpful"}


class TestOpenAIToolCalls:
    @pytest.mark.asyncio
    async def test_accumulates_and_yields_tool_calls(self) -> None:
        p = OpenAIProvider(_provider())
        chunks = [
            _MockOpenAIChunk(choices=[
                _MockOpenAIChoice(delta=_MockOpenAIDelta(
                    content=None,
                    tool_calls=[
                        _MockOpenAIToolDelta(index=0, id_="call_1",
                            function=_MockOpenAIFunction(name="search", arguments='{"q":')),
                    ]))]),
            _MockOpenAIChunk(choices=[
                _MockOpenAIChoice(delta=_MockOpenAIDelta(
                    tool_calls=[
                        _MockOpenAIToolDelta(index=0,
                            function=_MockOpenAIFunction(arguments='"cats"}')),
                    ]))]),
            _MockOpenAIChunk(choices=[
                _MockOpenAIChoice(delta=_MockOpenAIDelta(),
                    finish_reason="tool_calls")]),
        ]
        stream = _FakeOpenAIStream(chunks)
        p.client.chat.completions.create = _acreate(stream)
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        calls = [e for e in result if isinstance(e, ToolCall)]
        assert len(calls) == 1
        assert calls[0].name == "search"
        assert calls[0].id == "call_1"
        assert calls[0].args == {"q": "cats"}

    @pytest.mark.asyncio
    async def test_tool_call_emitted_before_finish_reason(self) -> None:
        """Tool calls emitted when finish_reason=='tool_calls', not after stream ends."""
        p = OpenAIProvider(_provider())
        chunks = [
            _MockOpenAIChunk(choices=[
                _MockOpenAIChoice(delta=_MockOpenAIDelta(
                    tool_calls=[
                        _MockOpenAIToolDelta(index=0,
                            function=_MockOpenAIFunction(name="calc")),
                    ]),
                    finish_reason="tool_calls")]),
        ]
        stream = _FakeOpenAIStream(chunks)
        p.client.chat.completions.create = _acreate(stream)
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        calls = [e for e in result if isinstance(e, ToolCall)]
        assert len(calls) == 1
        assert calls[0].name == "calc"

    @pytest.mark.asyncio
    async def test_fallback_json_parse_error_returns_empty_args(self) -> None:
        p = OpenAIProvider(_provider())
        chunks = [
            _MockOpenAIChunk(choices=[
                _MockOpenAIChoice(delta=_MockOpenAIDelta(
                    tool_calls=[
                        _MockOpenAIToolDelta(index=0, id_="c1",
                            function=_MockOpenAIFunction(name="f", arguments="{bad")),
                    ]))]),
            _MockOpenAIChunk(choices=[
                _MockOpenAIChoice(delta=_MockOpenAIDelta(),
                    finish_reason="tool_calls")]),
        ]
        stream = _FakeOpenAIStream(chunks)
        p.client.chat.completions.create = _acreate(stream)
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        calls = [e for e in result if isinstance(e, ToolCall)]
        assert calls[0].args == {}

    @pytest.mark.asyncio
    async def test_multiple_parallel_tool_calls(self) -> None:
        p = OpenAIProvider(_provider())
        chunks = [
            _MockOpenAIChunk(choices=[
                _MockOpenAIChoice(delta=_MockOpenAIDelta(
                    tool_calls=[
                        _MockOpenAIToolDelta(index=0, id_="a",
                            function=_MockOpenAIFunction(name="weather", arguments='{"city":"Paris"}')),
                        _MockOpenAIToolDelta(index=1, id_="b",
                            function=_MockOpenAIFunction(name="time", arguments='{"tz":"UTC"}')),
                    ]),
                    finish_reason="tool_calls")]),
        ]
        stream = _FakeOpenAIStream(chunks)
        p.client.chat.completions.create = _acreate(stream)
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        calls = [e for e in result if isinstance(e, ToolCall)]
        assert len(calls) == 2
        assert calls[0].name == "weather"
        assert calls[1].name == "time"

    @pytest.mark.asyncio
    async def test_tool_streams_max_tokens_when_set(self) -> None:
        p = OpenAIProvider(LLMConfig(api_key="sk-test", max_tokens=100))
        captured: dict[str, Any] = {}
        async def _capture(**kw: Any) -> _FakeOpenAIStream:
            captured.update(kw)
            return _FakeOpenAIStream([])
        p.client.chat.completions.create = _capture
        await _collect(p.stream([], [], ""))
        assert captured.get("max_tokens") == 100


class TestOpenAIRetries:
    @pytest.mark.asyncio
    async def test_retries_on_transient_error_and_succeeds(self) -> None:
        p = OpenAIProvider(LLMConfig(api_key="sk-test", max_retries=2,
            retry_backoff_secs=0.0, retry_backoff_multiplier=1.0))
        attempt = 0

        async def fake_create(**kw: Any) -> _FakeOpenAIStream:
            nonlocal attempt
            attempt += 1
            if attempt < 3:
                raise ConnectionError("transient")
            return _FakeOpenAIStream([])
        p.client.chat.completions.create = fake_create

        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        assert attempt == 3
        assert any(isinstance(e, Done) for e in result)

    @pytest.mark.asyncio
    async def test_raises_after_max_retries_exhausted(self) -> None:
        p = OpenAIProvider(LLMConfig(api_key="sk-test", max_retries=1,
            retry_backoff_secs=0.0))
        p.client.chat.completions.create = _araise(ConnectionError("fail"))

        with pytest.raises(YomaiLLMError, match="fail"):
            await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))

    @pytest.mark.asyncio
    async def test_rate_limit_error_is_retried(self) -> None:
        p = OpenAIProvider(LLMConfig(api_key="sk-test", max_retries=1,
            retry_backoff_secs=0.0))
        p._openai.RateLimitError = type("RateLimitError", (Exception,), {})
        p._openai.AuthenticationError = type("AuthenticationError", (Exception,), {})

        attempt = 0

        async def fake_create(**kw: Any) -> _FakeOpenAIStream:
            nonlocal attempt
            attempt += 1
            if attempt < 2:
                raise p._openai.RateLimitError("too many")
            return _FakeOpenAIStream([])
        p.client.chat.completions.create = fake_create

        await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        assert attempt == 2

    @pytest.mark.asyncio
    async def test_authentication_error_not_retried(self) -> None:
        p = OpenAIProvider(LLMConfig(api_key="sk-test", max_retries=2,
            retry_backoff_secs=0.0))
        p._openai.AuthenticationError = type("AuthenticationError", (Exception,), {})
        p._openai.RateLimitError = type("RateLimitError", (Exception,), {})
        p.client.chat.completions.create = _araise(
            p._openai.AuthenticationError("bad key"))

        with pytest.raises(YomaiLLMError, match="bad key"):
            await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))

    @pytest.mark.asyncio
    async def test_non_transient_error_not_retried(self) -> None:
        p = OpenAIProvider(LLMConfig(api_key="sk-test", max_retries=3,
            retry_backoff_secs=0.0))
        p.client.chat.completions.create = _araise(ValueError("bad"))

        with pytest.raises(YomaiLLMError, match="bad"):
            await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))


class TestOpenAISchemaFormatting:
    def test_tool_schemas_wraps_in_function_key(self) -> None:
        p = OpenAIProvider(_provider())
        result = p.tool_schemas([_simple_tool_fn()])
        assert result[0]["type"] == "function"
        assert "function" in result[0]
        assert result[0]["function"]["name"] == "search"

    def test_tool_result_messages_openai_format(self) -> None:
        p = OpenAIProvider(_provider())
        msgs = p.tool_result_messages(
            ToolCall(id="t1", name="search", args={"q": "cats"}), "results")
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["tool_calls"][0]["function"]["name"] == "search"
        assert msgs[1]["role"] == "tool"
        assert msgs[1]["content"] == "results"


# ===========================================================================
# Anthropic edge cases — null/missing attrs, empty streams, partial JSON
# ===========================================================================


class TestAnthropicEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_stream_emits_done_with_zero_usage(self) -> None:
        p = AnthropicProvider(_provider())
        p.client.messages.stream = lambda **kw: _make_anthropic_stream([], (0, 0))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        done = [e for e in result if isinstance(e, Done)]
        assert len(done) == 1
        assert done[0].input_tokens == 0
        assert done[0].output_tokens == 0

    @pytest.mark.asyncio
    async def test_content_block_start_with_null_content_block(self) -> None:
        p = AnthropicProvider(_provider())
        events = [_MockAnthropicEvent("content_block_start")]  # no content_block attr
        p.client.messages.stream = lambda **kw: _make_anthropic_stream(events, (0, 0))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        calls = [e for e in result if isinstance(e, ToolCall)]
        assert len(calls) == 0  # no crash

    @pytest.mark.asyncio
    async def test_content_block_delta_without_delta(self) -> None:
        p = AnthropicProvider(_provider())
        events = [_MockAnthropicEvent("content_block_delta")]  # no delta attr
        p.client.messages.stream = lambda **kw: _make_anthropic_stream(events, (0, 0))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        assert any(isinstance(e, Done) for e in result)  # no crash

    @pytest.mark.asyncio
    async def test_unknown_event_type_ignored(self) -> None:
        p = AnthropicProvider(_provider())
        events = [
            _MockAnthropicEvent("unknown_event_type"),
            _MockAnthropicEvent("content_block_stop"),
        ]
        p.client.messages.stream = lambda **kw: _make_anthropic_stream(events, (0, 0))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        assert any(isinstance(e, Done) for e in result)  # no crash

    @pytest.mark.asyncio
    async def test_missing_usage_on_final_message(self) -> None:
        p = AnthropicProvider(_provider())
        stream = _MockAnthropicStream([], (0, 0))
        async def _final():
            return _MockAnthropicMessageUsage(None)
        stream.get_final_message = _final
        p.client.messages.stream = lambda **kw: _AsyncCtx(stream)
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        done = [e for e in result if isinstance(e, Done)]
        assert done[0].input_tokens == 0

    @pytest.mark.asyncio
    async def test_tool_input_is_none(self) -> None:
        p = AnthropicProvider(_provider())
        events = [
            _MockAnthropicEvent("content_block_start",
                content_block=_MockAnthropicEvent("tool_use", id="t1", name="f")),
            _MockAnthropicEvent("content_block_stop"),
        ]
        p.client.messages.stream = lambda **kw: _make_anthropic_stream(events, (0, 0))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        calls = [e for e in result if isinstance(e, ToolCall)]
        assert len(calls) == 1
        assert calls[0].args == {}

    @pytest.mark.asyncio
    async def test_message_delta_without_usage(self) -> None:
        p = AnthropicProvider(_provider())
        events = [_MockAnthropicEvent("message_delta")]  # no usage attr
        p.client.messages.stream = lambda **kw: _make_anthropic_stream(events, (10, 0))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        done = [e for e in result if isinstance(e, Done)]
        assert done[0].input_tokens == 10

    @pytest.mark.asyncio
    async def test_partial_json_across_many_deltas(self) -> None:
        p = AnthropicProvider(_provider())
        events = [
            _MockAnthropicEvent("content_block_start",
                content_block=_MockAnthropicEvent("tool_use", id="x", name="calc", input={})),
            _MockAnthropicEvent("content_block_delta",
                delta=_MockAnthropicEvent("input_json_delta", partial_json='{"a":')),
            _MockAnthropicEvent("content_block_delta",
                delta=_MockAnthropicEvent("input_json_delta", partial_json='{"b":')),
            _MockAnthropicEvent("content_block_delta",
                delta=_MockAnthropicEvent("input_json_delta", partial_json='"nested"}}')),
            _MockAnthropicEvent("content_block_stop"),
        ]
        p.client.messages.stream = lambda **kw: _make_anthropic_stream(events, (0, 0))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        calls = [e for e in result if isinstance(e, ToolCall)]
        assert len(calls) == 1
        assert calls[0].args == {"a": {"b": "nested"}}


# ===========================================================================
# OpenAI edge cases — null attrs, empty streams, bad indices, out-of-order
# ===========================================================================


class TestOpenAIEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_stream_emits_done(self) -> None:
        p = OpenAIProvider(_provider())
        p.client.chat.completions.create = _acreate(_FakeOpenAIStream([]))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        done = [e for e in result if isinstance(e, Done)]
        assert len(done) == 1
        assert done[0].input_tokens == 0

    @pytest.mark.asyncio
    async def test_chunk_without_choices(self) -> None:
        p = OpenAIProvider(_provider())
        chunks = [_MockOpenAIChunk()]  # no choices attr set
        p.client.chat.completions.create = _acreate(_FakeOpenAIStream(chunks))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        assert any(isinstance(e, Done) for e in result)  # no crash

    @pytest.mark.asyncio
    async def test_none_delta(self) -> None:
        p = OpenAIProvider(_provider())
        chunks = [_MockOpenAIChunk(choices=[_MockOpenAIChoice(delta=None)])]
        p.client.chat.completions.create = _acreate(_FakeOpenAIStream(chunks))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        texts = [e for e in result if isinstance(e, TextChunk)]
        assert len(texts) == 0

    @pytest.mark.asyncio
    async def test_tool_delta_without_function(self) -> None:
        p = OpenAIProvider(_provider())
        chunks = [
            _MockOpenAIChunk(choices=[
                _MockOpenAIChoice(delta=_MockOpenAIDelta(
                    tool_calls=[_MockOpenAIToolDelta(index=0, id_="c1")],
                )),
            ]),
        ]
        p.client.chat.completions.create = _acreate(_FakeOpenAIStream(chunks))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        calls = [e for e in result if isinstance(e, ToolCall)]
        assert len(calls) == 0  # no name → not emitted

    @pytest.mark.asyncio
    async def test_out_of_order_tool_call_indices(self) -> None:
        p = OpenAIProvider(_provider())
        chunks = [
            _MockOpenAIChunk(choices=[
                _MockOpenAIChoice(delta=_MockOpenAIDelta(
                    tool_calls=[
                        _MockOpenAIToolDelta(index=1, id_="b",
                            function=_MockOpenAIFunction(name="second")),
                        _MockOpenAIToolDelta(index=0, id_="a",
                            function=_MockOpenAIFunction(name="first")),
                    ]),
                    finish_reason="tool_calls")]),
        ]
        p.client.chat.completions.create = _acreate(_FakeOpenAIStream(chunks))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        calls = [e for e in result if isinstance(e, ToolCall)]
        assert len(calls) == 2
        # dict insertion order: index 1 inserted first, values() yields index 1 first
        assert {c.name for c in calls} == {"first", "second"}

    @pytest.mark.asyncio
    async def test_negative_tool_call_index(self) -> None:
        p = OpenAIProvider(_provider())
        chunks = [
            _MockOpenAIChunk(choices=[
                _MockOpenAIChoice(delta=_MockOpenAIDelta(
                    tool_calls=[
                        _MockOpenAIToolDelta(index=-1, id_="neg",
                            function=_MockOpenAIFunction(name="neg")),
                    ]),
                    finish_reason="tool_calls")]),
        ]
        p.client.chat.completions.create = _acreate(_FakeOpenAIStream(chunks))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        calls = [e for e in result if isinstance(e, ToolCall)]
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_finish_reason_stop_with_pending_tools_emits_anyway(self) -> None:
        """Tool calls accumulated during 'stop' finish are NOT emitted (stale deltas)."""
        p = OpenAIProvider(_provider())
        chunks = [
            _MockOpenAIChunk(choices=[
                _MockOpenAIChoice(delta=_MockOpenAIDelta(
                    tool_calls=[
                        _MockOpenAIToolDelta(index=0, id_="c1",
                            function=_MockOpenAIFunction(name="search", arguments='{"q":"x"}')),
                    ]))]),
            _MockOpenAIChunk(choices=[
                _MockOpenAIChoice(delta=None, finish_reason="stop")]),
        ]
        p.client.chat.completions.create = _acreate(_FakeOpenAIStream(chunks))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        calls = [e for e in result if isinstance(e, ToolCall)]
        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_usage_without_prompt_tokens(self) -> None:
        p = OpenAIProvider(_provider())
        chunks = [
            _MockOpenAIChunk(usage={"completion_tokens": 5}),
        ]
        p.client.chat.completions.create = _acreate(_FakeOpenAIStream(chunks))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        done = [e for e in result if isinstance(e, Done)]
        assert done[0].input_tokens == 0
        assert done[0].output_tokens == 0

    @pytest.mark.asyncio
    async def test_tool_call_id_missing_falls_back_to_name(self) -> None:
        p = OpenAIProvider(_provider())
        chunks = [
            _MockOpenAIChunk(choices=[
                _MockOpenAIChoice(delta=_MockOpenAIDelta(
                    tool_calls=[
                        _MockOpenAIToolDelta(index=0,
                            function=_MockOpenAIFunction(name="calc", arguments='{"expr":"1+1"}')),
                    ]),
                    finish_reason="tool_calls")]),
        ]
        p.client.chat.completions.create = _acreate(_FakeOpenAIStream(chunks))
        result = await _collect(p.stream([{"role": "user", "content": "hi"}], [], ""))
        calls = [e for e in result if isinstance(e, ToolCall)]
        assert calls[0].id == "calc"  # id missing → falls back to name


class _MockAnthropicMessageUsage:
    def __init__(self, usage: Any) -> None:
        self.usage = usage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simple_tool_fn():
    fn = lambda x: x  # noqa: E731
    fn.__name__ = "search"
    fn.schema = {"name": "search", "description": "Search the web",
        "properties": {"query": {"type": "string"}}, "required": ["query"]}
    fn.tool_name = "search"
    return fn


def _acreate(result: Any) -> Any:
    """Return an async function that returns *result* when awaited."""

    async def _fn(**kw: Any) -> Any:
        return result

    return _fn


def _araise(exc: BaseException) -> Any:
    """Return an async function that raises *exc* when awaited."""

    async def _fn(**kw: Any) -> Any:
        raise exc

    return _fn
