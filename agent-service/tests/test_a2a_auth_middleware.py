"""Tests for A2A Authentication Middleware.

Verifies that the A2AAuthMiddleware enforces JWT authentication on A2A
sub-app endpoints, closing the gap where agent cards advertised OAuth2+mTLS
security schemes that the server never actually enforced.

Test structure:
  1. TestA2AAuthGap — demonstrates the problem: without middleware, all
     A2A endpoints are accessible without authentication despite the
     agent card advertising security requirements.
  2. TestA2AAuthMiddleware* — unit tests for the middleware itself.
  3. TestA2AAuthMiddlewareIntegration — verifies _build_a2a_app wires
     the middleware into the Starlette sub-app.
"""

from unittest.mock import MagicMock, patch

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from agent_service.a2a.auth_middleware import A2AAuthMiddleware, A2A_PUBLIC_PATHS
from agent_service.jwt_auth import JWTAuthError, TokenClaims


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_bare_app() -> Starlette:
    """Create a minimal Starlette app WITHOUT auth middleware (simulates gap)."""

    async def rpc_endpoint(request: Request):
        return PlainTextResponse("rpc-ok")

    async def agent_card(request: Request):
        return PlainTextResponse("card-ok")

    return Starlette(
        routes=[
            Route("/", rpc_endpoint, methods=["POST"]),
            Route("/.well-known/agent.json", agent_card),
            Route("/.well-known/agent-card.json", agent_card),
        ]
    )


def _make_protected_app() -> Starlette:
    """Create a minimal Starlette app WITH A2AAuthMiddleware for testing."""

    async def rpc_endpoint(request: Request):
        return PlainTextResponse("rpc-ok")

    async def agent_card(request: Request):
        return PlainTextResponse("card-ok")

    app = Starlette(
        routes=[
            Route("/", rpc_endpoint, methods=["POST"]),
            Route("/.well-known/agent.json", agent_card),
            Route("/.well-known/agent-card.json", agent_card),
        ]
    )
    app.add_middleware(A2AAuthMiddleware)
    return app


def _make_valid_claims() -> TokenClaims:
    """Return a valid TokenClaims fixture."""
    return TokenClaims(
        subject="test-user",
        issuer="http://keycloak:8080/realms/partner-agent",
        audience=["agent-service"],
        email="test@example.com",
        azp="test-client",
        raw={"sub": "test-user", "iss": "http://keycloak:8080/realms/partner-agent"},
    )


# ── 1. Gap demonstration ────────────────────────────────────────────────────


