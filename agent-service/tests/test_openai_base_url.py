"""Tests for OpenAI base_url pass-through (LiteLLM proxy support).

Validates that:
- OpenAIClient forwards base_url to AsyncOpenAI when provided
- OpenAIClient omits base_url from AsyncOpenAI kwargs when None or empty
- LLMClientFactory reads OPENAI_BASE_URL env var and passes it through
"""

from unittest.mock import patch

from agent_service.llm.factory import LLMClientFactory


class TestOpenAIClientBaseUrl:
    """Tests for OpenAIClient.__init__ base_url handling."""

    @patch("agent_service.llm.openai_client.AsyncOpenAI")
    def test_openai_client_default_no_base_url(self, mock_async_openai):
        """When base_url is None, AsyncOpenAI is called without base_url kwarg."""
        from agent_service.llm.openai_client import OpenAIClient

        OpenAIClient(api_key="test-key", model="gpt-4")

        mock_async_openai.assert_called_once_with(api_key="test-key")
        # Confirm base_url was NOT in the kwargs
        call_kwargs = mock_async_openai.call_args.kwargs
        assert "base_url" not in call_kwargs

    @patch("agent_service.llm.openai_client.AsyncOpenAI")
    def test_openai_client_with_base_url(self, mock_async_openai):
        """When base_url is provided, AsyncOpenAI is called with that base_url."""
        from agent_service.llm.openai_client import OpenAIClient

        OpenAIClient(
            api_key="test-key",
            model="gpt-4",
            base_url="http://litellm:4000",
        )

        mock_async_openai.assert_called_once_with(
            api_key="test-key",
            base_url="http://litellm:4000",
        )

    @patch("agent_service.llm.openai_client.AsyncOpenAI")
    def test_openai_client_base_url_empty_string_treated_as_none(
        self, mock_async_openai
    ):
        """When base_url is empty string, it is not passed to AsyncOpenAI."""
        from agent_service.llm.openai_client import OpenAIClient

        OpenAIClient(api_key="test-key", model="gpt-4", base_url="")

        mock_async_openai.assert_called_once_with(api_key="test-key")
        call_kwargs = mock_async_openai.call_args.kwargs
        assert "base_url" not in call_kwargs


class TestFactoryBaseUrl:
    """Tests for LLMClientFactory OPENAI_BASE_URL env var handling."""

    @patch("agent_service.llm.factory.OpenAIClient")
    def test_factory_reads_openai_base_url_env(self, mock_openai_cls, monkeypatch):
        """With OPENAI_BASE_URL env var set, factory passes it to OpenAIClient."""
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
        monkeypatch.setenv("OPENAI_BASE_URL", "http://litellm:4000")

        LLMClientFactory.create_client(backend="openai", model="gpt-4")

        mock_openai_cls.assert_called_once_with(
            api_key="fake-key",
            model="gpt-4",
            base_url="http://litellm:4000",
        )

    @patch("agent_service.llm.factory.OpenAIClient")
    def test_factory_no_base_url_when_unset(self, mock_openai_cls, monkeypatch):
        """Without OPENAI_BASE_URL env var, factory passes base_url=None."""
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

        LLMClientFactory.create_client(backend="openai", model="gpt-4")

        mock_openai_cls.assert_called_once_with(
            api_key="fake-key",
            model="gpt-4",
            base_url=None,
        )
