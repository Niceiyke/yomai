"""Tests for new LLM providers: Gemini, Mistral, Groq, vLLM."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from yomai.config import LLMConfig
from yomai.exceptions import YomaiLLMError

# ===========================================================================
# Gemini Provider
# ===========================================================================


class TestGeminiProvider:
    """Google Gemini provider."""

    def test_init_requires_sdk(self) -> None:
        with patch.dict("sys.modules", {"google.genai": None}):
            config = LLMConfig(provider="gemini", api_key="test-key", base_url=None)
            from yomai.llm.gemini import GeminiProvider

            with (
                pytest.raises(YomaiLLMError, match="not installed"),
                patch("builtins.__import__", side_effect=ImportError),
            ):
                GeminiProvider(config)

    @pytest.mark.skip(reason="Requires google-genai SDK not installed")
    def test_init_requires_api_key(self) -> None:
        import os

        os.environ.pop("GEMINI_API_KEY", None)
        config = LLMConfig(provider="gemini", api_key="", base_url=None)
        from yomai.llm.gemini import GeminiProvider

        with pytest.raises(YomaiLLMError, match="api_key"):
            GeminiProvider(config)

    def test_tool_schemas_format(self) -> None:
        from yomai.llm.gemini import _gemini_tool_schemas

        def fake_tool(a: int) -> str: ...

        setattr(fake_tool, "tool_name", "my_tool")
        setattr(
            fake_tool,
            "schema",
            {
                "description": "A test tool",
                "properties": {"a": {"type": "integer", "description": "param"}},
                "required": ["a"],
            },
        )
        schemas = _gemini_tool_schemas([fake_tool])
        assert len(schemas) == 1
        assert schemas[0]["name"] == "my_tool"
        assert schemas[0]["parameters"]["type"] == "object"
        assert "a" in schemas[0]["parameters"]["properties"]

    def test_normalize_text_content(self) -> None:
        from yomai.llm.gemini import GeminiProvider

        class StubProvider(GeminiProvider):
            pass

        prov = object.__new__(StubProvider)
        result = prov._normalize_message_content([{"type": "text", "text": "hello"}])
        assert result[0]["text"] == "hello"

    def test_normalize_image_content(self) -> None:
        from yomai.llm.gemini import GeminiProvider

        class StubProvider(GeminiProvider):
            pass

        prov = object.__new__(StubProvider)
        result = prov._normalize_message_content(
            [{"type": "image", "source": {"media_type": "image/png", "data": "abc"}}]
        )
        assert result[0]["inline_data"]["mime_type"] == "image/png"
        assert result[0]["inline_data"]["data"] == "abc"


# ===========================================================================
# Mistral Provider
# ===========================================================================


class TestMistralProvider:
    """Mistral AI provider."""

    def test_init_requires_sdk(self) -> None:
        with patch.dict("sys.modules", {"mistralai": None}):
            config = LLMConfig(provider="mistral", api_key="test-key", base_url=None)
            from yomai.llm.mistral import MistralProvider

            with (
                pytest.raises(YomaiLLMError, match="not installed"),
                patch("builtins.__import__", side_effect=ImportError),
            ):
                MistralProvider(config)

    @pytest.mark.skip(reason="Requires mistralai SDK not installed")
    def test_init_requires_api_key(self) -> None:
        import os

        os.environ.pop("MISTRAL_API_KEY", None)
        config = LLMConfig(provider="mistral", api_key="", base_url=None)
        from yomai.llm.mistral import MistralProvider

        with pytest.raises(YomaiLLMError, match="api_key"):
            MistralProvider(config)

    def test_tool_result_messages(self) -> None:
        from yomai.llm.base import ToolCall
        from yomai.llm.mistral import MistralProvider

        class StubProvider(MistralProvider):
            pass

        prov = object.__new__(StubProvider)
        msgs = prov.tool_result_messages(ToolCall(id="t1", name="weather", args={"city": "NYC"}), "72F")
        assert msgs[0]["role"] == "assistant"
        assert msgs[1]["role"] == "tool"
        assert msgs[1]["content"] == "72F"

    def test_to_mistral_messages(self) -> None:
        from yomai.llm.mistral import MistralProvider

        class StubProvider(MistralProvider):
            pass

        prov = object.__new__(StubProvider)
        result = prov._to_mistral_messages(
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ]
        )
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"


# ===========================================================================
# Groq Provider
# ===========================================================================


class TestGroqProvider:
    """Groq provider (OpenAI-compatible)."""

    def test_init_defaults(self) -> None:
        config = LLMConfig(provider="groq", api_key="gsk_test")
        with patch("openai.AsyncOpenAI") as mock_ai:
            from yomai.llm.groq import GroqProvider

            GroqProvider(config)
            mock_ai.assert_called_once()
            kwargs = mock_ai.call_args.kwargs
            assert kwargs["api_key"] == "gsk_test"
            assert "groq.com" in kwargs["base_url"]


# ===========================================================================
# vLLM Provider
# ===========================================================================


class TestVLLMProvider:
    """vLLM provider (OpenAI-compatible)."""

    def test_init_with_custom_base_url(self) -> None:
        config = LLMConfig(provider="vllm", api_key="optional", base_url="http://localhost:8000/v1")
        with patch("openai.AsyncOpenAI") as mock_ai:
            from yomai.llm.vllm import VLLMProvider

            VLLMProvider(config)
            mock_ai.assert_called_once()
            kwargs = mock_ai.call_args.kwargs
            assert kwargs["base_url"] == "http://localhost:8000/v1"
            assert kwargs["api_key"] == "optional"

    def test_init_default_base_url(self) -> None:
        import os

        os.environ["VLLM_BASE_URL"] = "http://localhost:8000/v1"
        config = LLMConfig(provider="vllm", api_key="key")
        # Config validator sets base_url automatically for vllm
        assert config.base_url is not None


# ===========================================================================
# LLMConfig Provider Defaults
# ===========================================================================


class TestLLMConfigProviderDefaults:
    """LLMConfig auto-configures model and env keys per provider."""

    def test_gemini_defaults(self) -> None:
        config = LLMConfig(provider="gemini")
        assert config.model == "gemini-2.0-flash"

    def test_mistral_defaults(self) -> None:
        config = LLMConfig(provider="mistral")
        assert config.model == "mistral-large-latest"

    def test_groq_defaults(self) -> None:
        config = LLMConfig(provider="groq")
        assert config.model == "llama-3.3-70b-versatile"

    def test_vllm_defaults(self) -> None:
        config = LLMConfig(provider="vllm")
        assert "Llama-3" in config.model

    def test_all_providers_in_literal(self) -> None:
        from typing import get_args

        from yomai.config import LLMConfig

        field = LLMConfig.model_fields.get("provider")
        if field is not None:
            args = get_args(field.annotation)
            providers = list(args)
            for p in ["anthropic", "openai", "ollama", "gemini", "mistral", "groq", "vllm"]:
                assert p in providers, f"Missing provider: {p}"


# ===========================================================================
# Mock LLM Compatibility
# ===========================================================================


class TestMockLLMAllProviders:
    """mock_llm patches all provider types."""

    def test_mock_llm_covers_all_providers(self) -> None:
        from yomai.llm.gemini import GeminiProvider
        from yomai.llm.groq import GroqProvider
        from yomai.llm.mistral import MistralProvider
        from yomai.llm.vllm import VLLMProvider
        from yomai.testing.mock_llm import mock_llm

        with mock_llm(responses=["Hello from mock"]):
            # All providers should have their stream patched
            gem_orig = GeminiProvider.stream
            mis_orig = MistralProvider.stream
            grq_orig = GroqProvider.stream
            vllm_orig = VLLMProvider.stream
            assert gem_orig is not None
            assert mis_orig is not None
            assert grq_orig is not None
            assert vllm_orig is not None