class TestA2AAuthGap:
    """Demonstrates the authentication gap: without middleware, A2A
    endpoints accept unauthenticated requests even though the agent
    card advertises security requirements.
    """

    def test_bare_app_rpc_accessible_without_auth(self):
        """Without middleware, the RPC endpoint accepts requests with no
        Authorization header — this is the gap we are fixing."""
        client = TestClient(_make_bare_app())
        resp = client.post("/", json={"jsonrpc": "2.0", "method": "ping", "id": 1})
        # The bare app happily responds — no 401
        assert resp.status_code == 200
        assert resp.text == "rpc-ok"

    def test_bare_app_rpc_accepts_garbage_auth(self):
        """Without middleware, the RPC endpoint ignores an invalid Bearer token."""
        client = TestClient(_make_bare_app())
        resp = client.post(
            "/",
            json={"jsonrpc": "2.0", "method": "ping", "id": 1},
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert resp.status_code == 200
        assert resp.text == "rpc-ok"

    def test_agent_card_advertises_security_but_bare_app_ignores_it(self):
        """The agent card declares security schemes, yet the bare A2A app
        does not enforce them — cards promise authentication that never
        happens. This is the core gap."""
        from agent_service.a2a.agent_cards import create_agent_card

        config = {
            "name": "test-agent",
            "a2a": {
                "card_name": "Test Agent",
                "card_description": "A test agent.",
                "skills": [],
            },
        }
        card = create_agent_card("test-agent", config, "http://localhost:8080/a2a/test-agent/")

        # Card advertises security requirements
        assert card.security_schemes is not None
        assert "partner-oauth2" in card.security_schemes
        assert card.security is not None
        assert len(card.security) > 0

        # But the bare app does not enforce them
        client = TestClient(_make_bare_app())
        resp = client.post("/", json={"jsonrpc": "2.0", "method": "ping", "id": 1})
        assert resp.status_code == 200  # No 401 — gap confirmed


# ── 2. Middleware unit tests ─────────────────────────────────────────────────


class TestA2AAuthMiddlewarePublicPaths:
    """Verify that discovery endpoints are accessible without auth."""

    @patch(
        "agent_service.a2a.auth_middleware.A2A_AUTH_ENABLED",
        True,
    )
    def test_agent_json_allowed_without_auth(self):
        client = TestClient(_make_protected_app())
        resp = client.get("/.well-known/agent.json")
        assert resp.status_code == 200
        assert resp.text == "card-ok"

    @patch(
        "agent_service.a2a.auth_middleware.A2A_AUTH_ENABLED",
        True,
    )
    def test_agent_card_json_allowed_without_auth(self):
        client = TestClient(_make_protected_app())
        resp = client.get("/.well-known/agent-card.json")
        assert resp.status_code == 200
        assert resp.text == "card-ok"

    def test_public_paths_constant_contains_expected_values(self):
        assert "/.well-known/agent.json" in A2A_PUBLIC_PATHS
        assert "/.well-known/agent-card.json" in A2A_PUBLIC_PATHS


class TestA2AAuthMiddlewareRejectsUnauthenticated:
    """Verify that non-public endpoints require authentication."""

    @patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True)
    @patch(
        "agent_service.a2a.auth_middleware.A2A_AUTH_ENABLED",
        True,
    )
    def test_rpc_rejected_without_authorization_header(self):
        """POST / without Authorization header must return 401."""
        client = TestClient(_make_protected_app())
        resp = client.post("/", json={"jsonrpc": "2.0", "method": "ping", "id": 1})
        assert resp.status_code == 401
        body = resp.json()
        assert "error" in body

    @patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True)
    @patch(
        "agent_service.a2a.auth_middleware.A2A_AUTH_ENABLED",
        True,
    )
    @patch("agent_service.jwt_auth.validate_bearer_token")
    def test_rpc_rejected_with_invalid_bearer_token(self, mock_validate):
        """POST / with an invalid Bearer token must return 401."""
        mock_validate.side_effect = JWTAuthError("Authentication failed")
        client = TestClient(_make_protected_app())
        resp = client.post(
            "/",
            json={"jsonrpc": "2.0", "method": "ping", "id": 1},
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert "error" in body

    @patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True)
    @patch(
        "agent_service.a2a.auth_middleware.A2A_AUTH_ENABLED",
        True,
    )
    def test_error_response_is_generic_no_details_leaked(self):
        """401 responses must NOT leak JWT validation specifics."""
        client = TestClient(_make_protected_app())
        resp = client.post("/", json={"jsonrpc": "2.0", "method": "ping", "id": 1})
        body = resp.json()
        # Only a generic error key — no "detail", "reason", "token", etc.
        assert set(body.keys()) == {"error"}
        error_text = body["error"].lower()
        assert "expired" not in error_text
        assert "audience" not in error_text
        assert "signature" not in error_text
        assert "jwks" not in error_text


class TestA2AAuthMiddlewareAllowsAuthenticated:
    """Verify that correctly authenticated requests pass through."""

    @patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True)
    @patch(
        "agent_service.a2a.auth_middleware.A2A_AUTH_ENABLED",
        True,
    )
    @patch("agent_service.jwt_auth.validate_bearer_token")
    def test_rpc_allowed_with_valid_bearer_token(self, mock_validate):
        """POST / with a valid Bearer token must reach the handler."""
        mock_validate.return_value = _make_valid_claims()
        client = TestClient(_make_protected_app())
        resp = client.post(
            "/",
            json={"jsonrpc": "2.0", "method": "ping", "id": 1},
            headers={"Authorization": "Bearer valid-token"},
        )
        assert resp.status_code == 200
        assert resp.text == "rpc-ok"
        mock_validate.assert_called_once_with("Bearer valid-token")


