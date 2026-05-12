from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from yomai.config import LLMConfig
from yomai.exceptions import YomaiLLMError
from yomai.llm._retry import _is_transient
from yomai.llm.base import Done, LLMEvent, LLMProvider, Message, TextChunk, ToolCall, ToolSchema
from yomai.tools.registry import ToolFunction, _registry


class OpenAIProvider(LLMProvider):
    def __init__(self, config: LLMConfig) -> None:
        try:
            import openai
        except ImportError as exc:
            raise YomaiLLMError("OpenAI SDK is not installed.", hint="Install yomai with openai support.") from exc
        if not config.api_key:
            raise YomaiLLMError(
                "Missing required config: api_key",
                hint="Pass api_key= to LLMConfig or set an OpenAI API key in your environment.",
                docs="https://yomai.dev/config#api-key",
            )
        self._openai: Any = openai
        self.config = config
        client_kwargs: dict[str, Any] = {"api_key": config.api_key}
        if config.base_url:
            client_kwargs["base_url"] = config.base_url
        self.client: Any = openai.AsyncOpenAI(**client_kwargs)
        self.model = config.model
        self.max_tokens = config.max_tokens

    def tool_schemas(self, tools: list[ToolFunction]) -> list[ToolSchema]:
        return _registry.get_schemas_for_openai(tools)

    def tool_result_messages(self, tool_call: ToolCall, result: str) -> list[Message]:
        return [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {"name": tool_call.name, "arguments": json.dumps(tool_call.args)},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": tool_call.id, "content": result},
        ]

    async def stream(self, messages: list[Message], tools: list[ToolSchema], system: str) -> AsyncIterator[LLMEvent]:
        openai_messages = list(messages)
        if system:
            openai_messages = [{"role": "system", "content": system}, *openai_messages]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
        if self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens

        max_retries = self.config.max_retries
        backoff = self.config.retry_backoff_secs
        multiplier = self.config.retry_backoff_multiplier
        last_exc: BaseException | None = None

        for attempt in range(max_retries + 1):
            tool_parts: dict[int, dict[str, Any]] = {}
            emitted_tool_calls = False
            input_tokens = 0
            output_tokens = 0

            try:
                stream = await self.client.chat.completions.create(**kwargs)
                async for chunk in stream:
                    usage = getattr(chunk, "usage", None)
                    if usage is not None:
                        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                        output_tokens = getattr(usage, "completion_tokens", 0) or 0

                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = getattr(choice, "delta", None)

                    if delta is not None:
                        content = getattr(delta, "content", None)
                        if content:
                            yield TextChunk(str(content))

                        for tool_delta in getattr(delta, "tool_calls", None) or []:
                            index = int(getattr(tool_delta, "index", 0) or 0)
                            entry = tool_parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
                            if getattr(tool_delta, "id", None):
                                entry["id"] = tool_delta.id
                            fn = getattr(tool_delta, "function", None)
                            if fn is not None:
                                if getattr(fn, "name", None):
                                    entry["name"] = fn.name
                                if getattr(fn, "arguments", None):
                                    entry["arguments"] += fn.arguments

                    if getattr(choice, "finish_reason", None) == "tool_calls" and tool_parts:
                        for entry in tool_parts.values():
                            if entry["name"]:
                                try:
                                    args = json.loads(entry["arguments"] or "{}")
                                except json.JSONDecodeError:
                                    args = {}
                                yield ToolCall(id=entry["id"] or entry["name"], name=entry["name"], args=args)
                        emitted_tool_calls = True

                if not emitted_tool_calls:
                    for entry in tool_parts.values():
                        if entry["name"]:
                            try:
                                args = json.loads(entry["arguments"] or "{}")
                            except json.JSONDecodeError:
                                args = {}
                            yield ToolCall(id=entry["id"] or entry["name"], name=entry["name"], args=args)
                yield Done(input_tokens=input_tokens, output_tokens=output_tokens)
                return  # Success

            except (self._openai.RateLimitError, self._openai.AuthenticationError) as exc:
                if isinstance(exc, self._openai.AuthenticationError) or not _is_transient(exc):
                    raise YomaiLLMError(str(exc), docs="https://yomai.dev/llm#openai") from exc
                last_exc = exc
            except Exception as exc:
                if not _is_transient(exc) or attempt >= max_retries:
                    raise YomaiLLMError(str(exc), docs="https://yomai.dev/llm#openai") from exc
                last_exc = exc

            if attempt < max_retries:
                delay = backoff * (multiplier ** attempt)
                await asyncio.sleep(delay)

        if last_exc is not None:
            raise YomaiLLMError(str(last_exc), docs="https://yomai.dev/llm#openai") from last_exc
