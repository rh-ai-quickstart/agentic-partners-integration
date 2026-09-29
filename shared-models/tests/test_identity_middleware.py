"""Tests for shared_models.identity_middleware module."""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

from shared_models.identity import WorkloadIdentity
from shared_models.identity_middleware import (
    SKIP_PATHS,
    SKIP_PREFIXES,
    IdentityMiddleware,
    _identity_from_bearer,
)


class TestSkipPaths:
    """Tests for SKIP_PATHS constant."""

    def test_contains_health(self):
        assert "/health" in SKIP_PATHS

    def test_contains_health_detailed(self):
        assert "/health/detailed" in SKIP_PATHS

    def test_contains_ready(self):
        assert "/ready" in SKIP_PATHS

    def test_contains_metrics(self):
        assert "/metrics" in SKIP_PATHS

    def test_contains_docs(self):
        assert "/docs" in SKIP_PATHS
        assert "/redoc" in SKIP_PATHS
        assert "/openapi.json" in SKIP_PATHS

    def test_contains_auth_login(self):
        assert "/auth/login" in SKIP_PATHS

    def test_contains_auth_endpoints(self):
        assert "/auth/me" in SKIP_PATHS
        assert "/auth/refresh" in SKIP_PATHS
        assert "/auth/config" in SKIP_PATHS
        assert "/auth/callback" in SKIP_PATHS


class TestSkipPrefixes:
    """Tests for SKIP_PREFIXES constant."""

    def test_contains_auth_prefix(self):
        assert "/auth/" in SKIP_PREFIXES

    def test_contains_docs_prefix(self):
        assert "/docs" in SKIP_PREFIXES

    def test_contains_redoc_prefix(self):
        assert "/redoc" in SKIP_PREFIXES


class TestIdentityMiddlewareDispatch:
    """Tests for IdentityMiddleware.dispatch()."""

    async def test_skip_path_sets_identity_none(self):
        """Requests to skip paths should have identity=None and be passed through."""
        middleware = IdentityMiddleware.__new__(IdentityMiddleware)

        request = MagicMock()
        request.url.path = "/health"
        request.state = MagicMock()

        expected_response = MagicMock()
        call_next = AsyncMock(return_value=expected_response)

        response = await middleware.dispatch(request, call_next)

        assert request.state.identity is None
        call_next.assert_called_once_with(request)
        assert response is expected_response

    async def test_skip_path_docs(self):
        middleware = IdentityMiddleware.__new__(IdentityMiddleware)

        request = MagicMock()
        request.url.path = "/docs"
        request.state = MagicMock()

        call_next = AsyncMock(return_value=MagicMock())
        await middleware.dispatch(request, call_next)

        assert request.state.identity is None

    async def test_skip_prefix_auth(self):
        """Paths starting with /auth/ are skipped."""
        middleware = IdentityMiddleware.__new__(IdentityMiddleware)

        request = MagicMock()
        request.url.path = "/auth/some-new-endpoint"
        request.state = MagicMock()

        call_next = AsyncMock(return_value=MagicMock())
        response = await middleware.dispatch(request, call_next)

        assert request.state.identity is None
        call_next.assert_called_once()

    @patch("shared_models.identity_middleware.extract_identity")
    async def test_normal_path_extracts_identity(self, mock_extract):
        """Non-skip paths should have identity extracted."""
        mock_identity = WorkloadIdentity(spiffe_id="spiffe://example.com/user/alice")
        mock_extract.return_value = mock_identity

        middleware = IdentityMiddleware.__new__(IdentityMiddleware)

        request = MagicMock()
        request.url.path = "/api/sessions"
        request.state = MagicMock()

        expected_response = MagicMock()
        call_next = AsyncMock(return_value=expected_response)

        response = await middleware.dispatch(request, call_next)

        mock_extract.assert_called_once_with(request)
        assert request.state.identity is mock_identity
        assert response is expected_response

    @patch("shared_models.identity_middleware.IDENTITY_ENFORCEMENT", True)
    @patch("shared_models.identity_middleware._identity_from_bearer")
    @patch("shared_models.identity_middleware.extract_identity")
    async def test_deny_by_default_when_no_identity(self, mock_extract, mock_bearer):
        """Requests without any identity return 403 when enforcement is enabled."""
        mock_extract.return_value = None
        mock_bearer.return_value = None

        middleware = IdentityMiddleware.__new__(IdentityMiddleware)

        request = MagicMock()
        request.url.path = "/api/sessions"
        request.state = MagicMock()

        call_next = AsyncMock(return_value=MagicMock())

        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 403
        call_next.assert_not_called()

    @patch("shared_models.identity_middleware.IDENTITY_ENFORCEMENT", False)
    @patch("shared_models.identity_middleware._identity_from_bearer")
    @patch("shared_models.identity_middleware.extract_identity")
    async def test_allows_requests_without_identity_when_disabled(self, mock_extract, mock_bearer):
        """Requests without identity pass through when enforcement is disabled."""
        mock_extract.return_value = None
        mock_bearer.return_value = None

        middleware = IdentityMiddleware.__new__(IdentityMiddleware)

        request = MagicMock()
        request.url.path = "/api/sessions"
        request.state = MagicMock()

        expected_response = MagicMock()
        call_next = AsyncMock(return_value=expected_response)

        response = await middleware.dispatch(request, call_next)

        assert request.state.identity is None
        assert response is expected_response

    @patch("shared_models.identity_middleware.extract_identity")
    async def test_jwt_fallback_when_no_spiffe(self, mock_extract):
        """Bearer token identity is used when SPIFFE identity is absent."""
        mock_extract.return_value = None
        jwt_identity = WorkloadIdentity(
            spiffe_id="spiffe://partner.example.com/user/carlos"
        )

        middleware = IdentityMiddleware.__new__(IdentityMiddleware)

        request = MagicMock()
        request.url.path = "/api/v1/chat"
        request.state = MagicMock()

        expected_response = MagicMock()
        call_next = AsyncMock(return_value=expected_response)

        with patch(
            "shared_models.identity_middleware._identity_from_bearer",
            return_value=jwt_identity,
        ):
            response = await middleware.dispatch(request, call_next)

        assert request.state.identity is jwt_identity
        assert response is expected_response

    @patch("shared_models.identity_middleware.extract_identity")
    async def test_spiffe_takes_precedence_over_jwt(self, mock_extract):
        """SPIFFE identity is preferred over JWT-derived identity."""
        spiffe_identity = WorkloadIdentity(
            spiffe_id="spiffe://partner.example.com/service/request-manager"
        )
        mock_extract.return_value = spiffe_identity

        middleware = IdentityMiddleware.__new__(IdentityMiddleware)

        request = MagicMock()
        request.url.path = "/api/v1/chat"
        request.state = MagicMock()

        expected_response = MagicMock()
        call_next = AsyncMock(return_value=expected_response)

        response = await middleware.dispatch(request, call_next)

        assert request.state.identity is spiffe_identity
        assert response is expected_response


