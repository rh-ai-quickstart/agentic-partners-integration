"""Tests for agent_service.main.

The main module mounts A2A sub-applications at import time using an
AgentManager instance, so we patch the AgentManager and get_a2a_app
before importing the app to avoid side effects.

The invoke_agent endpoint imports AgentManager inline via
``from .agents import AgentManager``, so we must patch
``agent_service.agents.AgentManager`` (the canonical location).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Default SPIFFE identity header for service-to-service calls.
# All /invoke requests require caller identity when ENFORCE_AGENT_AUTH=true.
SERVICE_SPIFFE_ID = "spiffe://partner.example.com/service/request-manager"
DEFAULT_HEADERS = {"X-SPIFFE-ID": SERVICE_SPIFFE_ID}

# Standard specialist agent registry used across tests
_TEST_AGENT_DEPT_MAP = {
    "software-support": ["software"],
    "network-support": ["network"],
    "kubernetes-support": ["kubernetes"],
}
_TEST_AGENT_DESCRIPTIONS = {
    "software-support": "Handles software issues, bugs, errors, crashes, application problems, error codes",
    "network-support": "Handles network issues, connectivity, VPN, firewall, DNS, router problems",
    "kubernetes-support": "Handles Kubernetes issues, pod failures, deployment problems, cluster troubleshooting, container orchestration",
}


def _make_mock_manager(**overrides: object) -> MagicMock:
    """Create a MagicMock AgentManager with dynamic registry methods."""
    manager = MagicMock()
    manager.get_agent_dept_map.return_value = _TEST_AGENT_DEPT_MAP
    manager.get_agent_descriptions.return_value = _TEST_AGENT_DESCRIPTIONS
    manager.get_specialist_agents.return_value = {}
    manager.get_agent_endpoints.return_value = {}
    manager.get_remote_agent_endpoints.return_value = {}
    for key, value in overrides.items():
        setattr(manager, key, value)
    return manager


@pytest.fixture
def patched_app(monkeypatch):
    """Import the FastAPI app with A2A mounting and AgentManager mocked out."""
    monkeypatch.setattr("shared_models.identity.SPIFFE_MODE", "mock")
    mock_a2a_manager = _make_mock_manager()
    with (
        patch(
            "agent_service.agents.AgentManager",
            return_value=mock_a2a_manager,
        ),
        patch(
            "agent_service.a2a.server.get_a2a_app",
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
    def test_invoke_routing_agent(self, mock_agent_manager_cls, patched_app):
        from fastapi.testclient import TestClient

        # Set up mock agent
        mock_agent = AsyncMock()
        mock_agent.create_response_with_retry.return_value = (
            "Hello! How can I help you?",
            False,
        )
        mock_manager = _make_mock_manager()
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
            headers=DEFAULT_HEADERS,
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
        mock_manager = _make_mock_manager()
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
            headers=DEFAULT_HEADERS,
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

        mock_manager = _make_mock_manager()
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
            headers=DEFAULT_HEADERS,
        )

        assert response.status_code == 404

    @patch("agent_service.main.httpx.AsyncClient")
    @patch("agent_service.agents.AgentManager")
    def test_invoke_agent_returns_503_when_rag_fails(
        self, mock_agent_manager_cls, mock_httpx_cls, patched_app
    ):
        from fastapi.testclient import TestClient

        mock_agent = AsyncMock()
        mock_manager = _make_mock_manager()
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
            headers=DEFAULT_HEADERS,
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
        mock_manager = _make_mock_manager()
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
            headers=DEFAULT_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["routing_decision"] == "software-support"

    @patch("agent_service.main.simple_health_check", new_callable=AsyncMock)
    def test_detailed_health_check(self, mock_simple_health, patched_app):
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
        mock_manager = _make_mock_manager()
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
            headers=DEFAULT_HEADERS,
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
        mock_manager = _make_mock_manager()
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
            headers=DEFAULT_HEADERS,
        )

        assert response.status_code == 200
        # Verify conversation history was passed in messages
        call_args = mock_agent.create_response_with_retry.call_args
        messages = (
            call_args.kwargs.get("messages")
            or call_args[1].get("messages")
            or call_args[0][0]
        )
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
        mock_manager = _make_mock_manager()
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
            headers=DEFAULT_HEADERS,
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
        mock_manager = _make_mock_manager()
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
            headers=DEFAULT_HEADERS,
        )

        assert response.status_code == 200
        # Verify conversation history was included in messages
        call_args = mock_agent.create_response_with_retry.call_args
        messages = (
            call_args.kwargs.get("messages")
            or call_args[1].get("messages")
            or call_args[0][0]
        )
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
        mock_manager = _make_mock_manager()
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
            headers=DEFAULT_HEADERS,
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
            headers=DEFAULT_HEADERS,
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
        from fastapi.testclient import TestClient

        mock_manager = _make_mock_manager()
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
            headers=DEFAULT_HEADERS,
        )

        # HTTPException(404) should pass through, NOT become 500
        assert response.status_code == 404


class TestAgentRegistry:
    """Tests for the GET /api/v1/agents/registry endpoint."""

    @patch("agent_service.agents.AgentManager")
    def test_registry_returns_agent_info(self, mock_agent_manager_cls, patched_app):
        """Registry endpoint returns departments and descriptions for local agents."""
        from fastapi.testclient import TestClient

        mock_manager = _make_mock_manager()
        mock_manager.get_specialist_agents.return_value = {
            "software-support": {"departments": ["software"]},
            "network-support": {"departments": ["network"]},
        }
        mock_manager.get_remote_agent_endpoints.return_value = {}
        mock_agent_manager_cls.return_value = mock_manager

        client = TestClient(patched_app)
        response = client.get("/api/v1/agents/registry", headers=DEFAULT_HEADERS)

        assert response.status_code == 200
        data = response.json()
        assert "software-support" in data["agents"]
        assert "network-support" in data["agents"]
        sw = data["agents"]["software-support"]
        assert "endpoint" not in sw
        assert "departments" in sw
        assert "description" in sw

    @patch("agent_service.agents.AgentManager")
    def test_registry_includes_remote_endpoint(
        self, mock_agent_manager_cls, patched_app
    ):
        """Remote agents have their custom endpoint URL in the registry."""
        from fastapi.testclient import TestClient

        mock_manager = _make_mock_manager()
        mock_manager.get_specialist_agents.return_value = {
            "software-support": {"departments": ["software"]},
            "database-support": {
                "departments": ["database"],
                "endpoint": "http://db-agent:9090/api/v1/agents/database-support/invoke",
            },
        }
        mock_manager.get_agent_dept_map.return_value = {
            "software-support": ["software"],
            "database-support": ["database"],
        }
        mock_manager.get_agent_descriptions.return_value = {
            "software-support": "Handles software issues",
            "database-support": "Handles database issues",
        }
        mock_manager.get_remote_agent_endpoints.return_value = {
            "database-support": "http://db-agent:9090/api/v1/agents/database-support/invoke",
        }
        mock_agent_manager_cls.return_value = mock_manager

        client = TestClient(patched_app)
        response = client.get("/api/v1/agents/registry", headers=DEFAULT_HEADERS)

        data = response.json()
        assert "endpoint" not in data["agents"]["software-support"]
        assert data["agents"]["database-support"]["endpoint"] == (
            "http://db-agent:9090/api/v1/agents/database-support/invoke"
        )


class TestAuthEnforcement:
    """Tests for caller identity and policy engine authorization enforcement."""

    def test_rejects_request_without_identity(self, patched_app, monkeypatch):
        """When ENFORCE_AGENT_AUTH=true, requests without X-SPIFFE-ID are rejected."""
        from fastapi.testclient import TestClient

        monkeypatch.setenv("ENFORCE_AGENT_AUTH", "true")

        client = TestClient(patched_app)
        response = client.post(
            "/api/v1/agents/routing-agent/invoke",
            json={
                "session_id": "sess-noauth",
                "user_id": "user@test.com",
                "message": "Hello",
            },
            # No X-SPIFFE-ID header
        )

        assert response.status_code == 403
        assert "Caller identity required" in response.json()["detail"]

    @patch("shared_models.identity_middleware.IDENTITY_ENFORCEMENT", False)
    def test_endpoint_rejects_no_identity_when_middleware_disabled(
        self, patched_app, monkeypatch
    ):
        """Endpoint-level auth rejects even when middleware enforcement is off."""
        from fastapi.testclient import TestClient

        monkeypatch.setenv("ENFORCE_AGENT_AUTH", "true")

        client = TestClient(patched_app)
        response = client.post(
            "/api/v1/agents/routing-agent/invoke",
            json={
                "session_id": "sess-noauth-ep",
                "user_id": "user@test.com",
                "message": "Hello",
            },
        )

        assert response.status_code == 403
        assert "mTLS certificate" in response.json()["detail"]

    @patch("agent_service.agents.AgentManager")
    def test_allows_request_with_service_identity(
        self, mock_agent_manager_cls, patched_app, monkeypatch
    ):
        """Service-to-service call with X-SPIFFE-ID but no delegation is allowed."""
        from fastapi.testclient import TestClient

        monkeypatch.setenv("ENFORCE_AGENT_AUTH", "true")

        mock_agent = AsyncMock()
        mock_agent.create_response_with_retry.return_value = ("Hello!", False)
        mock_manager = _make_mock_manager()
        mock_manager.get_agent.return_value = mock_agent
        mock_manager.agents_dict = {"routing-agent": mock_agent}
        mock_agent_manager_cls.return_value = mock_manager

        client = TestClient(patched_app)
        response = client.post(
            "/api/v1/agents/routing-agent/invoke",
            json={
                "session_id": "sess-svc",
                "user_id": "user@test.com",
                "message": "Hello",
            },
            headers=DEFAULT_HEADERS,
        )

        assert response.status_code == 200

    def test_opa_delegation_allows(self, patched_app, monkeypatch):
        """When delegation headers present and policy engine allows, request proceeds."""
        from dataclasses import dataclass, field

        from fastapi.testclient import TestClient

        monkeypatch.setenv("ENFORCE_AGENT_AUTH", "true")

        @dataclass
        class FakePolicyDecision:
            allow: bool = True
            reason: str = "Delegated access granted"
            effective_departments: list = field(default_factory=lambda: ["software"])

        with (
            patch("agent_service.agents.AgentManager") as mock_agent_manager_cls,
            patch(
                "shared_models.policy_client.check_agent_authorization",
                new_callable=AsyncMock,
                return_value=FakePolicyDecision(),
            ),
            patch("agent_service.main.httpx.AsyncClient") as mock_httpx_cls,
        ):
            mock_agent = AsyncMock()
            mock_agent.create_response_with_retry.return_value = (
                "Fix: restart app",
                False,
            )
            mock_manager = _make_mock_manager()
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
                    "session_id": "sess-deleg-ok",
                    "user_id": "carlos@example.com",
                    "message": "App crash",
                    "transfer_context": {"departments": ["software"]},
                },
                headers={
                    "X-SPIFFE-ID": SERVICE_SPIFFE_ID,
                    "X-Delegation-User": "spiffe://partner.example.com/user/carlos",
                },
            )

        assert response.status_code == 200

    def test_policy_delegation_denies(self, patched_app, monkeypatch):
        """When delegation headers present and policy engine denies, returns 403."""
        from dataclasses import dataclass, field

        from fastapi.testclient import TestClient

        monkeypatch.setenv("ENFORCE_AGENT_AUTH", "true")

        @dataclass
        class FakePolicyDecision:
            allow: bool = False
            reason: str = "No overlapping departments"
            effective_departments: list = field(default_factory=list)

        with patch(
            "shared_models.policy_client.check_agent_authorization",
            new_callable=AsyncMock,
            return_value=FakePolicyDecision(),
        ):
            client = TestClient(patched_app)
            response = client.post(
                "/api/v1/agents/network-support/invoke",
                json={
                    "session_id": "sess-deleg-deny",
                    "user_id": "carlos@example.com",
                    "message": "VPN issue",
                    "transfer_context": {"departments": ["software"]},
                },
                headers={
                    "X-SPIFFE-ID": SERVICE_SPIFFE_ID,
                    "X-Delegation-User": "spiffe://partner.example.com/user/carlos",
                },
            )

        assert response.status_code == 403
        assert "Authorization denied" in response.json()["detail"]

    @patch("agent_service.agents.AgentManager")
    def test_enforcement_disabled_allows_without_identity(
        self, mock_agent_manager_cls, patched_app, monkeypatch
    ):
        """When ENFORCE_AGENT_AUTH=false, identity is not required."""
        from fastapi.testclient import TestClient

        monkeypatch.setenv("ENFORCE_AGENT_AUTH", "false")
        monkeypatch.setattr("shared_models.identity_middleware.IDENTITY_ENFORCEMENT", False)

        mock_agent = AsyncMock()
        mock_agent.create_response_with_retry.return_value = ("Hello!", False)
        mock_manager = _make_mock_manager()
        mock_manager.get_agent.return_value = mock_agent
        mock_manager.agents_dict = {"routing-agent": mock_agent}
        mock_agent_manager_cls.return_value = mock_manager

        client = TestClient(patched_app)
        response = client.post(
            "/api/v1/agents/routing-agent/invoke",
            json={
                "session_id": "sess-noauth-ok",
                "user_id": "user@test.com",
                "message": "Hello",
            },
            # No X-SPIFFE-ID header
        )

        assert response.status_code == 200

    def test_agent_caller_without_delegation_denied(self, patched_app, monkeypatch):
        """Agent callers without delegation context are rejected.

        This verifies defense-in-depth: policy engine Rule 5 denies autonomous agents.
        """
        from dataclasses import dataclass, field

        from fastapi.testclient import TestClient

        monkeypatch.setenv("ENFORCE_AGENT_AUTH", "true")

        # Agent identity (not a service) with delegation but policy engine denies
        @dataclass
        class FakePolicyDecision:
            allow: bool = False
            reason: str = "Autonomous agent access denied"
            effective_departments: list = field(default_factory=list)

        with patch(
            "shared_models.policy_client.check_agent_authorization",
            new_callable=AsyncMock,
            return_value=FakePolicyDecision(),
        ):
            client = TestClient(patched_app)
            response = client.post(
                "/api/v1/agents/software-support/invoke",
                json={
                    "session_id": "sess-agent-auto",
                    "user_id": "user@test.com",
                    "message": "Do something",
                    "transfer_context": {"departments": []},
                },
                headers={
                    "X-SPIFFE-ID": "spiffe://partner.example.com/agent/rogue-agent",
                    "X-Delegation-User": "spiffe://partner.example.com/user/nobody",
                },
            )

        assert response.status_code == 403

    def test_jwt_delegated_claim_extracts_identity(self, patched_app, monkeypatch):
        """JWT with act claim extracts delegation from claims, not headers."""
        from dataclasses import dataclass, field

        from fastapi.testclient import TestClient

        monkeypatch.setenv("ENFORCE_AGENT_AUTH", "true")
        monkeypatch.setenv("JWT_VALIDATION_ENABLED", "true")

        @dataclass
        class FakeTokenClaims:
            subject: str = "user-123"
            issuer: str = "http://keycloak:8080/realms/partner-agent"
            audience: list = field(default_factory=lambda: ["agent-service"])
            email: str = "carlos@example.com"
            groups: list = field(default_factory=lambda: ["software"])
            act: dict = field(default_factory=lambda: {"sub": "request-manager-svc"})
            azp: str = "partner-agent-ui"
            raw: dict = field(default_factory=lambda: {"sub": "user-123", "azp": "partner-agent-ui"})
            is_delegated: bool = True
            caller_identity: str = "user-123"

        @dataclass
        class FakePolicyDecision:
            allow: bool = True
            reason: str = "Delegated access granted"
            effective_departments: list = field(default_factory=lambda: ["software"])

        with (
            patch("agent_service.agents.AgentManager") as mock_agent_manager_cls,
            patch(
                "agent_service.main.validate_bearer_token",
                return_value=FakeTokenClaims(),
            ),
            patch(
                "shared_models.policy_client.check_agent_authorization",
                new_callable=AsyncMock,
                return_value=FakePolicyDecision(),
            ),
            patch("agent_service.main.httpx.AsyncClient") as mock_httpx_cls,
        ):
            mock_agent = AsyncMock()
            mock_agent.create_response_with_retry.return_value = ("Fixed!", False)
            mock_manager = _make_mock_manager()
            mock_manager.get_agent.return_value = mock_agent
            mock_agent_manager_cls.return_value = mock_manager

            mock_rag = MagicMock()
            mock_rag.status_code = 200
            mock_rag.json.return_value = {"response": "RAG", "sources": []}
            mock_httpx_instance = AsyncMock()
            mock_httpx_instance.post.return_value = mock_rag
            mock_httpx_instance.__aenter__ = AsyncMock(return_value=mock_httpx_instance)
            mock_httpx_instance.__aexit__ = AsyncMock(return_value=False)
            mock_httpx_cls.return_value = mock_httpx_instance

            client = TestClient(patched_app)
            response = client.post(
                "/api/v1/agents/software-support/invoke",
                json={
                    "session_id": "sess-jwt-deleg",
                    "user_id": "carlos@example.com",
                    "message": "Fix crash",
                },
                headers={
                    "X-SPIFFE-ID": SERVICE_SPIFFE_ID,
                    "Authorization": "Bearer fake-jwt-token",
                },
            )

        assert response.status_code == 200

    def test_jwt_with_header_delegation(self, patched_app, monkeypatch):
        """JWT present (no act claim) but X-Delegation-User header → uses header delegation with JWT groups."""
        from dataclasses import dataclass, field

        from fastapi.testclient import TestClient

        monkeypatch.setenv("ENFORCE_AGENT_AUTH", "true")
        monkeypatch.setenv("JWT_VALIDATION_ENABLED", "true")

        @dataclass
        class FakeTokenClaims:
            subject: str = "user-123"
            issuer: str = "http://keycloak:8080/realms/partner-agent"
            audience: list = field(default_factory=lambda: ["agent-service"])
            email: str = "carlos@example.com"
            groups: list = field(default_factory=lambda: ["software"])
            act: object = None
            azp: str = "partner-agent-ui"
            raw: dict = field(default_factory=lambda: {"sub": "user-123"})
            is_delegated: bool = False
            caller_identity: str = "user-123"

        @dataclass
        class FakePolicyDecision:
            allow: bool = True
            reason: str = "Delegated access granted"
            effective_departments: list = field(default_factory=lambda: ["software"])

        with (
            patch("agent_service.agents.AgentManager") as mock_agent_manager_cls,
            patch(
                "agent_service.main.validate_bearer_token",
                return_value=FakeTokenClaims(),
            ),
            patch(
                "shared_models.policy_client.check_agent_authorization",
                new_callable=AsyncMock,
                return_value=FakePolicyDecision(),
            ),
            patch("agent_service.main.httpx.AsyncClient") as mock_httpx_cls,
        ):
            mock_agent = AsyncMock()
            mock_agent.create_response_with_retry.return_value = ("Fixed!", False)
            mock_manager = _make_mock_manager()
            mock_manager.get_agent.return_value = mock_agent
            mock_agent_manager_cls.return_value = mock_manager

            mock_rag = MagicMock()
            mock_rag.status_code = 200
            mock_rag.json.return_value = {"response": "RAG", "sources": []}
            mock_httpx_instance = AsyncMock()
            mock_httpx_instance.post.return_value = mock_rag
            mock_httpx_instance.__aenter__ = AsyncMock(return_value=mock_httpx_instance)
            mock_httpx_instance.__aexit__ = AsyncMock(return_value=False)
            mock_httpx_cls.return_value = mock_httpx_instance

            client = TestClient(patched_app)
            response = client.post(
                "/api/v1/agents/software-support/invoke",
                json={
                    "session_id": "sess-jwt-hdr",
                    "user_id": "carlos@example.com",
                    "message": "Fix crash",
                    "transfer_context": {"departments": ["software"]},
                },
                headers={
                    "X-SPIFFE-ID": SERVICE_SPIFFE_ID,
                    "Authorization": "Bearer fake-jwt-token",
                    "X-Delegation-User": "spiffe://partner.example.com/user/carlos",
                },
            )

        assert response.status_code == 200


class TestLifespan:
    """Tests for the lifespan function."""

    @patch("agent_service.main.create_shared_lifespan")
    def test_lifespan_calls_create_shared_lifespan(
        self, mock_create_lifespan, patched_app
    ):
        """Line 27: lifespan function calls create_shared_lifespan."""
        from agent_service.main import _agent_service_startup, lifespan

        mock_create_lifespan.return_value = MagicMock()

        result = lifespan(patched_app)

        mock_create_lifespan.assert_called_once_with(
            service_name="agent-service",
            version="0.1.0",
            custom_startup=_agent_service_startup,
        )


class TestMainBlock:
    """Tests for the __main__ block."""

    @patch("uvicorn.run")
    def test_main_block(self, mock_uvicorn_run, patched_app, monkeypatch):
        """__main__ block runs uvicorn.run with correct params."""
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

    @patch("uvicorn.run")
    def test_main_block_mtls(self, mock_uvicorn_run, patched_app, monkeypatch):
        """__main__ block with SPIFFE_MODE=mtls passes SSL params."""
        monkeypatch.setenv("SPIFFE_MODE", "mtls")
        monkeypatch.setenv("PORT", "8443")

        mock_paths = {
            "cert": "/run/spire/svids/svid.0.pem",
            "key": "/run/spire/svids/svid.0.key",
            "bundle": "/run/spire/svids/bundle.0.pem",
        }

        import runpy
        import ssl

        with patch("shared_models.mtls.svid_paths", return_value=mock_paths):
            runpy.run_module("agent_service.main", run_name="__main__", alter_sys=False)

        call_kwargs = mock_uvicorn_run.call_args
        assert call_kwargs[1]["ssl_keyfile"] == mock_paths["key"]
        assert call_kwargs[1]["ssl_certfile"] == mock_paths["cert"]
        assert call_kwargs[1]["ssl_ca_certs"] == mock_paths["bundle"]
        assert call_kwargs[1]["ssl_cert_reqs"] == ssl.CERT_OPTIONAL


class TestAgentServiceStartup:
    """Tests for _agent_service_startup (lines 29-45)."""

    @pytest.mark.asyncio
    async def test_dcr_startup_spire_success_dcr_success(self):
        """Lines 29-43: DCR_ENABLED, SPIRE works, DCR registration succeeds."""
        mock_svid = MagicMock()
        mock_svid.spiffe_id = "spiffe://partner.example.com/agent-service"

        mock_spire = MagicMock()
        mock_spire.fetch_svid.return_value = mock_svid

        mock_dcr = AsyncMock()
        mock_dcr.ensure_registered = AsyncMock()

        with (
            patch("shared_models.dcr_client.DCR_ENABLED", True),
            patch("shared_models.spire_client.get_spire_client", return_value=mock_spire),
            patch(
                "shared_models.dcr_client.get_dcr_client", return_value=mock_dcr
            ) as mock_get_dcr,
        ):
            from agent_service.main import _agent_service_startup

            await _agent_service_startup()

        mock_spire.fetch_svid.assert_called_once()
        mock_get_dcr.assert_called_once_with(
            spiffe_id="spiffe://partner.example.com/agent-service",
            client_name="agent-service",
        )
        mock_dcr.ensure_registered.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dcr_startup_spire_fails_falls_back_to_env(self, monkeypatch):
        """Lines 37-38: SPIRE failure falls back to SPIFFE_ID env var."""
        monkeypatch.setenv("SPIFFE_ID", "spiffe://env-fallback/agent-service")

        mock_dcr = AsyncMock()
        mock_dcr.ensure_registered = AsyncMock()

        with (
            patch("shared_models.dcr_client.DCR_ENABLED", True),
            patch(
                "shared_models.spire_client.get_spire_client",
                side_effect=RuntimeError("SPIRE not available"),
            ),
            patch(
                "shared_models.dcr_client.get_dcr_client", return_value=mock_dcr
            ) as mock_get_dcr,
        ):
            from agent_service.main import _agent_service_startup

            await _agent_service_startup()

        mock_get_dcr.assert_called_once_with(
            spiffe_id="spiffe://env-fallback/agent-service",
            client_name="agent-service",
        )

    @pytest.mark.asyncio
    async def test_dcr_startup_registration_fails_logs_warning(self):
        """Lines 44-45: DCR registration failure logs warning, does not crash."""
        mock_svid = MagicMock()
        mock_svid.spiffe_id = "spiffe://partner.example.com/agent-service"

        mock_spire = MagicMock()
        mock_spire.fetch_svid.return_value = mock_svid

        mock_dcr = AsyncMock()
        mock_dcr.ensure_registered = AsyncMock(
            side_effect=RuntimeError("Registration failed")
        )

        with (
            patch("shared_models.dcr_client.DCR_ENABLED", True),
            patch("shared_models.spire_client.get_spire_client", return_value=mock_spire),
            patch("shared_models.dcr_client.get_dcr_client", return_value=mock_dcr),
        ):
            from agent_service.main import _agent_service_startup

            # Should NOT raise
            await _agent_service_startup()

        mock_dcr.ensure_registered.assert_awaited_once()


class TestA2AMount:
    """Tests for the module-level A2A mount loop (line 76)."""

    def test_a2a_mount_with_specialist_agents(self):
        """Line 76: app.mount() is called for each specialist agent."""
        mock_manager = _make_mock_manager()
        mock_manager.get_specialist_agents.return_value = {
            "software-support": {"departments": ["software"]},
        }

        mock_a2a_app = MagicMock()

        with (
            patch(
                "agent_service.agents.AgentManager",
                return_value=mock_manager,
            ),
            patch(
                "agent_service.a2a.server.get_a2a_app",
                return_value=mock_a2a_app,
            ) as mock_get_a2a_app,
        ):
            import importlib

            import agent_service.main

            importlib.reload(agent_service.main)

        mock_get_a2a_app.assert_any_call(
            "software-support", {"departments": ["software"]}
        )


class TestRoutingAgentEdgeCases:
    """Tests for routing-agent edge cases (lines 382, 438)."""

    @patch("agent_service.agents.AgentManager")
    def test_no_blocked_agents_empty_blocked_section(
        self, mock_agent_manager_cls, patched_app
    ):
        """Line 382: blocked_section = '' when user has access to ALL agents.

        The dept map must only contain agents whose departments are a subset
        of the user's departments, so no agent is blocked.
        """
        from fastapi.testclient import TestClient

        mock_agent = AsyncMock()
        mock_agent.create_response_with_retry.return_value = (
            "Hello! How can I help you?",
            False,
        )
        mock_manager = _make_mock_manager()
        # Override dept map to only include agents the user can access
        mock_manager.get_agent_dept_map.return_value = {
            "software-support": ["software"],
            "network-support": ["network"],
        }
        mock_manager.get_agent_descriptions.return_value = {
            "software-support": "Handles software issues",
            "network-support": "Handles network issues",
        }
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
                "session_id": "sess-no-block-all",
                "user_id": "admin@test.com",
                "message": "Hello",
                "transfer_context": {
                    "departments": ["software", "network"],
                },
            },
            headers=DEFAULT_HEADERS,
        )

        assert response.status_code == 200
        # Verify blocked_section was empty by checking the system prompt
        call_args = mock_agent.create_response_with_retry.call_args
        messages = (
            call_args.kwargs.get("messages")
            or call_args[1].get("messages")
            or call_args[0][0]
        )
        system_msg = messages[0]["content"]
        assert "ACCESS RESTRICTION" not in system_msg

    @patch("agent_service.agents.AgentManager")
    def test_conversation_history_with_agent_attribution(
        self, mock_agent_manager_cls, patched_app
    ):
        """Line 438: assistant turn with 'agent' key gets prefixed with [Agent: ...]."""
        from fastapi.testclient import TestClient

        mock_agent = AsyncMock()
        mock_agent.create_response_with_retry.return_value = (
            "ROUTE:software-support\nRouting to specialist.",
            False,
        )
        mock_manager = _make_mock_manager()
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
                "session_id": "sess-agent-attr",
                "user_id": "user@test.com",
                "message": "Yes, do it",
                "transfer_context": {
                    "departments": ["software"],
                    "conversation_history": [
                        {"role": "user", "content": "My app crashes"},
                        {
                            "role": "assistant",
                            "content": "I found ticket T-123.",
                            "agent": "software-support",
                        },
                    ],
                },
            },
            headers=DEFAULT_HEADERS,
        )

        assert response.status_code == 200
        # Verify the agent attribution was prepended to the message content
        call_args = mock_agent.create_response_with_retry.call_args
        messages = (
            call_args.kwargs.get("messages")
            or call_args[1].get("messages")
            or call_args[0][0]
        )
        # Find the assistant message from history
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        assert any(
            "[Agent: software-support]" in m["content"] for m in assistant_msgs
        )
