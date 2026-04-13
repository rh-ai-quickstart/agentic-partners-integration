"""Tests for shared_models.identity_middleware module."""

from unittest.mock import AsyncMock, MagicMock, patch

from shared_models.identity import WorkloadIdentity
from shared_models.identity_middleware import SKIP_PATHS, IdentityMiddleware


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

    @patch("shared_models.identity_middleware.extract_identity")
    async def test_normal_path_extracts_identity(self, mock_extract):
        """Non-skip paths should have identity extracted."""
        mock_identity = WorkloadIdentity(
            spiffe_id="spiffe://example.com/user/alice"
        )
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

    @patch("shared_models.identity_middleware.extract_identity")
    async def test_allows_requests_without_identity(self, mock_extract):
        """Requests without identity should still be allowed (backward compat)."""
        mock_extract.return_value = None

        middleware = IdentityMiddleware.__new__(IdentityMiddleware)

        request = MagicMock()
        request.url.path = "/api/sessions"
        request.state = MagicMock()

        expected_response = MagicMock()
        call_next = AsyncMock(return_value=expected_response)

        response = await middleware.dispatch(request, call_next)

        assert request.state.identity is None
        assert response is expected_response