class TestIdentityFromBearer:
    """Tests for _identity_from_bearer helper."""

    def _make_token(self, payload: dict) -> str:
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        sig = base64.urlsafe_b64encode(b"fakesig").rstrip(b"=").decode()
        return f"{header}.{body}.{sig}"

    def test_extracts_preferred_username(self):
        token = self._make_token({"preferred_username": "alice", "sub": "uuid-123"})
        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {token}"}
        identity = _identity_from_bearer(request)
        assert identity is not None
        assert identity.spiffe_id == "spiffe://partner.example.com/user/alice"

    def test_falls_back_to_sub(self):
        token = self._make_token({"sub": "uuid-123"})
        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {token}"}
        identity = _identity_from_bearer(request)
        assert identity is not None
        assert identity.spiffe_id == "spiffe://partner.example.com/user/uuid-123"

    def test_falls_back_to_azp(self):
        token = self._make_token({"azp": "partner-agent-ui"})
        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {token}"}
        identity = _identity_from_bearer(request)
        assert identity is not None
        assert identity.spiffe_id == "spiffe://partner.example.com/user/partner-agent-ui"

    def test_returns_none_without_bearer(self):
        request = MagicMock()
        request.headers = {}
        assert _identity_from_bearer(request) is None

    def test_returns_none_for_non_bearer(self):
        request = MagicMock()
        request.headers = {"Authorization": "Basic abc123"}
        assert _identity_from_bearer(request) is None

    def test_returns_none_for_invalid_token(self):
        request = MagicMock()
        request.headers = {"Authorization": "Bearer not-a-jwt"}
        assert _identity_from_bearer(request) is None

    def test_returns_none_for_empty_claims(self):
        token = self._make_token({})
        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {token}"}
        assert _identity_from_bearer(request) is None

    def test_returns_none_for_corrupt_payload(self):
        """Three-part token where the payload is not valid base64/JSON."""
        request = MagicMock()
        request.headers = {"Authorization": "Bearer header.not~valid~b64.sig"}
        assert _identity_from_bearer(request) is None
