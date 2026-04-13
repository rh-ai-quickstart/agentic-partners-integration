"""Shared fixtures for agent-service tests."""

from unittest.mock import AsyncMock

import pytest

from agent_service.llm.base import BaseLLMClient, LLMResponse


@pytest.fixture
def mock_llm_client():
    """AsyncMock of BaseLLMClient with a default create_completion return."""
    client = AsyncMock(spec=BaseLLMClient)
    client.create_completion.return_value = LLMResponse(
        content="Test response",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        model="test-model",
        finish_reason="stop",
    )
    client.get_model_name.return_value = "test-model"
    return client


@pytest.fixture
def mock_agent_config():
    """Sample agent configuration dict."""
    return {
        "name": "test-agent",
        "llm_backend": "openai",
        "llm_model": "gpt-4",
        "system_message": "You are a helpful test agent.",
        "sampling_params": {
            "strategy": {
                "temperature": 0.5,
            }
        },
    }


@pytest.fixture
def mock_global_config():
    """Sample global configuration dict."""
    return {
        "llm_backend": "openai",
        "llm_model": "gpt-4",
    }
