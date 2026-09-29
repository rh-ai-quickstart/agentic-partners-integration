"""Tests for JWT Authentication Middleware."""

import time
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from kubernetes_agent.auth_middleware import JWTAuthMiddleware

# ---------------------------------------------------------------------------
# Helpers – minimal FastAPI app with the middleware under test
# ---------------------------------------------------------------------------

FAKE_KEYCLOAK = "https://keycloak.example.com"
FAKE_REALM = "partner-agent"
FAKE_ISSUER = f"{FAKE_KEYCLOAK}/realms/{FAKE_REALM}"

# RSA key pair generated once for test signing (HS256 would be simpler but
# the middleware hardcodes RS256, so we need an RSA-flavoured mock).
from cryptography.hazmat.primitives.asymmetric import rsa

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_key = _private_key.public_key()


def _build_app() -> FastAPI:
    """Return a tiny FastAPI app with the middleware applied."""
    app = FastAPI()
    app.add_middleware(JWTAuthMiddleware)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/.well-known/agent.json")
    async def agent_card():
        return {"name": "kubernetes-support"}

    @app.post("/api/v1/agents/kubernetes-support/invoke")
    async def invoke(request: Request):
        claims = getattr(request.state, "jwt_claims", None)
        return {"result": "ok", "claims": claims}

    return app


def _make_token(
    issuer: str = FAKE_ISSUER,
    exp_offset: int = 3600,
    extra_claims: dict | None = None,
) -> str:
    """Create a signed RS256 JWT for testing."""
    now = int(time.time())
    payload = {
        "iss": issuer,
        "sub": "test-user",
        "iat": now,
        "exp": now + exp_offset,
    }
    if extra_claims:
        payload.update(extra_claims)
    return pyjwt.encode(payload, _private_key, algorithm="RS256")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def _auth_enabled():
    """Patch module-level globals so auth is enabled with a real Keycloak URL."""
    with (
        patch("kubernetes_agent.auth_middleware.JWT_AUTH_ENABLED", True),
        patch("kubernetes_agent.auth_middleware.KEYCLOAK_URL", FAKE_KEYCLOAK),
        patch("kubernetes_agent.auth_middleware.KEYCLOAK_REALM", FAKE_REALM),
    ):
        yield


@pytest.fixture()
def _auth_disabled():
    """Patch module-level globals so auth is disabled."""
    with patch("kubernetes_agent.auth_middleware.JWT_AUTH_ENABLED", False):
        yield


@pytest.fixture()
def _no_keycloak_url():
    """Auth enabled but KEYCLOAK_URL is empty (graceful degradation)."""
    with (
        patch("kubernetes_agent.auth_middleware.JWT_AUTH_ENABLED", True),
        patch("kubernetes_agent.auth_middleware.KEYCLOAK_URL", ""),
    ):
        yield


@pytest.fixture()
def client():
    """Test client wrapping the tiny app."""
    return TestClient(_build_app())


# ---------------------------------------------------------------------------
# Mock helpers for JWKS
# ---------------------------------------------------------------------------

