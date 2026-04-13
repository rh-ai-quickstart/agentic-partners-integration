"""Tests for agent_service.main.

The main module mounts A2A sub-applications at import time, so we patch
the a2a server functions before importing the app to avoid side effects.

The invoke_agent endpoint imports AgentManager inline via
``from .agents import AgentManager``, so we must patch
``agent_service.agents.AgentManager`` (the canonical location).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def patched_app():
    """Import the FastAPI app with A2A sub-apps mocked out."""
    with (
        patch(
            "agent_service.a2a.server.get_software_support_a2a_app",
            return_value=MagicMock(),
        ),
        patch(
            "agent_service.a2a.server.get_network_support_a2a_app",
            return_value=MagicMock(),
        ),
    ):
        # Force re-import of main to pick up the patches
        import importlib

        import agent_service.main

        importlib.reload(agent_service.main)
        yield agent_service.main.app


class TestHealthCheck:
    """Tests for the /health endpoint."""

    def test_health_check_returns_correct_structure(self, patched_app):
        from fastapi.testclient import TestClient

        client = TestClient(patched_app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data

    def test_health_check_includes_service_name(self, patched_app):
        from fastapi.testclient import TestClient

        client = TestClient(patched_app)
        response = client.get("/health")
        data = response.json()
        assert data["service"] == "agent-service"

    def test_health_check_includes_version(self, patched_app):
        from fastapi.testclient import TestClient

        client = TestClient(patched_app)
        response = client.get("/health")
        data = response.json()
        assert "version" in data
        assert data["version"] == "0.1.0"


class TestInvokeAgent:
    """Tests for the /api/v1/agents/{agent_name}/invoke endpoint."""

    @patch("agent_service.agents.AgentManager")
    def test_invoke_routing_agent(
        self, mock_agent_manager_cls, patched_app
    ):
        from fastapi.testclient import TestClient

        # Set up mock agent
        mock_agent = AsyncMock()
        mock_agent.create_response_with_retry.return_value = (
            "Hello! How can I help you?",
            False,
        )
        mock_manager = MagicMock()
        mock_manager.get_agent.return_value = mock_agent
        mock_manager.agents_dict = {"routing-agent": mock_agent}
        mock_agent_manager_cls.return_value = mock_manager

        client = TestClient(patched_app)
        response = client.post(
            "/api/v1/agents/routing-agent/invoke",
            json={
                "session_id": "sess-1",
                "user_id": "user@test.com",
                "message": "Hello",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["agent_id"] == "routing-agent"
        assert data["session_id"] == "sess-1"
        assert data["content"] == "Hello! How can I help you?"

    @patch("agent_service.main.httpx.AsyncClient")
    @patch("agent_service.agents.AgentManager")
    def test_invoke_specialist_agent(
        self, mock_agent_manager_cls, mock_httpx_cls, patched_app
    ):
        from fastapi.testclient import TestClient

        # Set up mock agent
        mock_agent = AsyncMock()
        mock_agent.create_response_with_retry.return_value = (
            "Here is the solution based on ticket T-123",
            False,
        )
        mock_manager = MagicMock()
        mock_manager.get_agent.return_value = mock_agent
        mock_agent_manager_cls.return_value = mock_manager

        # Set up mock RAG response
        mock_rag_response = MagicMock()
        mock_rag_response.status_code = 200
        mock_rag_response.json.return_value = {
            "response": "RAG answer",
            "sources": [{"id": "T-123", "similarity": 0.95, "content": "Fix info"}],
        }

        mock_httpx_instance = AsyncMock()
        mock_httpx_instance.post.return_value = mock_rag_response
        mock_httpx_instance.__aenter__ = AsyncMock(return_value=mock_httpx_instance)
        mock_httpx_instance.__aexit__ = AsyncMock(return_value=False)
        mock_httpx_cls.return_value = mock_httpx_instance

        client = TestClient(patched_app)
        response = client.post(
            "/api/v1/agents/software-support/invoke",
            json={
                "session_id": "sess-2",
                "user_id": "user@test.com",
                "message": "My app crashes",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["agent_id"] == "software-support"
        assert data["metadata"]["rag_used"] is True

    @patch("agent_service.agents.AgentManager")
    def test_invoke_unknown_agent_returns_404(
        self, mock_agent_manager_cls, patched_app
    ):
        from fastapi.testclient import TestClient

        mock_manager = MagicMock()
        mock_manager.get_agent.side_effect = ValueError("No agent found")
        mock_agent_manager_cls.return_value = mock_manager

        client = TestClient(patched_app)
        response = client.post(
            "/api/v1/agents/nonexistent-agent/invoke",
            json={
                "session_id": "sess-3",
                "user_id": "user@test.com",
                "message": "Help",
            },
        )

        assert response.status_code == 404

    @patch("agent_service.main.httpx.AsyncClient")
    @patch("agent_service.agents.AgentManager")
    def test_invoke_agent_returns_503_when_rag_fails(
        self, mock_agent_manager_cls, mock_httpx_cls, patched_app
    ):
        from fastapi.testclient import TestClient

        mock_agent = AsyncMock()
        mock_manager = MagicMock()
        mock_manager.get_agent.return_value = mock_agent
        mock_agent_manager_cls.return_value = mock_manager

        # Simulate RAG returning 500
        mock_rag_response = MagicMock()
        mock_rag_response.status_code = 500
        mock_rag_response.text = "Internal Server Error"

        mock_httpx_instance = AsyncMock()
        mock_httpx_instance.post.return_value = mock_rag_response
        mock_httpx_instance.__aenter__ = AsyncMock(return_value=mock_httpx_instance)
        mock_httpx_instance.__aexit__ = AsyncMock(return_value=False)
        mock_httpx_cls.return_value = mock_httpx_instance

        client = TestClient(patched_app)
        response = client.post(
            "/api/v1/agents/software-support/invoke",
            json={
                "session_id": "sess-4",
                "user_id": "user@test.com",
                "message": "App crash",
            },
        )

        assert response.status_code == 503

    @patch("agent_service.agents.AgentManager")
    def test_invoke_routing_agent_with_route_decision(
        self, mock_agent_manager_cls, patched_app
    ):
        from fastapi.testclient import TestClient

        mock_agent = AsyncMock()
        mock_agent.create_response_with_retry.return_value = (
            "ROUTE:software-support\nI'll connect you with our software support specialist.",
            False,
        )
        mock_manager = MagicMock()
        mock_manager.get_agent.return_value = mock_agent
        mock_manager.agents_dict = {
            "routing-agent": mock_agent,
            "software-support": MagicMock(),
        }
        mock_agent_manager_cls.return_value = mock_manager

        client = TestClient(patched_app)
        response = client.post(
            "/api/v1/agents/routing-agent/invoke",
            json={
                "session_id": "sess-5",
                "user_id": "user@test.com",
                "message": "My software is crashing",
                "transfer_context": {"departments": ["software"]},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["routing_decision"] == "software-support"

    @patch("agent_service.main.simple_health_check", new_callable=AsyncMock)
    def test_detailed_health_check(
        self, mock_simple_health, patched_app
    ):
        """Line 66: detailed_health_check endpoint calls simple_health_check."""
        from fastapi.testclient import TestClient

        mock_simple_health.return_value = {
            "status": "healthy",
            "service": "agent-service",
            "database": "connected",
        }

        # Override the db dependency to avoid needing a real database
        from shared_models import get_db_session_dependency

        async def override_db():
            return MagicMock()

        patched_app.dependency_overrides[get_db_session_dependency] = override_db

        client = TestClient(patched_app)
        response = client.get("/health/detailed")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

        # Clean up
        patched_app.dependency_overrides.clear()

    @patch("agent_service.agents.AgentManager")
    def test_invoke_routing_agent_no_blocked_agents(
        self, mock_agent_manager_cls, patched_app
    ):
        """Line 168: blocked_section is empty when user has access to all agents."""
        from fastapi.testclient import TestClient

        mock_agent = AsyncMock()
        mock_agent.create_response_with_retry.return_value = (
            "Hello! How can I help you?",
            False,
        )
        mock_manager = MagicMock()
        mock_manager.get_agent.return_value = mock_agent
        mock_manager.agents_dict = {
            "routing-agent": mock_agent,
            "software-support": MagicMock(),
            "network-support": MagicMock(),
        }
        mock_agent_manager_cls.return_value = mock_manager

        client = TestClient(patched_app)
        response = client.post(
            "/api/v1/agents/routing-agent/invoke",
            json={
                "session_id": "sess-no-block",
                "user_id": "admin@test.com",
                "message": "Hello",
                "transfer_context": {
                    "departments": ["software", "network"],
                },
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["agent_id"] == "routing-agent"

    @patch("agent_service.agents.AgentManager")
    def test_invoke_routing_agent_with_conversation_history(
        self, mock_agent_manager_cls, patched_app
    ):
        """Lines 195-197: conversation history is included in routing agent messages."""
        from fastapi.testclient import TestClient

        mock_agent = AsyncMock()
        mock_agent.create_response_with_retry.return_value = (
            "I see you mentioned a crash earlier. Let me help.",
            False,
        )
        mock_manager = MagicMock()
        mock_manager.get_agent.return_value = mock_agent
        mock_manager.agents_dict = {"routing-agent": mock_agent}
        mock_agent_manager_cls.return_value = mock_manager

        client = TestClient(patched_app)
        response = client.post(
            "/api/v1/agents/routing-agent/invoke",
            json={
                "session_id": "sess-hist",
                "user_id": "user@test.com",
                "message": "Can you help with that?",
                "transfer_context": {
                    "departments": ["software"],
                    "conversation_history": [
                        {"role": "user", "content": "My app crashed"},
                        {"role": "assistant", "content": "I can help with that."},
                    ],
                },
            },
        )

        assert response.status_code == 200
        # Verify conversation history was passed in messages
        call_args = mock_agent.create_response_with_retry.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages") or call_args[0][0]
        # Should include system + 2 history turns + 1 current message
        assert len(messages) >= 4

    @patch("agent_service.main.httpx.AsyncClient")
    @patch("agent_service.agents.AgentManager")
    def test_invoke_specialist_httpx_connection_error(
        self, mock_agent_manager_cls, mock_httpx_cls, patched_app
    ):
        """Lines 323-329: httpx.HTTPError during RAG call returns 503."""
        import httpx
        from fastapi.testclient import TestClient

        mock_agent = AsyncMock()
        mock_manager = MagicMock()
        mock_manager.get_agent.return_value = mock_agent
        mock_agent_manager_cls.return_value = mock_manager

        # Simulate httpx connection error
        mock_httpx_instance = AsyncMock()
        mock_httpx_instance.post.side_effect = httpx.ConnectError("Connection refused")
        mock_httpx_instance.__aenter__ = AsyncMock(return_value=mock_httpx_instance)
        mock_httpx_instance.__aexit__ = AsyncMock(return_value=False)
        mock_httpx_cls.return_value = mock_httpx_instance

        client = TestClient(patched_app)
        response = client.post(
            "/api/v1/agents/software-support/invoke",
            json={
                "session_id": "sess-conn",
                "user_id": "user@test.com",
                "message": "App crash",
            },
        )

        assert response.status_code == 503
        assert "RAG API unavailable" in response.json()["detail"]

    @patch("agent_service.main.httpx.AsyncClient")
    @patch("agent_service.agents.AgentManager")
    def test_invoke_specialist_with_conversation_history(
        self, mock_agent_manager_cls, mock_httpx_cls, patched_app
    ):
        """Lines 364-366: specialist agent includes conversation history."""
        from fastapi.testclient import TestClient

        mock_agent = AsyncMock()
        mock_agent.create_response_with_retry.return_value = (
            "Based on your earlier issue, here is the fix.",
            False,
        )
        mock_manager = MagicMock()
        mock_manager.get_agent.return_value = mock_agent
        mock_agent_manager_cls.return_value = mock_manager

        # Set up mock RAG response
        mock_rag_response = MagicMock()
        mock_rag_response.status_code = 200
        mock_rag_response.json.return_value = {
            "response": "RAG answer",
            "sources": [],
        }

        mock_httpx_instance = AsyncMock()
        mock_httpx_instance.post.return_value = mock_rag_response
        mock_httpx_instance.__aenter__ = AsyncMock(return_value=mock_httpx_instance)
        mock_httpx_instance.__aexit__ = AsyncMock(return_value=False)
        mock_httpx_cls.return_value = mock_httpx_instance

        client = TestClient(patched_app)
        response = client.post(
            "/api/v1/agents/software-support/invoke",
            json={
                "session_id": "sess-hist-spec",
                "user_id": "user@test.com",
                "message": "What about the fix?",
                "transfer_context": {
                    "conversation_history": [
                        {"role": "user", "content": "My app crashes on startup"},
                        {"role": "assistant", "content": "Let me look into that."},
                    ],
                },
            },
        )

        assert response.status_code == 200
        # Verify conversation history was included in messages
        call_args = mock_agent.create_response_with_retry.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages") or call_args[0][0]
        # Should include 2 history turns + 1 current with RAG context
        assert len(messages) >= 3

    @patch("agent_service.main.httpx.AsyncClient")
    @patch("agent_service.agents.AgentManager")
    def test_invoke_specialist_agent_response_failed(
        self, mock_agent_manager_cls, mock_httpx_cls, patched_app
    ):
        """Line 387: specialist agent response generation fails (failed=True)."""
        from fastapi.testclient import TestClient

        mock_agent = AsyncMock()
        mock_agent.create_response_with_retry.return_value = (
            "I apologize, but I'm having difficulty generating a response right now. Please try again.",
            True,  # Response generation failed
        )
        mock_manager = MagicMock()
        mock_manager.get_agent.return_value = mock_agent
        mock_agent_manager_cls.return_value = mock_manager

        mock_rag_response = MagicMock()
        mock_rag_response.status_code = 200
        mock_rag_response.json.return_value = {
            "response": "RAG answer",
            "sources": [{"id": "T-1", "similarity": 0.9, "content": "Fix"}],
        }

        mock_httpx_instance = AsyncMock()
        mock_httpx_instance.post.return_value = mock_rag_response
        mock_httpx_instance.__aenter__ = AsyncMock(return_value=mock_httpx_instance)
        mock_httpx_instance.__aexit__ = AsyncMock(return_value=False)
        mock_httpx_cls.return_value = mock_httpx_instance

        client = TestClient(patched_app)
        response = client.post(
            "/api/v1/agents/software-support/invoke",
            json={
                "session_id": "sess-fail",
                "user_id": "user@test.com",
                "message": "App crash",
            },
        )

        # Should still return 200 with the default apology message
        assert response.status_code == 200
        data = response.json()
        assert "apologize" in data["content"].lower()

    @patch("agent_service.agents.AgentManager")
    def test_invoke_agent_generic_exception_returns_500(
        self, mock_agent_manager_cls, patched_app
    ):
        """Lines 418-426: generic Exception is caught and returns 500."""
        from fastapi.testclient import TestClient

        # Make AgentManager constructor raise a non-HTTP exception
        mock_agent_manager_cls.side_effect = RuntimeError("Unexpected internal error")

        client = TestClient(patched_app)
        response = client.post(
            "/api/v1/agents/routing-agent/invoke",
            json={
                "session_id": "sess-err",
                "user_id": "user@test.com",
                "message": "Hello",
            },
        )

        assert response.status_code == 500
        assert "Agent invocation failed" in response.json()["detail"]

    @patch("agent_service.agents.AgentManager")
    def test_invoke_agent_http_exception_reraise(
        self, mock_agent_manager_cls, patched_app
    ):
        """Lines 415-417: HTTPException is re-raised as-is (not wrapped in 500).

        This verifies the 'except HTTPException: raise' path.
        The 404 test already covers this implicitly, but this test is explicit
        about it being distinct from the generic Exception handler.
        """
        from fastapi import HTTPException
        from fastapi.testclient import TestClient

        mock_manager = MagicMock()
        mock_manager.get_agent.side_effect = ValueError("No agent found")
        mock_agent_manager_cls.return_value = mock_manager

        client = TestClient(patched_app)
        response = client.post(
            "/api/v1/agents/nonexistent/invoke",
            json={
                "session_id": "sess-http",
                "user_id": "user@test.com",
                "message": "Help",
            },
        )

        # HTTPException(404) should pass through, NOT become 500
        assert response.status_code == 404


class TestLifespan:
    """Tests for the lifespan function."""

    @patch("agent_service.main.create_shared_lifespan")
    def test_lifespan_calls_create_shared_lifespan(self, mock_create_lifespan, patched_app):
        """Line 27: lifespan function calls create_shared_lifespan."""
        from agent_service.main import lifespan

        mock_create_lifespan.return_value = MagicMock()

        result = lifespan(patched_app)

        mock_create_lifespan.assert_called_once_with(
            service_name="agent-service",
            version="0.1.0",
        )


class TestMainBlock:
    """Tests for the __main__ block."""

    @patch("uvicorn.run")
    def test_main_block(self, mock_uvicorn_run, patched_app, monkeypatch):
        """Lines 433-438: __main__ block runs uvicorn.run with correct params."""
        monkeypatch.setenv("PORT", "9090")
        monkeypatch.setenv("HOST", "127.0.0.1")
        monkeypatch.setenv("RELOAD", "true")

        import runpy

        runpy.run_module("agent_service.main", run_name="__main__", alter_sys=False)

        mock_uvicorn_run.assert_called_once_with(
            "agent_service.main:app",
            host="127.0.0.1",
            port=9090,
            reload=True,
            log_level="info",
        )
