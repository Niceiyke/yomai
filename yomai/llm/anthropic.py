from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from yomai.config import LLMConfig
from yomai.exceptions import YomaiLLMError
from yomai.llm._retry import _is_transient
from yomai.llm.base import (
    Done,
    LLMEvent,
    LLMProvider,
    Message,
    TextChunk,
    ToolCall,
    ToolSchema,
    _normalize_audio_block,
    _normalize_document_block,
    _normalize_image_for_anthropic,
)
from yomai.tools.registry import ToolFunction, get_schemas_for_anthropic


class AnthropicProvider(LLMProvider):
    def __init__(self, config: LLMConfig) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise YomaiLLMError(
                "Anthropic SDK is not installed.", hint="Install yomai with anthropic support."
            ) from exc

        if not config.api_key:
            raise YomaiLLMError(
                "Missing required config: api_key",
                hint="Set ANTHROPIC_API_KEY or pass api_key= to LLMConfig.",
                docs="https://yomai.dev/config#api-key",
            )
        self._anthropic: Any = anthropic
        self.config = config
        client_kwargs: dict[str, Any] = {"api_key": config.api_key}
        if config.base_url:
            client_kwargs["base_url"] = config.base_url
        self.client: Any = anthropic.AsyncAnthropic(**client_kwargs)
        self.model = config.model
        self.max_tokens = config.max_tokens

    def tool_schemas(self, tools: list[ToolFunction]) -> list[ToolSchema]:
        return get_schemas_for_anthropic(tools)

    def _normalize_message_content(self, content: Any) -> Any:
        if not isinstance(content, list):
            return content
        normalized: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                normalized.append(block)
                continue
            bt = block.get("type", "")
            if bt in ("image", "image_url"):
                normalized.append(_normalize_image_for_anthropic(block))
            elif bt in ("document", "document_url"):
                normalized.append(_normalize_document_block(block))
            elif bt == "input_audio":
                normalized.append(_normalize_audio_block(block))
            else:
                normalized.append(block)
        return normalized

    def tool_result_messages(self, tool_call: ToolCall, result: str) -> list[Message]:
        return [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": tool_call.id, "name": tool_call.name, "input": tool_call.args}],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool_call.id, "content": result}],
            },
        ]

    async def stream(self, messages: list[Message], tools: list[ToolSchema], system: str) -> AsyncIterator[LLMEvent]:
        normalized = self._normalize_messages(messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": normalized,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        max_retries = self.config.max_retries
        backoff = self.config.retry_backoff_secs
        multiplier = self.config.retry_backoff_multiplier
        last_exc: BaseException | None = None

        for attempt in range(max_retries + 1):
            input_tokens = 0
            output_tokens = 0
            current_tool: dict[str, Any] | None = None

            try:
                async with self.client.messages.stream(**kwargs) as stream:
                    async for event in stream:
                        etype = getattr(event, "type", "")

                        if etype == "content_block_start":
                            block = getattr(event, "content_block", None)
                            if getattr(block, "type", None) == "tool_use":
                                current_tool = {
                                    "id": getattr(block, "id", ""),
                                    "name": getattr(block, "name", ""),
                                    "args": getattr(block, "input", {}) or {},
                                    "json": "",
                                }

                        elif etype == "content_block_delta":
                            delta = getattr(event, "delta", None)
                            if getattr(delta, "type", None) == "text_delta":
                                text = getattr(delta, "text", "")
                                if text:
                                    yield TextChunk(text)
                            elif getattr(delta, "type", None) == "input_json_delta" and current_tool is not None:
                                current_tool["json"] += getattr(delta, "partial_json", "") or ""

                        elif etype == "content_block_stop" and current_tool:
                            args = current_tool["args"]
                            if current_tool.get("json"):
                                try:
                                    parsed = json.loads(str(current_tool["json"]))
                                    args = parsed if isinstance(parsed, dict) else {"value": parsed}
                                except json.JSONDecodeError:
                                    args = current_tool["args"]
                            yield ToolCall(
                                id=str(current_tool["id"]),
                                name=str(current_tool["name"]),
                                args=dict(args) if isinstance(args, dict) else {"value": args},
                            )
                            current_tool = None

                        elif etype == "message_delta":
                            usage = getattr(event, "usage", None)
                            if usage:
                                output_tokens = getattr(usage, "output_tokens", output_tokens) or output_tokens

                    final = await stream.get_final_message()
                    usage = getattr(final, "usage", None)
                    if usage:
                        input_tokens = getattr(usage, "input_tokens", 0) or 0
                        output_tokens = getattr(usage, "output_tokens", output_tokens) or output_tokens
                    yield Done(input_tokens=input_tokens, output_tokens=output_tokens)
                    return  # Success — exit retry loop

            except (self._anthropic.RateLimitError, self._anthropic.AuthenticationError) as exc:
                if isinstance(exc, self._anthropic.AuthenticationError) or not _is_transient(exc):
                    raise YomaiLLMError(str(exc), docs="https://yomai.dev/llm#anthropic") from exc
                last_exc = exc
            except Exception as exc:
                if not _is_transient(exc) or attempt >= max_retries:
                    raise YomaiLLMError(str(exc), docs="https://yomai.dev/llm#anthropic") from exc
                last_exc = exc

            if attempt < max_retries:
                delay = backoff * (multiplier**attempt)
                await asyncio.sleep(delay)

        if last_exc is not None:
            raise YomaiLLMError(str(last_exc), docs="https://yomai.dev/llm#anthropic") from last_exc
