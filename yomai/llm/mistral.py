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
    _normalize_document_block,
    _normalize_image_for_openai,
)
from yomai.tools.registry import ToolFunction, get_schemas_for_openai


class MistralProvider(LLMProvider):
    def __init__(self, config: LLMConfig) -> None:
        try:
            from mistralai import Mistral
        except ImportError as exc:
            raise YomaiLLMError(
                "Mistral AI SDK is not installed.",
                hint="Install yomai with mistral support: pip install mistralai",
            ) from exc

        if not config.api_key:
            raise YomaiLLMError(
                "Missing required config: api_key",
                hint="Set MISTRAL_API_KEY or pass api_key= to LLMConfig.",
                docs="https://yomai.dev/config#api-key",
            )
        self._mistral: Any = Mistral
        self.config = config
        self.model = config.model
        self.max_tokens = config.max_tokens
        client_kwargs: dict[str, Any] = {"api_key": config.api_key}
        if config.base_url:
            client_kwargs["server_url"] = config.base_url
        self.client: Any = Mistral(**client_kwargs)

    def tool_schemas(self, tools: list[ToolFunction]) -> list[ToolSchema]:
        return get_schemas_for_openai(tools)

    def tool_result_messages(self, tool_call: ToolCall, result: str) -> list[Message]:
        return [
            {
                "role": "assistant",
                "content": "",
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

    def _normalize_message_content(self, content: Any) -> Any:
        if not isinstance(content, list):
            return content
        normalized: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                normalized.append(block)
                continue
            bt = block.get("type", "")
            if bt == "image":
                normalized.append(_normalize_image_for_openai(block))
            elif bt in ("document", "document_url"):
                normalized.append(_normalize_document_block(block))
            else:
                normalized.append(block)
        return normalized

    def _to_mistral_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "assistant":
                result.append({"role": "assistant", "content": content})
            elif role == "tool":
                result.append({"role": "tool", "content": content, "tool_call_id": msg.get("tool_call_id", "")})
            else:
                result.append({"role": "user", "content": content})
        return result

    async def stream(self, messages: list[Message], tools: list[ToolSchema], system: str) -> AsyncIterator[LLMEvent]:
        normalized = self._normalize_messages(messages)
        mistral_msgs = self._to_mistral_messages(normalized)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": mistral_msgs,
        }
        if system:
            kwargs["messages"] = [{"role": "system", "content": system}] + kwargs["messages"]
        if tools:
            kwargs["tools"] = tools
        if self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens

        max_retries = self.config.max_retries
        backoff = self.config.retry_backoff_secs
        multiplier = self.config.retry_backoff_multiplier
        last_exc: BaseException | None = None

        for attempt in range(max_retries + 1):
            input_tokens = 0
            output_tokens = 0
            tool_parts: dict[int, dict[str, Any]] = {}
            emitted_tool_calls = False

            try:
                stream_response: Any = await self.client.chat.stream_async(**kwargs)
                async for chunk in stream_response:
                    choices = getattr(chunk.data, "choices", []) if hasattr(chunk, "data") else []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = getattr(choice, "delta", None)
                    finish_reason = getattr(choice, "finish_reason", None)

                    if delta is not None:
                        text = getattr(delta, "content", None)
                        if text:
                            yield TextChunk(str(text))

                        for tool_delta in getattr(delta, "tool_calls", None) or []:
                            index = int(getattr(tool_delta, "index", 0) or 0)
                            entry = tool_parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
                            tid = getattr(tool_delta, "id", None)
                            if tid:
                                entry["id"] = str(tid)
                            fn = getattr(tool_delta, "function", None)
                            if fn is not None:
                                if getattr(fn, "name", None):
                                    entry["name"] = str(fn.name)
                                if getattr(fn, "arguments", None):
                                    entry["arguments"] += str(fn.arguments)

                    if finish_reason == "tool_calls" and tool_parts:
                        for entry in tool_parts.values():
                            if entry["name"]:
                                try:
                                    args = json.loads(entry["arguments"] or "{}")
                                except json.JSONDecodeError:
                                    args = {}
                                yield ToolCall(id=entry["id"] or entry["name"], name=entry["name"], args=args)
                        emitted_tool_calls = True
                        tool_parts.clear()

                    usage = getattr(chunk.data, "usage", None) if hasattr(chunk, "data") else None
                    if usage:
                        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                        output_tokens = getattr(usage, "completion_tokens", 0) or 0

                if not emitted_tool_calls and tool_parts:
                    for entry in tool_parts.values():
                        if entry["name"]:
                            try:
                                args = json.loads(entry["arguments"] or "{}")
                            except json.JSONDecodeError:
                                args = {}
                            yield ToolCall(id=entry["id"] or entry["name"], name=entry["name"], args=args)

                yield Done(input_tokens=input_tokens, output_tokens=output_tokens)
                return

            except Exception as exc:
                err_str = str(exc).lower()
                if "rate" in err_str or "429" in err_str or _is_transient(exc):
                    if attempt < max_retries:
                        last_exc = exc
                        delay = backoff * (multiplier**attempt)
                        await asyncio.sleep(delay)
                        continue
                raise YomaiLLMError(str(exc), docs="https://yomai.dev/llm#mistral") from exc

        if last_exc is not None:
            raise YomaiLLMError(str(last_exc), docs="https://yomai.dev/llm#mistral") from last_exc
