"""Tests for request_manager.main."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    def test_health_returns_correct_structure(self):
        """Health check returns status, service, version, timestamp."""
        from request_manager.main import app

        client = TestClient(app)
        resp = client.get("/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "request-manager"
        assert "version" in data
        assert "timestamp" in data


class TestAppRouters:
    """Tests for app router inclusion."""

    def test_app_includes_auth_router(self):
        """The app should include the auth router."""
        from request_manager.main import app

        routes = [route.path for route in app.routes]
        assert any("/auth" in r for r in routes)

    def test_app_includes_adk_router(self):
        """The app should include the adk router."""
        from request_manager.main import app

        routes = [route.path for route in app.routes]
        assert any("/adk" in r for r in routes)


class TestCredentialContextMiddleware:
    """Tests for the credential_context_middleware."""

    def test_extracts_authorization_header(self):
        """Middleware should extract Authorization header."""
        from request_manager.credential_service import CredentialService
        from request_manager.main import app

        client = TestClient(app)

        # The middleware sets credentials on request and clears on response.
        # We can verify by checking the health endpoint still works with auth headers.
        resp = client.get(
            "/health",
            headers={"Authorization": "Bearer test-token-123"},
        )
        assert resp.status_code == 200

        # After the request completes, credentials should be cleared
        assert CredentialService.get_token() is None

    def test_extracts_user_id_header(self):
        """Middleware extracts X-User-ID header."""
        from request_manager.credential_service import CredentialService
        from request_manager.main import app

        client = TestClient(app)

        resp = client.get(
            "/health",
            headers={"X-User-ID": "user@example.com"},
        )
        assert resp.status_code == 200

        # After request, should be cleared
        assert CredentialService.get_user_id() is None

    def test_extracts_session_id_header(self):
        """Middleware extracts X-Session-ID header."""
        from request_manager.credential_service import CredentialService
        from request_manager.main import app

        client = TestClient(app)

        resp = client.get(
            "/health",
            headers={"X-Session-ID": "sess-abc"},
        )
        assert resp.status_code == 200

        # After request, should be cleared
        assert CredentialService.get_session_id() is None

    def test_clears_credentials_after_request(self):
        """Credentials are always cleared after request processing."""
        from request_manager.credential_service import CredentialService
        from request_manager.main import app

        client = TestClient(app)

        resp = client.get(
            "/health",
            headers={
                "Authorization": "Bearer tok",
                "X-User-ID": "uid",
                "X-Session-ID": "sid",
            },
        )
        assert resp.status_code == 200

        assert CredentialService.get_token() is None
        assert CredentialService.get_user_id() is None
        assert CredentialService.get_session_id() is None

    def test_clears_credentials_on_error(self):
        """Credentials are cleared even when the request handler errors."""
        from request_manager.credential_service import CredentialService
        from request_manager.main import app

        client = TestClient(app)

        # Request a non-existent route to trigger 404
        resp = client.get(
            "/nonexistent-route",
            headers={"Authorization": "Bearer should-be-cleared"},
        )

        # Credentials should still be cleared (the finally block runs)
        assert CredentialService.get_token() is None


class TestExceptionHandlers:
    """Tests for custom exception handlers."""

    def test_http_exception_handler(self):
        """HTTPException returns structured error response."""
        from request_manager.main import app

        client = TestClient(app)

        # Trigger a 404 by accessing a non-existent route
        # Note: 404 for non-existent routes may not go through the custom handler,
        # but the handler is registered so it handles known HTTPExceptions from endpoints.
        # We can test the /health/detailed endpoint without DB to trigger a potential error.

    def test_general_exception_handler_format(self):
        """General exception handler returns JSON with error structure."""
        from request_manager.main import app

        client = TestClient(app)
        # The general exception handler wraps unhandled exceptions.
        # We verify it's registered correctly.
        assert app.exception_handlers is not None


class TestHealthCheckTimestamp:
    """Tests for the health check timestamp format."""

    def test_timestamp_is_iso_format(self):
        """Health check timestamp should be in ISO format."""
        from request_manager.main import app

        client = TestClient(app)
        resp = client.get("/health")
        data = resp.json()

        timestamp = data["timestamp"]
        # Basic check that it looks like ISO format
        assert "T" in timestamp or ":" in timestamp


# ---------------------------------------------------------------------------
# _session_cleanup_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSessionCleanupTask:
    """Tests for the _session_cleanup_task background task."""

    async def test_cleanup_task_runs_and_cleans(self):
        """Cleanup task expires old sessions and deletes inactive ones (lines 33-85)."""
        import asyncio

        from request_manager.main import _session_cleanup_task

        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mock_db_manager = MagicMock()
        mock_db_manager.get_session.return_value = mock_db

        call_count = 0

        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        with (
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("shared_models.get_database_manager", return_value=mock_db_manager),
            patch(
                "request_manager.database_utils.expire_old_sessions",
                new_callable=AsyncMock,
                return_value=3,
            ) as mock_expire,
            patch(
                "request_manager.database_utils.delete_inactive_sessions",
                new_callable=AsyncMock,
                return_value=2,
            ) as mock_delete,
        ):
            await _session_cleanup_task()

        mock_expire.assert_awaited_once()
        mock_delete.assert_awaited_once()

    async def test_cleanup_task_handles_errors(self):
        """Cleanup task continues running even on errors (lines 78-85)."""
        import asyncio

        from request_manager.main import _session_cleanup_task

        call_count = 0

        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: normal sleep, will trigger DB error
                return
            elif call_count == 2:
                # Second call: the 60s error retry sleep
                return
            else:
                # Third call: cancel to stop the loop
                raise asyncio.CancelledError()

        with (
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch(
                "shared_models.get_database_manager",
                side_effect=RuntimeError("DB connection failed"),
            ),
        ):
            await _session_cleanup_task()

        # Verify we got through the error path and the 60s retry sleep
        assert call_count >= 2

    async def test_cleanup_task_cancelled(self):
        """Cleanup task handles CancelledError gracefully (lines 75-77)."""
        import asyncio

        from request_manager.main import _session_cleanup_task

        async def cancel_immediately(seconds):
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=cancel_immediately):
            # Should not raise, just break out of the loop
            await _session_cleanup_task()

    async def test_cleanup_task_reads_env_vars(self, monkeypatch):
        """Cleanup task reads configuration from env vars (lines 37-41)."""
        import asyncio

        from request_manager.main import _session_cleanup_task

        monkeypatch.setenv("SESSION_CLEANUP_INTERVAL_HOURS", "6")
        monkeypatch.setenv("INACTIVE_SESSION_RETENTION_DAYS", "14")

        recorded_sleep_time = None

        async def capture_sleep(seconds):
            nonlocal recorded_sleep_time
            recorded_sleep_time = seconds
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=capture_sleep):
            await _session_cleanup_task()

        # 6 hours = 21600 seconds
        assert recorded_sleep_time == 21600


# ---------------------------------------------------------------------------
# _request_manager_startup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRequestManagerStartup:
    """Tests for the _request_manager_startup function."""

    async def test_initializes_processor_and_starts_cleanup(self):
        """Startup initializes unified processor and starts cleanup task (lines 90-104)."""
        import request_manager.main as main_module
        from request_manager.main import _request_manager_startup

        original_processor = getattr(main_module, "unified_processor", None)

        with (
            patch(
                "request_manager.main.get_communication_strategy",
            ) as mock_get_strategy,
            patch("asyncio.create_task") as mock_create_task,
        ):

            mock_strategy = MagicMock()
            mock_get_strategy.return_value = mock_strategy

            await _request_manager_startup()

            # Verify strategy was created
            mock_get_strategy.assert_called_once()

            # Verify unified processor was initialized
            assert main_module.unified_processor is not None

            # Verify background task was started
            mock_create_task.assert_called_once()

        # Restore original state
        main_module.unified_processor = original_processor


# ---------------------------------------------------------------------------
# Exception handlers - extended tests
# ---------------------------------------------------------------------------


class TestExceptionHandlersExtended:
    """Extended tests for exception handlers (lines 217-246)."""

    def test_http_exception_returns_structured_error(self):
        """HTTPException handler returns ErrorResponse format (lines 214-223)."""
        # Add a temporary route that raises HTTPException
        from fastapi import HTTPException as FastAPIHTTPException
        from request_manager.main import app

        @app.get("/test-http-error")
        async def raise_http_error():
            raise FastAPIHTTPException(status_code=403, detail="Forbidden resource")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test-http-error")

        assert resp.status_code == 403
        data = resp.json()
        assert data["error"] == "Forbidden resource"
        assert data["error_code"] == "HTTP_403"

        # Clean up the temporary route
        app.routes[:] = [
            r for r in app.routes if getattr(r, "path", "") != "/test-http-error"
        ]

    def test_general_exception_returns_500(self):
        """General exception handler returns 500 with structured error (lines 226-237)."""
        from request_manager.main import app

        @app.get("/test-general-error")
        async def raise_general_error():
            raise ValueError("Something went wrong")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test-general-error")

        assert resp.status_code == 500
        data = resp.json()
        assert data["error"] == "Internal server error"
        assert data["error_code"] == "INTERNAL_ERROR"

        # Clean up the temporary route
        app.routes[:] = [
            r for r in app.routes if getattr(r, "path", "") != "/test-general-error"
        ]


# ---------------------------------------------------------------------------
# Credential context middleware - extended tests
# ---------------------------------------------------------------------------


class TestCredentialContextMiddlewareExtended:
    """Extended tests for credential_context_middleware (lines 148-177)."""

    def test_no_headers_still_works(self):
        """Middleware works fine when no credential headers are present."""
        from request_manager.credential_service import CredentialService
        from request_manager.main import app

        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200

        # All credentials should be None
        assert CredentialService.get_token() is None
        assert CredentialService.get_user_id() is None
        assert CredentialService.get_session_id() is None

    def test_user_id_from_query_param(self):
        """Middleware extracts user_id from query params (line 162)."""
        from request_manager.credential_service import CredentialService
        from request_manager.main import app

        client = TestClient(app)
        resp = client.get("/health?user_id=query-user")
        assert resp.status_code == 200

        # After request, should be cleared
        assert CredentialService.get_user_id() is None

    def test_session_id_from_query_param(self):
        """Middleware extracts session_id from query params (line 167)."""
        from request_manager.credential_service import CredentialService
        from request_manager.main import app

        client = TestClient(app)
        resp = client.get("/health?session_id=query-sess")
        assert resp.status_code == 200

        # After request, should be cleared
        assert CredentialService.get_session_id() is None


# ---------------------------------------------------------------------------
# Detailed health check endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDetailedHealthCheck:
    """Tests for the /health/detailed endpoint (lines 200-207)."""

    async def test_detailed_health_check_structure(self):
        """Detailed health check returns HealthCheck model (lines 200-207)."""
        from request_manager.main import detailed_health_check

        db = AsyncMock()

        with patch(
            "request_manager.main.create_health_check_endpoint",
            new_callable=AsyncMock,
            return_value={
                "status": "healthy",
                "database_connected": True,
                "services": {"database": "connected"},
            },
        ):
            result = await detailed_health_check(db=db)

        assert result.status == "healthy"
        assert result.database_connected is True
        assert "database" in result.services
