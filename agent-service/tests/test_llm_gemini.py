"""Tests for agent_service.llm.gemini_client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_service.llm.base import LLMMessage, LLMResponse


class TestGeminiClient:
    """Tests for the GeminiClient implementation."""

    @patch("agent_service.llm.gemini_client.genai")
    def test_init_creates_genai_client(self, mock_genai):
        from agent_service.llm.gemini_client import GeminiClient

        client = GeminiClient(api_key="test-key", model="gemini-1.5-pro")
        mock_genai.Client.assert_called_once_with(api_key="test-key")
        assert client.model_name == "gemini-1.5-pro"

    @patch("agent_service.llm.gemini_client.genai")
    async def test_create_completion_converts_messages(self, mock_genai):
        from agent_service.llm.gemini_client import GeminiClient

        # Set up mock response
        mock_response = MagicMock()
        mock_response.text = "Gemini says hello"
        mock_response.usage_metadata.prompt_token_count = 10
        mock_response.usage_metadata.candidates_token_count = 5
        mock_response.usage_metadata.total_token_count = 15

        mock_genai.Client.return_value.aio.models.generate_content = AsyncMock(
            return_value=mock_response
        )

        client = GeminiClient(api_key="test-key", model="gemini-1.5-pro")
        messages = [
            LLMMessage(role="user", content="Hello"),
            LLMMessage(role="assistant", content="Hi"),
            LLMMessage(role="user", content="How are you?"),
        ]

        result = await client.create_completion(messages, temperature=0.5)

        # Verify the generate_content call
        call_args = mock_genai.Client.return_value.aio.models.generate_content.call_args
        assert call_args.kwargs["model"] == "gemini-1.5-pro"
        contents = call_args.kwargs["contents"]
        assert len(contents) == 3
        assert contents[0]["role"] == "user"
        assert contents[1]["role"] == "model"  # assistant -> model
        assert contents[2]["role"] == "user"

    @patch("agent_service.llm.gemini_client.genai")
    async def test_create_completion_handles_system_messages(self, mock_genai):
        from agent_service.llm.gemini_client import GeminiClient

        mock_response = MagicMock()
        mock_response.text = "response"
        mock_response.usage_metadata.prompt_token_count = 5
        mock_response.usage_metadata.candidates_token_count = 3
        mock_response.usage_metadata.total_token_count = 8

        mock_genai.Client.return_value.aio.models.generate_content = AsyncMock(
            return_value=mock_response
        )

        client = GeminiClient(api_key="test-key", model="gemini-1.5-pro")
        messages = [
            LLMMessage(role="system", content="You are helpful"),
            LLMMessage(role="user", content="Hi"),
        ]

        await client.create_completion(messages)

        call_args = mock_genai.Client.return_value.aio.models.generate_content.call_args
        # The GenerateContentConfig is constructed with system_instruction kwarg
        config_call = mock_genai.types.GenerateContentConfig.call_args
        assert config_call.kwargs["system_instruction"] == "You are helpful"
        # Only user message should be in contents (system is extracted)
        contents = call_args.kwargs["contents"]
        assert len(contents) == 1
        assert contents[0]["role"] == "user"

    @patch("agent_service.llm.gemini_client.genai")
    async def test_create_completion_returns_llm_response(self, mock_genai):
        from agent_service.llm.gemini_client import GeminiClient

        mock_response = MagicMock()
        mock_response.text = "answer"
        mock_response.usage_metadata.prompt_token_count = 12
        mock_response.usage_metadata.candidates_token_count = 8
        mock_response.usage_metadata.total_token_count = 20

        mock_genai.Client.return_value.aio.models.generate_content = AsyncMock(
            return_value=mock_response
        )

        client = GeminiClient(api_key="test-key", model="gemini-1.5-flash")
        messages = [LLMMessage(role="user", content="test")]

        result = await client.create_completion(messages)

        assert isinstance(result, LLMResponse)
        assert result.content == "answer"
        assert result.usage["prompt_tokens"] == 12
        assert result.usage["completion_tokens"] == 8
        assert result.usage["total_tokens"] == 20
        assert result.model == "gemini-1.5-flash"
        assert result.finish_reason == "stop"

    @patch("agent_service.llm.gemini_client.genai")
    def test_get_model_name(self, mock_genai):
        from agent_service.llm.gemini_client import GeminiClient

        client = GeminiClient(api_key="key", model="gemini-2.0-flash")
        assert client.get_model_name() == "gemini-2.0-flash"
