"""Tests for multi-modal input (images, audio, documents) through AgentRequest."""

from __future__ import annotations

from typing import Any


class TestMultiModalContent:
    """Content block types and normalization."""

    def test_text_content_block(self) -> None:
        from yomai.core.schemas import AgentRequest

        req = AgentRequest(message=[{"type": "text", "text": "Hello world"}])
        assert req.message_text == "Hello world"

    def test_image_url_content_block(self) -> None:
        from yomai.core.schemas import AgentRequest

        req = AgentRequest(
            message=[
                {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
                {"type": "text", "text": "Describe this image"},
            ]
        )
        assert req.message_text == "Describe this image"

    def test_image_base64_content_block(self) -> None:
        from yomai.core.schemas import AgentRequest

        req = AgentRequest(
            message=[
                {"type": "image", "source": {"media_type": "image/png", "data": "base64..."}},
            ]
        )
        assert req.message_text == "[multi-modal]"

    def test_audio_content_block(self) -> None:
        from yomai.core.schemas import AgentRequest

        req = AgentRequest(
            message=[
                {"type": "input_audio", "input_audio": {"data": "base64...", "format": "wav"}},
                {"type": "text", "text": "Transcribe this"},
            ]
        )
        assert req.message_text == "Transcribe this"

    def test_document_url_content_block(self) -> None:
        from yomai.core.schemas import AgentRequest

        req = AgentRequest(
            message=[
                {"type": "document_url", "document_url": {"url": "https://example.com/doc.pdf"}},
                {"type": "text", "text": "Summarize"},
            ]
        )
        assert req.message_text == "Summarize"

    def test_document_base64_content_block(self) -> None:
        from yomai.core.schemas import AgentRequest

        req = AgentRequest(
            message=[
                {"type": "document", "source": {"media_type": "application/pdf", "data": "..."}},
            ]
        )
        assert req.message_text == "[multi-modal]"

    def test_content_block_type_classifier(self) -> None:
        from yomai.core.schemas import AgentRequest

        assert AgentRequest.content_block_type({"type": "text"}) == "text"
        assert AgentRequest.content_block_type({"type": "image_url"}) == "image"
        assert AgentRequest.content_block_type({"type": "image"}) == "image"
        assert AgentRequest.content_block_type({"type": "input_audio"}) == "audio"
        assert AgentRequest.content_block_type({"type": "document_url"}) == "document"
        assert AgentRequest.content_block_type({"type": "document"}) == "document"
        assert AgentRequest.content_block_type({"type": "unknown"}) == "unknown"

    def test_pydantic_content_models(self) -> None:
        from yomai.core.schemas import AudioInputContent, AudioInputSource, ImageUrlContent, ImageUrlSource, TextContent

        text = TextContent(text="Hello")
        assert text.text == "Hello"
        assert text.type == "text"

        img = ImageUrlContent(image_url=ImageUrlSource(url="http://x", detail="low"))
        assert img.image_url.url == "http://x"
        assert img.image_url.detail == "low"

        audio = AudioInputContent(input_audio=AudioInputSource(data="aaa", format="mp3"))
        assert audio.input_audio.data == "aaa"
        assert audio.input_audio.format == "mp3"


class TestProviderNormalization:
    """Multi-modal content normalization across providers."""

    def test_normalize_image_anthropic(self) -> None:
        from yomai.llm.base import _normalize_image_for_anthropic

        openai_format = {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}}
        result = _normalize_image_for_anthropic(openai_format)
        assert result["type"] == "image"
        assert result["source"]["type"] == "base64"
        assert result["source"]["media_type"] == "image/png"
        assert result["source"]["data"] == "abc123"

    def test_normalize_image_openai(self) -> None:
        from yomai.llm.base import _normalize_image_for_openai

        anthropic_format = {"type": "image", "source": {"media_type": "image/png", "data": "abc123"}}
        result = _normalize_image_for_openai(anthropic_format)
        assert result["type"] == "image_url"
        assert result["image_url"]["url"] == "data:image/png;base64,abc123"

    def test_normalize_image_openai_passthrough(self) -> None:
        from yomai.llm.base import _normalize_image_for_openai

        openai_format = {"type": "image_url", "image_url": {"url": "https://x/img.png"}}
        result = _normalize_image_for_openai(openai_format)
        assert result["type"] == "image_url"
        assert result["image_url"]["url"] == "https://x/img.png"

    def test_normalize_document_block(self) -> None:
        from yomai.llm.base import _normalize_document_block

        result = _normalize_document_block(
            {"type": "document", "source": {"media_type": "application/pdf", "data": "..."}}
        )
        assert result["type"] == "text"
        assert "application/pdf" in result["text"]

    def test_normalize_document_url_block(self) -> None:
        from yomai.llm.base import _normalize_document_block

        result = _normalize_document_block({"type": "document_url", "document_url": {"url": "https://x/doc.pdf"}})
        assert result["type"] == "text"
        assert "https://x/doc.pdf" in result["text"]

    def test_normalize_audio_block(self) -> None:
        from yomai.llm.base import _normalize_audio_block

        result = _normalize_audio_block({"type": "input_audio", "input_audio": {"data": "abc", "format": "mp3"}})
        assert result["type"] == "input_audio"
        assert result["input_audio"]["data"] == "abc"
        assert result["input_audio"]["format"] == "mp3"

    def test_has_multi_modal_detection(self) -> None:
        from yomai.llm.base import _has_multi_modal

        assert not _has_multi_modal([{"role": "user", "content": "hello"}])
        assert _has_multi_modal([{"role": "user", "content": [{"type": "text", "text": "hi"}]}])

    def test_provider_normalize_messages(self) -> None:
        from yomai.llm.base import LLMProvider, _normalize_image_for_openai

        class TestProv(LLMProvider):
            def _normalize_message_content(self, content: Any) -> Any:
                if not isinstance(content, list):
                    return content
                result: list[dict[str, Any]] = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "image":
                        result.append(_normalize_image_for_openai(block))
                    else:
                        result.append(block)
                return result

            async def stream(self, messages: list, tools: list, system: str) -> Any:  # pyright: ignore[reportIncompatibleMethodOverride]
                yield None  # type: ignore[misc]

        provider = TestProv()
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "image", "source": {"media_type": "image/png", "data": "abc"}},
                ],
            }
        ]
        normalized = provider._normalize_messages(msgs)
        assert normalized[0]["content"][0]["type"] == "text"
        assert normalized[0]["content"][1]["type"] == "image_url"


class TestMessageTextHelper:
    """Tests for _message_text in agent module."""

    def test_plain_string(self) -> None:
        from yomai.core.agent import _message_text

        assert _message_text("hello") == "hello"

    def test_image_block_included(self) -> None:
        from yomai.core.agent import _message_text

        result = _message_text([{"type": "text", "text": "hi"}, {"type": "image_url"}])
        assert "[image]" in result

    def test_audio_block_included(self) -> None:
        from yomai.core.agent import _message_text

        result = _message_text([{"type": "text", "text": "hi"}, {"type": "input_audio"}])
        assert "[audio]" in result

    def test_document_block_included(self) -> None:
        from yomai.core.agent import _message_text

        result = _message_text([{"type": "text", "text": "hi"}, {"type": "document_url"}])
        assert "[document]" in result

    def test_no_text_returns_placeholder(self) -> None:
        from yomai.core.agent import _message_text

        result = _message_text([{"type": "image_url"}])
        assert result == "[image]"