def _mock_jwks_returns_valid_key():
    """Return a patch context that makes _get_jwks_client return a mock
    whose get_signing_key_from_jwt yields the test public key."""
    mock_signing_key = MagicMock()
    mock_signing_key.key = _public_key

    mock_client = MagicMock()
    mock_client.get_signing_key_from_jwt.return_value = mock_signing_key

    return patch(
        "kubernetes_agent.auth_middleware._get_jwks_client",
        return_value=mock_client,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPublicPaths:
    """Public (unauthenticated) endpoints must be reachable without a JWT."""

    @pytest.mark.usefixtures("_auth_enabled")
    def test_health_endpoint_no_auth_required(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    @pytest.mark.usefixtures("_auth_enabled")
    def test_agent_card_discovery_no_auth(self, client: TestClient):
        resp = client.get("/.well-known/agent.json")
        assert resp.status_code == 200
        assert resp.json()["name"] == "kubernetes-support"


class TestPathTraversalPrevention:
    """Ensure public path checks use exact match, not suffix matching."""

    @pytest.mark.usefixtures("_auth_enabled")
    def test_path_ending_with_health_is_not_public(self, client: TestClient):
        """A path like /evil/health must NOT bypass auth via suffix match."""
        with _mock_jwks_returns_valid_key():
            resp = client.get("/evil/health")
        assert resp.status_code in (401, 404)

    @pytest.mark.usefixtures("_auth_enabled")
    def test_nested_well_known_is_not_public(self, client: TestClient):
        """A path like /prefix/.well-known/agent.json must NOT bypass auth."""
        with _mock_jwks_returns_valid_key():
            resp = client.get("/prefix/.well-known/agent.json")
        assert resp.status_code in (401, 404)


class TestMissingOrBadAuth:
    """Requests to protected endpoints without valid credentials must be rejected."""

    @pytest.mark.usefixtures("_auth_enabled")
    def test_invoke_without_auth_returns_401(self, client: TestClient):
        with _mock_jwks_returns_valid_key():
            resp = client.post("/api/v1/agents/kubernetes-support/invoke")
        assert resp.status_code == 401
        assert "error" in resp.json()

    @pytest.mark.usefixtures("_auth_enabled")
    def test_invoke_with_invalid_token_returns_401(self, client: TestClient):
        with _mock_jwks_returns_valid_key():
            resp = client.post(
                "/api/v1/agents/kubernetes-support/invoke",
                headers={"Authorization": "Bearer invalid_token"},
            )
        assert resp.status_code == 401
        assert "error" in resp.json()


class TestValidAuth:
    """A correctly signed, non-expired JWT with the right issuer must be accepted."""

    @pytest.mark.usefixtures("_auth_enabled")
    def test_invoke_with_valid_token_proceeds(self, client: TestClient):
        token = _make_token()
        with _mock_jwks_returns_valid_key():
            resp = client.post(
                "/api/v1/agents/kubernetes-support/invoke",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        assert resp.json()["result"] == "ok"

    @pytest.mark.usefixtures("_auth_enabled")
    def test_claims_attached_to_request_state(self, client: TestClient):
        token = _make_token(extra_claims={"preferred_username": "alice"})
        with _mock_jwks_returns_valid_key():
            resp = client.post(
                "/api/v1/agents/kubernetes-support/invoke",
                headers={"Authorization": f"Bearer {token}"},
            )
        body = resp.json()
        assert resp.status_code == 200
        assert body["claims"]["sub"] == "test-user"
        assert body["claims"]["preferred_username"] == "alice"
        assert body["claims"]["iss"] == FAKE_ISSUER


class TestTokenValidationFailures:
    """Tokens that fail signature, expiry, or issuer checks must be rejected."""

    @pytest.mark.usefixtures("_auth_enabled")
    def test_expired_token_returns_401(self, client: TestClient):
        token = _make_token(exp_offset=-3600)  # expired 1 hour ago
        with _mock_jwks_returns_valid_key():
            resp = client.post(
                "/api/v1/agents/kubernetes-support/invoke",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 401
        assert "error" in resp.json()

    @pytest.mark.usefixtures("_auth_enabled")
    def test_wrong_issuer_returns_401(self, client: TestClient):
        token = _make_token(issuer="https://evil.example.com/realms/hacker")
        with _mock_jwks_returns_valid_key():
            resp = client.post(
                "/api/v1/agents/kubernetes-support/invoke",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 401
        assert "error" in resp.json()


class TestAuthBypasses:
    """When auth is disabled or Keycloak URL is absent, requests pass through."""

    @pytest.mark.usefixtures("_auth_disabled")
    def test_auth_disabled_allows_all(self, client: TestClient):
        resp = client.post("/api/v1/agents/kubernetes-support/invoke")
        assert resp.status_code == 200
        assert resp.json()["result"] == "ok"

    @pytest.mark.usefixtures("_no_keycloak_url")
    def test_no_keycloak_url_skips_validation(self, client: TestClient):
        resp = client.post("/api/v1/agents/kubernetes-support/invoke")
        assert resp.status_code == 200
        assert resp.json()["result"] == "ok"


class TestGetJwksClient:
    """Tests for the _get_jwks_client() lazy initializer."""

    def test_creates_client_when_keycloak_url_set(self):
        """Lines 34-40: lazy client creation when KEYCLOAK_URL is set."""
        from kubernetes_agent.auth_middleware import _get_jwks_client

        with (
            patch("kubernetes_agent.auth_middleware._jwks_client", None),
            patch("kubernetes_agent.auth_middleware.KEYCLOAK_URL", FAKE_KEYCLOAK),
            patch("kubernetes_agent.auth_middleware.KEYCLOAK_REALM", FAKE_REALM),
            patch("kubernetes_agent.auth_middleware.PyJWKClient") as mock_cls,
        ):
            result = _get_jwks_client()
            mock_cls.assert_called_once_with(
                f"{FAKE_KEYCLOAK}/realms/{FAKE_REALM}/protocol/openid-connect/certs",
                cache_keys=True,
                max_cached_keys=16,
            )
            assert result is mock_cls.return_value

    def test_returns_none_when_no_keycloak_url(self):
        from kubernetes_agent.auth_middleware import _get_jwks_client

        with (
            patch("kubernetes_agent.auth_middleware._jwks_client", None),
            patch("kubernetes_agent.auth_middleware.KEYCLOAK_URL", ""),
        ):
            assert _get_jwks_client() is None


class TestJwksClientNoneFallback:
    """Line 72: defensive fallback when _get_jwks_client() returns None."""

    @pytest.mark.usefixtures("_auth_enabled")
    def test_passes_through_when_jwks_client_returns_none(self, client: TestClient):
        token = _make_token()
        with patch(
            "kubernetes_agent.auth_middleware._get_jwks_client",
            return_value=None,
        ):
            resp = client.post(
                "/api/v1/agents/kubernetes-support/invoke",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        assert resp.json()["result"] == "ok"
