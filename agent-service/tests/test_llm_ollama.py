"""Tests for agent_service.llm.ollama_client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agent_service.llm.base import LLMMessage, LLMResponse


class TestOllamaClient:
    """Tests for the OllamaClient implementation."""

    @patch("agent_service.llm.ollama_client.httpx.AsyncClient")
    def test_init_strips_trailing_slash(self, mock_httpx_client):
        from agent_service.llm.ollama_client import OllamaClient

        client = OllamaClient(base_url="http://localhost:11434/", model="llama3.1")
        assert client.base_url == "http://localhost:11434"
        assert client.model == "llama3.1"

    @patch("agent_service.llm.ollama_client.httpx.AsyncClient")
    def test_init_no_trailing_slash(self, mock_httpx_client):
        from agent_service.llm.ollama_client import OllamaClient

        client = OllamaClient(base_url="http://localhost:11434", model="mistral")
        assert client.base_url == "http://localhost:11434"

    @patch("agent_service.llm.ollama_client.httpx.AsyncClient")
    async def test_create_completion_posts_to_correct_url(self, mock_httpx_client):
        from agent_service.llm.ollama_client import OllamaClient

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {"content": "Ollama says hi"},
            "prompt_eval_count": 15,
            "eval_count": 8,
            "done_reason": "stop",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client_instance = MagicMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_httpx_client.return_value = mock_client_instance

        client = OllamaClient(base_url="http://localhost:11434", model="llama3.1")
        messages = [LLMMessage(role="user", content="Hello")]

        result = await client.create_completion(messages, temperature=0.8)

        mock_client_instance.post.assert_awaited_once()
        call_args = mock_client_instance.post.call_args
        assert call_args[0][0] == "http://localhost:11434/api/chat"

        payload = call_args[1]["json"]
        assert payload["model"] == "llama3.1"
        assert payload["stream"] is False
        assert payload["options"]["temperature"] == 0.8
        assert len(payload["messages"]) == 1

    @patch("agent_service.llm.ollama_client.httpx.AsyncClient")
    async def test_create_completion_returns_llm_response(self, mock_httpx_client):
        from agent_service.llm.ollama_client import OllamaClient

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {"content": "response text"},
            "prompt_eval_count": 20,
            "eval_count": 10,
            "done_reason": "stop",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client_instance = MagicMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_httpx_client.return_value = mock_client_instance

        client = OllamaClient(base_url="http://localhost:11434", model="llama3.1")
        result = await client.create_completion(
            [LLMMessage(role="user", content="test")]
        )

        assert isinstance(result, LLMResponse)
        assert result.content == "response text"
        assert result.usage["prompt_tokens"] == 20
        assert result.usage["completion_tokens"] == 10
        assert result.usage["total_tokens"] == 30
        assert result.model == "llama3.1"
        assert result.finish_reason == "stop"

    @patch("agent_service.llm.ollama_client.httpx.AsyncClient")
    def test_get_model_name(self, mock_httpx_client):
        from agent_service.llm.ollama_client import OllamaClient

        client = OllamaClient(base_url="http://localhost:11434", model="codellama")
        assert client.get_model_name() == "codellama"

    @patch("agent_service.llm.ollama_client.httpx.AsyncClient")
    async def test_create_completion_with_max_tokens(self, mock_httpx_client):
        from agent_service.llm.ollama_client import OllamaClient

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {"content": "short"},
            "prompt_eval_count": 5,
            "eval_count": 2,
        }
        mock_response.raise_for_status = MagicMock()

        mock_client_instance = MagicMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_httpx_client.return_value = mock_client_instance

        client = OllamaClient(base_url="http://localhost:11434", model="llama3.1")
        await client.create_completion(
            [LLMMessage(role="user", content="test")],
            max_tokens=50,
        )

        payload = mock_client_instance.post.call_args[1]["json"]
        assert payload["options"]["num_predict"] == 50
