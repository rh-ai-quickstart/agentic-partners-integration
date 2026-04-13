"""Tests for agent_service.llm.openai_client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_service.llm.base import LLMMessage, LLMResponse


class TestOpenAIClient:
    """Tests for the OpenAIClient implementation."""

    @patch("agent_service.llm.openai_client.AsyncOpenAI")
    def test_init(self, mock_async_openai):
        from agent_service.llm.openai_client import OpenAIClient

        client = OpenAIClient(api_key="test-key", model="gpt-4")
        mock_async_openai.assert_called_once_with(api_key="test-key")
        assert client.model == "gpt-4"

    @patch("agent_service.llm.openai_client.AsyncOpenAI")
    async def test_create_completion_calls_api(self, mock_async_openai):
        from agent_service.llm.openai_client import OpenAIClient

        # Build mock response
        mock_choice = MagicMock()
        mock_choice.message.content = "OpenAI says hello"
        mock_choice.finish_reason = "stop"

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 5
        mock_usage.total_tokens = 15

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage
        mock_response.model = "gpt-4-0613"

        mock_async_openai.return_value.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        client = OpenAIClient(api_key="test-key", model="gpt-4")
        messages = [
            LLMMessage(role="system", content="Be helpful"),
            LLMMessage(role="user", content="Hello"),
        ]

        result = await client.create_completion(messages, temperature=0.3, max_tokens=100)

        # Verify API call
        mock_async_openai.return_value.chat.completions.create.assert_awaited_once()
        call_kwargs = mock_async_openai.return_value.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4"
        assert call_kwargs["temperature"] == 0.3
        assert call_kwargs["max_tokens"] == 100
        assert len(call_kwargs["messages"]) == 2

    @patch("agent_service.llm.openai_client.AsyncOpenAI")
    async def test_create_completion_returns_llm_response(self, mock_async_openai):
        from agent_service.llm.openai_client import OpenAIClient

        mock_choice = MagicMock()
        mock_choice.message.content = "response text"
        mock_choice.finish_reason = "stop"

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 20
        mock_usage.completion_tokens = 10
        mock_usage.total_tokens = 30

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage
        mock_response.model = "gpt-4"

        mock_async_openai.return_value.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        client = OpenAIClient(api_key="key", model="gpt-4")
        messages = [LLMMessage(role="user", content="test")]

        result = await client.create_completion(messages)

        assert isinstance(result, LLMResponse)
        assert result.content == "response text"
        assert result.usage["prompt_tokens"] == 20
        assert result.usage["completion_tokens"] == 10
        assert result.usage["total_tokens"] == 30
        assert result.model == "gpt-4"
        assert result.finish_reason == "stop"

    @patch("agent_service.llm.openai_client.AsyncOpenAI")
    def test_get_model_name(self, mock_async_openai):
        from agent_service.llm.openai_client import OpenAIClient

        client = OpenAIClient(api_key="key", model="gpt-3.5-turbo")
        assert client.get_model_name() == "gpt-3.5-turbo"

    @patch("agent_service.llm.openai_client.AsyncOpenAI")
    async def test_create_completion_no_usage(self, mock_async_openai):
        """When usage is None, tokens should default to 0."""
        from agent_service.llm.openai_client import OpenAIClient

        mock_choice = MagicMock()
        mock_choice.message.content = "response"
        mock_choice.finish_reason = "stop"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = None
        mock_response.model = "gpt-4"

        mock_async_openai.return_value.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        client = OpenAIClient(api_key="key", model="gpt-4")
        result = await client.create_completion([LLMMessage(role="user", content="x")])
        assert result.usage["prompt_tokens"] == 0
        assert result.usage["completion_tokens"] == 0
        assert result.usage["total_tokens"] == 0
