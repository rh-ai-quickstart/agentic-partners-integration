"""Tests for kubernetes_agent.main — FastAPI endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with mocked agent config loading."""
    with (
        patch("kubernetes_agent.main.load_agent_config") as mock_load,
        patch("kubernetes_agent.main.get_a2a_app") as mock_a2a,
    ):
        mock_load.return_value = {
            "name": "kubernetes-support",
            "description": "Handles Kubernetes issues",
            "departments": ["kubernetes"],
            "llm_backend": "openai",
            "llm_model": "gpt-4",
            "system_message": "You are a Kubernetes specialist.",
            "a2a": {
                "card_name": "Kubernetes Support Agent",
                "card_description": "K8s support",
                "skills": [],
            },
        }

        from starlette.applications import Starlette

        mock_a2a.return_value = Starlette()

        # Re-import to apply patches at module level
        import importlib

        import kubernetes_agent.main

        importlib.reload(kubernetes_agent.main)

        yield TestClient(kubernetes_agent.main.app)


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "kubernetes-partner-agent"
        assert "version" in data
        assert "timestamp" in data


class TestInvokeEndpoint:
    @patch("kubernetes_agent.main.KubernetesAgent")
    @patch("kubernetes_agent.main.httpx.AsyncClient")
    async def test_invoke_success_with_rag(self, mock_httpx, mock_agent_cls, client):
        mock_agent = MagicMock()
        mock_agent.create_response_with_retry = AsyncMock(
            return_value=("Check pod logs with kubectl logs", False)
        )
        mock_agent_cls.return_value = mock_agent

        mock_rag_response = MagicMock()
        mock_rag_response.status_code = 200
        mock_rag_response.json.return_value = {
            "response": "CrashLoopBackOff usually means...",
            "sources": [
                {
                    "id": "K8S-TICKET-001",
                    "similarity": 0.95,
                    "content": "Pod crash fix",
                },
            ],
        }

        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_rag_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=None)
        mock_httpx.return_value = mock_client_instance

        response = client.post(
            "/api/v1/agents/kubernetes-support/invoke",
            json={
                "session_id": "test-session",
                "user_id": "user@example.com",
                "message": "My pods are crashing",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["agent_id"] == "kubernetes-support"
        assert data["session_id"] == "test-session"
        assert "content" in data

    @patch("kubernetes_agent.main.KubernetesAgent")
    @patch("kubernetes_agent.main.httpx.AsyncClient")
    async def test_invoke_without_rag(self, mock_httpx, mock_agent_cls, client):
        """Agent still works when RAG API is unavailable."""
        mock_agent = MagicMock()
        mock_agent.create_response_with_retry = AsyncMock(
            return_value=("General Kubernetes advice", False)
        )
        mock_agent_cls.return_value = mock_agent

        import httpx as real_httpx

        mock_client_instance = AsyncMock()
        mock_client_instance.post.side_effect = real_httpx.ConnectError(
            "Connection refused"
        )
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=None)
        mock_httpx.return_value = mock_client_instance

        response = client.post(
            "/api/v1/agents/kubernetes-support/invoke",
            json={
                "session_id": "test-session",
                "user_id": "user@example.com",
                "message": "How do I debug a pod?",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["agent_id"] == "kubernetes-support"
        assert data["metadata"]["rag_used"] is False

    def test_invoke_rejects_empty_message(self, client):
        response = client.post(
            "/api/v1/agents/kubernetes-support/invoke",
            json={
                "session_id": "test-session",
                "user_id": "user@example.com",
                "message": "",
            },
        )
        assert response.status_code == 422


class TestAgentCard:
    def test_a2a_card_generation(self, mock_agent_config):
        from kubernetes_agent.a2a.agent_cards import create_agent_card

        card = create_agent_card(
            "kubernetes-support",
            mock_agent_config,
            "http://localhost:8080/a2a/kubernetes-support/",
        )

        assert card.name == "Kubernetes Support Agent"
        assert len(card.skills) == 1
        assert card.skills[0].id == "kubernetes_troubleshooting"
        assert "kubernetes" in card.skills[0].tags
