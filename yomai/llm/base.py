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


def _has_multi_modal(messages: list[Message]) -> bool:
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            return True
    return False


def _normalize_image_for_anthropic(block: dict[str, Any]) -> dict[str, Any]:
    bt = block.get("type", "")
    if bt == "image":
        source = block.get("source", {})
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": source.get("media_type", "image/png"),
                "data": source.get("data", ""),
            },
        }
    if bt == "image_url":
        url_source = block.get("image_url", {})
        url = url_source.get("url", "")
        if url.startswith("data:"):
            header, b64 = url.split(",", 1)
            media_type = header.replace("data:", "").replace(";base64", "")
            return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}
        return {"type": "image", "source": {"type": "url", "url": url}}
    return block


def _normalize_image_for_openai(block: dict[str, Any]) -> dict[str, Any]:
    bt = block.get("type", "")
    if bt == "image":
        source = block.get("source", {})
        media_type = source.get("media_type", "image/png")
        b64 = source.get("data", "")
        return {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}}
    if bt == "image_url":
        return block
    return block


def _normalize_document_block(block: dict[str, Any]) -> dict[str, Any]:
    bt = block.get("type", "")
    if bt == "document":
        source = block.get("source", {})
        return {"type": "text", "text": f"[Document: {source.get('media_type', 'unknown')}]"}
    if bt == "document_url":
        url_source = block.get("document_url", {})
        return {"type": "text", "text": f"[Document URL: {url_source.get('url', '')}]"}
    return block


def _normalize_audio_block(block: dict[str, Any]) -> dict[str, Any]:
    bt = block.get("type", "")
    if bt == "input_audio":
        audio = block.get("input_audio", {})
        return {
            "type": "input_audio",
            "input_audio": {"data": audio.get("data", ""), "format": audio.get("format", "wav")},
        }
    return block


class LLMProvider(ABC):
    """Abstract base for LLM streaming providers.

    Implementations stream LLM responses as a sequence of typed events:
    :class:`TextChunk` (incremental text), :class:`ToolCall` (parsed tool
    invocation), and :class:`Done` (usage statistics).

    Providers may optionally implement ``tool_schemas(tools)`` to format
    tool definitions in the provider's native format, and
    ``tool_result_messages(tool_call, result)`` to produce the follow-up
    messages after tool execution.

    Subclasses can override ``_normalize_message_content`` to convert
    Yomai-format content blocks to the provider's native format.
    """

    def _normalize_message_content(self, content: Any) -> Any:
        return content

    def _normalize_messages(self, messages: list[Message]) -> list[Message]:
        return [{**msg, "content": self._normalize_message_content(msg.get("content"))} for msg in messages]

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
