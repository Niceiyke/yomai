from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from yomai.config import LLMConfig
from yomai.exceptions import YomaiLLMError
from yomai.llm._retry import _is_transient
from yomai.llm.base import Done, LLMEvent, LLMProvider, Message, TextChunk, ToolCall, ToolSchema
from yomai.tools.registry import ToolFunction


def _gemini_tool_schemas(tools: list[ToolFunction]) -> list[ToolSchema]:
    schemas: list[ToolSchema] = []
    for fn in tools:
        schema = getattr(fn, "schema", None)
        if not isinstance(schema, dict):
            continue
        properties = schema.get("properties", {})
        schemas.append(
            {
                "name": getattr(fn, "tool_name", fn.__name__),
                "description": schema.get("description", ""),
                "parameters": {
                    "type": "object",
                    "properties": {k: {sk: sv for sk, sv in v.items() if sk != "title"} for k, v in properties.items()},
                    "required": schema.get("required", []),
                },
            }
        )
    return schemas


class GeminiProvider(LLMProvider):
    def __init__(self, config: LLMConfig) -> None:
        try:
            from google import genai
        except ImportError as exc:
            raise YomaiLLMError(
                "Google GenAI SDK is not installed.",
                hint="Install yomai with gemini support: pip install google-genai",
            ) from exc

        if not config.api_key:
            raise YomaiLLMError(
                "Missing required config: api_key",
                hint="Set GEMINI_API_KEY or pass api_key= to LLMConfig.",
                docs="https://yomai.dev/config#api-key",
            )
        self._genai: Any = genai
        self.config = config
        self.model = config.model
        self.max_tokens = config.max_tokens
        self.client: Any = genai.Client(api_key=config.api_key)

    def tool_schemas(self, tools: list[ToolFunction]) -> list[ToolSchema]:
        return _gemini_tool_schemas(tools)

    def tool_result_messages(self, tool_call: ToolCall, result: str) -> list[Message]:
        return [
            {"role": "model", "parts": [{"function_call": {"name": tool_call.name, "args": tool_call.args}}]},
            {
                "role": "user",
                "parts": [{"function_response": {"name": tool_call.name, "response": {"result": result}}}],
            },
        ]

    def _gemini_tools(self, tools: list[ToolSchema]) -> dict[str, Any] | None:
        if not tools:
            return None
        declarations = []
        for tool in tools:
            declarations.append(
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {"type": "object", "properties": {}, "required": []}),
                }
            )
        return {"function_declarations": declarations}

    def _normalize_message_content(self, content: Any) -> Any:
        if not isinstance(content, list):
            return content
        parts: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            bt = block.get("type", "")
            if bt == "text":
                parts.append({"text": block.get("text", "")})
            elif bt == "image_url":
                img = block.get("image_url", {})
                url = img.get("url", "")
                if url.startswith("data:"):
                    header, b64 = url.split(",", 1)
                    mime_type = header.replace("data:", "").replace(";base64", "")
                    parts.append({"inline_data": {"mime_type": mime_type, "data": b64}})
                else:
                    parts.append({"inline_data": {"mime_type": "image/png", "data": url}})
            elif bt == "image":
                source = block.get("source", {})
                parts.append(
                    {
                        "inline_data": {
                            "mime_type": source.get("media_type", "image/png"),
                            "data": source.get("data", ""),
                        }
                    }
                )
            elif bt == "input_audio":
                audio = block.get("input_audio", {})
                parts.append(
                    {"inline_data": {"mime_type": f"audio/{audio.get('format', 'wav')}", "data": audio.get("data", "")}}
                )
            elif bt in ("document", "document_url"):
                parts.append(
                    {
                        "text": block.get("document_url", block.get("source", {})).get("url", "[document]")
                        if bt == "document_url"
                        else "[document]"
                    }
                )
            else:
                parts.append(block)
        return parts

    async def stream(self, messages: list[Message], tools: list[ToolSchema], system: str) -> AsyncIterator[LLMEvent]:
        normalized = self._normalize_messages(messages)

        contents: list[dict[str, Any]] = []
        for msg in normalized:
            role = "user" if msg.get("role") == "user" else "model"
            content = msg.get("content")
            if isinstance(content, str):
                parts = [{"text": content}]
            elif isinstance(content, list):
                parts = content
            else:
                parts = [{"text": str(content)}]
            contents.append({"role": role, "parts": parts})

        config_kwargs: dict[str, Any] = {}
        if system:
            config_kwargs["system_instruction"] = system
        if self.max_tokens:
            config_kwargs["max_output_tokens"] = self.max_tokens
        tool_config = self._gemini_tools(tools)
        if tool_config:
            config_kwargs["tools"] = [tool_config]

        max_retries = self.config.max_retries
        backoff = self.config.retry_backoff_secs
        multiplier = self.config.retry_backoff_multiplier
        last_exc: BaseException | None = None

        for attempt in range(max_retries + 1):
            input_tokens = 0
            output_tokens = 0

            try:
                response: Any = await self.client.aio.models.generate_content_stream(
                    model=self.model,
                    contents=contents,
                    config=config_kwargs if config_kwargs else None,
                )
                async for chunk in response:
                    try:
                        usage = getattr(chunk, "usage_metadata", None)
                        if usage:
                            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
                            output_tokens = getattr(usage, "candidates_token_count", 0) or 0

                        candidates = getattr(chunk, "candidates", None) or []
                        for candidate in candidates:
                            content = getattr(candidate, "content", None)
                            if content is None:
                                continue
                            for part in getattr(content, "parts", None) or []:
                                if hasattr(part, "text") and part.text:
                                    yield TextChunk(str(part.text))
                                if hasattr(part, "function_call"):
                                    fc = part.function_call
                                    args = dict(getattr(fc, "args", {}) or {})
                                    yield ToolCall(
                                        id=getattr(fc, "id", "") or getattr(fc, "name", ""),
                                        name=str(getattr(fc, "name", "")),
                                        args=args,
                                    )
                    except Exception:
                        continue

                yield Done(input_tokens=input_tokens, output_tokens=output_tokens)
                return

            except Exception as exc:
                err_str = str(exc).lower()
                if ("429" in err_str or "resource_exhausted" in err_str or _is_transient(exc)) and attempt < max_retries:
                    last_exc = exc
                    delay = backoff * (multiplier**attempt)
                    await asyncio.sleep(delay)
                    continue
                raise YomaiLLMError(str(exc), docs="https://yomai.dev/llm#gemini") from exc

        if last_exc is not None:
            raise YomaiLLMError(str(last_exc), docs="https://yomai.dev/llm#gemini") from last_exc
