"""Tests for aro_agent.main — FastAPI endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from aro_agent.main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestHealthEndpoint:
    @patch("aro_agent.main.load_agent_config")
    def test_health_returns_200(self, mock_load, client):
        mock_load.return_value = {"mcp_servers": []}
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "aro-partner-agent"
        assert "version" in data
        assert "timestamp" in data


class TestInvokeEndpoint:
    @patch("aro_agent.main.AROAgent")
    def test_invoke_success(self, mock_agent_cls, client):
        mock_agent = MagicMock()
        mock_agent.create_response_with_retry = AsyncMock(
            return_value=("Check your resource limits", False, [{"tool": "search", "arguments": {}, "result_preview": "data"}])
        )
        mock_agent.mcp_server_url = "http://mcp:8080/sse"
        mock_agent_cls.return_value = mock_agent

        response = client.post(
            "/api/v1/agents/aro-support/invoke",
            json={
                "session_id": "test-session",
                "user_id": "user@example.com",
                "message": "Pods are OOMKilled",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["agent_id"] == "aro-support"
        assert data["session_id"] == "test-session"
        assert data["content"] == "Check your resource limits"
        assert data["metadata"]["mcp_enabled"] is True

    @patch("aro_agent.main.AROAgent")
    def test_invoke_with_conversation_history(self, mock_agent_cls, client):
        mock_agent = MagicMock()
        mock_agent.create_response_with_retry = AsyncMock(
            return_value=("Follow-up response", False, [])
        )
        mock_agent.mcp_server_url = None
        mock_agent_cls.return_value = mock_agent

        response = client.post(
            "/api/v1/agents/aro-support/invoke",
            json={
                "session_id": "test-session",
                "user_id": "user@example.com",
                "message": "What about the second pod?",
                "transfer_context": {
                    "conversation_history": [
                        {"role": "user", "content": "My pods are crashing"},
                        {"role": "assistant", "content": "Let me check..."},
                    ]
                },
            },
        )

        assert response.status_code == 200
        assert response.json()["metadata"]["mcp_enabled"] is False

    def test_invoke_rejects_empty_message(self, client):
        response = client.post(
            "/api/v1/agents/aro-support/invoke",
            json={
                "session_id": "test-session",
                "user_id": "user@example.com",
                "message": "",
            },
        )
        assert response.status_code == 422

    @patch("aro_agent.main.AROAgent")
    def test_invoke_agent_failure_returns_500(self, mock_agent_cls, client):
        mock_agent_cls.side_effect = RuntimeError("Config not found")

        response = client.post(
            "/api/v1/agents/aro-support/invoke",
            json={
                "session_id": "test-session",
                "user_id": "user@example.com",
                "message": "Help",
            },
        )
        assert response.status_code == 500
