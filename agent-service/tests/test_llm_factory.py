"""Tests for agent_service.llm.factory."""

from unittest.mock import MagicMock, patch

import pytest

from agent_service.llm.base import InstrumentedLLMClient
from agent_service.llm.factory import LLMClientFactory


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
