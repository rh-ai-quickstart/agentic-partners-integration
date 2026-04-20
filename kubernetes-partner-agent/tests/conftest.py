"""Shared fixtures for kubernetes-partner-agent tests."""

from unittest.mock import AsyncMock

import pytest
from kubernetes_agent.llm.base import BaseLLMClient, LLMResponse


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
    """Sample Kubernetes agent configuration dict."""
    return {
        "name": "kubernetes-support",
        "description": "Handles Kubernetes issues",
        "departments": ["kubernetes"],
        "llm_backend": "openai",
        "llm_model": "gpt-4",
        "system_message": "You are a Kubernetes support specialist.",
        "sampling_params": {
            "strategy": {
                "type": "top_p",
                "temperature": 0.7,
                "top_p": 0.95,
            }
        },
        "a2a": {
            "card_name": "Kubernetes Support Agent",
            "card_description": "Kubernetes support specialist.",
            "skills": [
                {
                    "id": "kubernetes_troubleshooting",
                    "name": "Kubernetes Troubleshooting",
                    "description": "Diagnoses Kubernetes issues.",
                    "tags": ["kubernetes", "pods"],
                    "examples": ["My pods are in CrashLoopBackOff"],
                },
            ],
        },
    }
