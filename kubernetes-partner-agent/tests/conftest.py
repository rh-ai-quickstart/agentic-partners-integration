"""Shared fixtures for kubernetes-partner-agent tests."""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_agent_config():
    """Sample Kubernetes agent configuration dict."""
    return {
        "name": "kubernetes-support",
        "description": "Handles Kubernetes issues",
        "departments": ["kubernetes"],
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
