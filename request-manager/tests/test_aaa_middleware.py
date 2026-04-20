"""Tests for request_manager.aaa_middleware."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from request_manager.aaa_middleware import AAAMiddleware


@pytest.mark.asyncio
class TestAAAMiddleware:
    """Tests for AAAMiddleware.get_user_context()."""

    @patch("request_manager.aaa_middleware.AAAService")
    async def test_get_user_context_user_found(self, mock_aaa_service, mock_db_session):
        """When user is found, return full context with departments."""
        # Arrange
        mock_user = MagicMock()
        mock_user.user_id = "uid-123"
        mock_user.primary_email = "alice@example.com"
        mock_user.role = MagicMock()
        mock_user.role.value = "engineer"
        mock_user.status = "active"
        mock_user.organization = "Acme"
        mock_user.department = "engineering"
        mock_user.spiffe_id = "spiffe://example.com/user/alice"
        mock_user.privileges = {"can_deploy": True}

        mock_aaa_service.get_user_by_email = AsyncMock(return_value=mock_user)
        mock_aaa_service.get_user_departments = AsyncMock(
            return_value=["engineering", "software"]
        )

        # Act
        ctx = await AAAMiddleware.get_user_context(mock_db_session, "alice@example.com")

        # Assert
        assert ctx["user_id"] == "uid-123"
        assert ctx["email"] == "alice@example.com"
        assert ctx["role"] == "engineer"
        assert ctx["status"] == "active"
        assert ctx["organization"] == "Acme"
        assert ctx["department"] == "engineering"
        assert ctx["departments"] == ["engineering", "software"]
        assert ctx["spiffe_id"] == "spiffe://example.com/user/alice"
        assert ctx["privileges"] == {"can_deploy": True}

    @patch("request_manager.aaa_middleware.AAAService")
    async def test_get_user_context_user_not_found(
        self, mock_aaa_service, mock_db_session
    ):
        """When user is not found, return minimal fallback context."""
        mock_aaa_service.get_user_by_email = AsyncMock(return_value=None)

        ctx = await AAAMiddleware.get_user_context(
            mock_db_session, "unknown@example.com"
        )

        assert ctx["email"] == "unknown@example.com"
        assert ctx["role"] == "user"
        assert ctx["status"] == "unknown"
        assert ctx["departments"] == []
        assert "user_id" not in ctx

    @patch("request_manager.aaa_middleware.AAAService")
    async def test_get_user_context_handles_exception(
        self, mock_aaa_service, mock_db_session
    ):
        """When an exception occurs, return error fallback context."""
        mock_aaa_service.get_user_by_email = AsyncMock(
            side_effect=RuntimeError("DB connection lost")
        )

        ctx = await AAAMiddleware.get_user_context(mock_db_session, "error@example.com")

        assert ctx["email"] == "error@example.com"
        assert ctx["role"] == "user"
        assert ctx["status"] == "error"
        assert ctx["departments"] == []

    @patch("request_manager.aaa_middleware.AAAService")
    async def test_get_user_context_user_role_none(
        self, mock_aaa_service, mock_db_session
    ):
        """When user.role is None, default to 'user'."""
        mock_user = MagicMock()
        mock_user.user_id = "uid-456"
        mock_user.primary_email = "norole@example.com"
        mock_user.role = None
        mock_user.status = "active"
        mock_user.organization = None
        mock_user.department = None
        mock_user.spiffe_id = None
        mock_user.privileges = None

        mock_aaa_service.get_user_by_email = AsyncMock(return_value=mock_user)
        mock_aaa_service.get_user_departments = AsyncMock(return_value=[])

        ctx = await AAAMiddleware.get_user_context(
            mock_db_session, "norole@example.com"
        )

        assert ctx["role"] == "user"
        # privileges comes from mock_user.privileges; when None, the code
        # passes it through directly so it can be None or {} depending on mock
        assert ctx["privileges"] is None or ctx["privileges"] == {}

    @patch("request_manager.aaa_middleware.AAAService")
    async def test_get_user_context_departments_exception(
        self, mock_aaa_service, mock_db_session
    ):
        """When get_user_departments raises, the outer except catches it."""
        mock_user = MagicMock()
        mock_user.user_id = "uid-789"
        mock_user.primary_email = "dept-err@example.com"

        mock_aaa_service.get_user_by_email = AsyncMock(return_value=mock_user)
        mock_aaa_service.get_user_departments = AsyncMock(
            side_effect=RuntimeError("departments query failed")
        )

        ctx = await AAAMiddleware.get_user_context(
            mock_db_session, "dept-err@example.com"
        )

        # The outer except block catches and returns error context
        assert ctx["status"] == "error"
        assert ctx["departments"] == []