class TestA2AAuthMiddlewareDisabled:
    """Verify that A2A_AUTH_ENABLED=false bypasses enforcement."""

    @patch(
        "agent_service.a2a.auth_middleware.A2A_AUTH_ENABLED",
        False,
    )
    def test_rpc_allowed_when_auth_disabled(self):
        """When A2A_AUTH_ENABLED is false, requests pass without auth."""
        client = TestClient(_make_protected_app())
        resp = client.post("/", json={"jsonrpc": "2.0", "method": "ping", "id": 1})
        assert resp.status_code == 200
        assert resp.text == "rpc-ok"


class TestA2AAuthMiddlewareJWTDisabledGlobally:
    """Verify behavior when JWT_VALIDATION_ENABLED is false globally."""

    @patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", False)
    @patch(
        "agent_service.a2a.auth_middleware.A2A_AUTH_ENABLED",
        True,
    )
    def test_rpc_allowed_when_jwt_validation_disabled(self):
        """When JWT_VALIDATION_ENABLED is false, middleware logs a
        warning but lets requests through (matches global behavior)."""
        client = TestClient(_make_protected_app())
        resp = client.post("/", json={"jsonrpc": "2.0", "method": "ping", "id": 1})
        assert resp.status_code == 200
        assert resp.text == "rpc-ok"


# ── 3. Integration with server.py ────────────────────────────────────────────


class TestA2AAuthMiddlewareServerIntegration:
    """Verify that _build_a2a_app adds A2AAuthMiddleware to the Starlette app."""

    @patch("agent_service.a2a.server.A2AStarletteApplication")
    @patch("agent_service.a2a.server.DefaultRequestHandler")
    @patch("agent_service.a2a.server.InMemoryTaskStore")
    @patch("agent_service.a2a.server.create_agent_card")
    @patch("agent_service.a2a.server.SpecialistAgentExecutor")
    def test_build_a2a_app_adds_auth_middleware(
        self,
        mock_executor_cls,
        mock_card_fn,
        mock_store_cls,
        mock_handler_cls,
        mock_a2a_app_cls,
    ):
        from agent_service.a2a.server import _build_a2a_app

        config = {"name": "test-agent", "departments": ["test"]}
        mock_a2a_instance = MagicMock()
        mock_starlette_app = MagicMock()
        mock_a2a_instance.build.return_value = mock_starlette_app
        mock_a2a_app_cls.return_value = mock_a2a_instance

        result = _build_a2a_app("test-agent", config, "http://localhost:8080/")

        # The starlette app must have add_middleware called with A2AAuthMiddleware
        mock_starlette_app.add_middleware.assert_called_once_with(A2AAuthMiddleware)
        # The returned app is the same starlette app (with middleware added)
        assert result is mock_starlette_app

    @patch("agent_service.a2a.server.A2AStarletteApplication")
    @patch("agent_service.a2a.server.DefaultRequestHandler")
    @patch("agent_service.a2a.server.InMemoryTaskStore")
    @patch("agent_service.a2a.server.create_agent_card")
    @patch("agent_service.a2a.server.SpecialistAgentExecutor")
    def test_get_a2a_app_returns_app_with_middleware(
        self,
        mock_executor_cls,
        mock_card_fn,
        mock_store_cls,
        mock_handler_cls,
        mock_a2a_app_cls,
        monkeypatch,
    ):
        from agent_service.a2a.server import get_a2a_app

        monkeypatch.setenv(
            "TEST_AGENT_A2A_URL",
            "http://localhost:8080/a2a/test-agent/",
        )

        config = {"name": "test-agent", "departments": ["test"]}
        mock_a2a_instance = MagicMock()
        mock_starlette_app = MagicMock()
        mock_a2a_instance.build.return_value = mock_starlette_app
        mock_a2a_app_cls.return_value = mock_a2a_instance

        result = get_a2a_app("test-agent", config)

        mock_starlette_app.add_middleware.assert_called_once_with(A2AAuthMiddleware)
        assert result is mock_starlette_app
