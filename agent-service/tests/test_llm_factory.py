"""Tests for agent_service.llm.factory."""

from unittest.mock import MagicMock, patch

import pytest

from agent_service.llm.base import InstrumentedLLMClient
from agent_service.llm.factory import LLMClientFactory, _get_api_key_with_fallback


class TestCreateClient:
    """Tests for LLMClientFactory.create_client()."""

    @patch("agent_service.llm.factory.GeminiClient")
    def test_gemini_backend(self, mock_gemini_cls, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "fake-key")
        client = LLMClientFactory.create_client(
            backend="gemini", model="gemini-1.5-pro"
        )
        mock_gemini_cls.assert_called_once_with(
            api_key="fake-key", model="gemini-1.5-pro"
        )

    @patch("agent_service.llm.factory.OpenAIClient")
    def test_openai_backend(self, mock_openai_cls, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
        client = LLMClientFactory.create_client(backend="openai", model="gpt-4")
        mock_openai_cls.assert_called_once_with(api_key="fake-key", model="gpt-4")

    @patch("agent_service.llm.factory.OllamaClient")
    def test_ollama_backend(self, mock_ollama_cls, monkeypatch):
        # Ollama doesn't require an API key
        client = LLMClientFactory.create_client(backend="ollama", model="llama3.1")
        mock_ollama_cls.assert_called_once()

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown LLM backend"):
            LLMClientFactory.create_client(backend="unknown-backend")

    @patch("agent_service.llm.factory.OpenAIClient")
    def test_instrumentation_wraps_client(self, mock_openai_cls, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
        monkeypatch.setenv("LLM_INSTRUMENTATION", "true")
        client = LLMClientFactory.create_client(backend="openai", model="gpt-4")
        assert isinstance(client, InstrumentedLLMClient)

    @patch("agent_service.llm.factory.OpenAIClient")
    def test_no_instrumentation_by_default(self, mock_openai_cls, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
        monkeypatch.delenv("LLM_INSTRUMENTATION", raising=False)
        client = LLMClientFactory.create_client(backend="openai", model="gpt-4")
        assert not isinstance(client, InstrumentedLLMClient)


class TestCreateGeminiClient:
    """Tests for _create_gemini_client."""

    def test_raises_without_api_key(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
            LLMClientFactory._create_gemini_client()


class TestCreateOpenAIClient:
    """Tests for _create_openai_client."""

    def test_raises_without_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            LLMClientFactory._create_openai_client()


class TestGetApiKeyWithFallback:
    """Tests for _get_api_key_with_fallback resolution order."""

    def test_universal_ai_api_key_takes_priority(self, monkeypatch):
        """Line 35: AI_API_KEY universal key is returned first."""
        monkeypatch.setenv("AI_API_KEY", "universal-key")
        monkeypatch.setenv("AI_OPENAI_API_KEY", "provider-key")
        monkeypatch.setenv("OPENAI_API_KEY", "legacy-key")
        assert _get_api_key_with_fallback("openai") == "universal-key"

    def test_provider_specific_key_when_no_universal(self, monkeypatch):
        """Line 40: AI_{PROVIDER}_API_KEY is returned when AI_API_KEY is absent."""
        monkeypatch.delenv("AI_API_KEY", raising=False)
        monkeypatch.setenv("AI_OPENAI_API_KEY", "provider-specific-key")
        monkeypatch.setenv("OPENAI_API_KEY", "legacy-key")
        assert _get_api_key_with_fallback("openai") == "provider-specific-key"


class TestLLMBackendDeprecationWarning:
    """Test for the LLM_BACKEND deprecation warning in create_client (line 121)."""

    def test_llm_backend_deprecation_warning_logged(self):
        """Line 121: LLM_BACKEND deprecation warning is logged.

        This branch is guarded by ``not backend and os.getenv("LLM_BACKEND")``.
        Reaching it requires os.getenv to return a falsy value for the or-chain
        call (with default) but a truthy value for the if-check call (no default).
        We mock os.getenv to produce this state.
        """
        import os as _os

        original_getenv = _os.getenv

        def fake_getenv(key, *args):
            if key == "AI_PROVIDER":
                return None
            if key == "LLM_BACKEND":
                # or-chain call includes a default arg; if-check call does not
                if args:
                    return ""  # falsy — makes backend = "" after the or-chain
                return "openai"  # truthy — satisfies the if-check
            if key == "LLM_INSTRUMENTATION":
                return ""
            return original_getenv(key, *args)

        with patch("agent_service.llm.factory.os.getenv", side_effect=fake_getenv):
            # backend="" after the or-chain, warning fires, then
            # backend.lower() = "" does not match any known provider
            with pytest.raises(ValueError, match="Unknown LLM backend"):
                LLMClientFactory.create_client()
