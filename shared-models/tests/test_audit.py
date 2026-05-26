"""Tests for shared_models.audit — SOC 2 audit event service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestAuditService:
    """Tests for AuditService.emit()."""

    @patch("shared_models.database.get_database_manager")
    async def test_emit_success(self, mock_get_db_manager):
        """Successfully writes an audit event to the database."""
        from shared_models.audit import AuditService

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        mock_db_manager = MagicMock()
        mock_db_manager.get_session = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_session),
                __aexit__=AsyncMock(return_value=None),
            )
        )
        mock_get_db_manager.return_value = mock_db_manager

        await AuditService.emit(
            event_type="auth.login.success",
            actor="user@example.com",
            action="login",
            resource="/api/v1/chat",
            outcome="success",
            reason="Valid JWT",
            metadata={"departments": ["engineering"]},
            source_ip="192.168.1.1",
            service="request-manager",
        )

        mock_session.add.assert_called_once()
        mock_session.commit.assert_awaited_once()

        event = mock_session.add.call_args[0][0]
        assert event.event_type == "auth.login.success"
        assert event.actor == "user@example.com"
        assert event.action == "login"
        assert event.resource == "/api/v1/chat"
        assert event.outcome == "success"
        assert event.reason == "Valid JWT"
        assert event.metadata_ == {"departments": ["engineering"]}
        assert event.source_ip == "192.168.1.1"
        assert event.service == "request-manager"

    @patch("shared_models.database.get_database_manager")
    async def test_emit_truncates_long_reason(self, mock_get_db_manager):
        """Reason field is truncated to 1000 chars."""
        from shared_models.audit import AuditService

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        mock_db_manager = MagicMock()
        mock_db_manager.get_session = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_session),
                __aexit__=AsyncMock(return_value=None),
            )
        )
        mock_get_db_manager.return_value = mock_db_manager

        long_reason = "x" * 2000
        await AuditService.emit(
            event_type="auth.denied",
            actor="user@example.com",
            action="access",
            reason=long_reason,
        )

        event = mock_session.add.call_args[0][0]
        assert len(event.reason) == 1000

    @patch("shared_models.database.get_database_manager")
    async def test_emit_truncates_long_source_ip(self, mock_get_db_manager):
        """source_ip field is truncated to 45 chars."""
        from shared_models.audit import AuditService

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        mock_db_manager = MagicMock()
        mock_db_manager.get_session = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_session),
                __aexit__=AsyncMock(return_value=None),
            )
        )
        mock_get_db_manager.return_value = mock_db_manager

        long_ip = "a" * 100
        await AuditService.emit(
            event_type="test",
            actor="user@example.com",
            action="test",
            source_ip=long_ip,
        )

        event = mock_session.add.call_args[0][0]
        assert len(event.source_ip) == 45

    @patch("shared_models.database.get_database_manager")
    async def test_emit_defaults_for_optional_fields(self, mock_get_db_manager):
        """Optional fields default to empty strings/dicts."""
        from shared_models.audit import AuditService

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        mock_db_manager = MagicMock()
        mock_db_manager.get_session = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_session),
                __aexit__=AsyncMock(return_value=None),
            )
        )
        mock_get_db_manager.return_value = mock_db_manager

        await AuditService.emit(
            event_type="test.event",
            actor="admin@example.com",
            action="test",
        )

        event = mock_session.add.call_args[0][0]
        assert event.resource == ""
        assert event.outcome == "success"
        assert event.reason == ""
        assert event.metadata_ == {}
        assert event.source_ip == ""
        assert event.service == ""

    @patch("shared_models.audit.logger")
    async def test_emit_logs_error_on_db_failure(self, mock_logger):
        """Database failure is logged but does not raise."""
        from shared_models.audit import AuditService

        with patch("shared_models.database.get_database_manager", side_effect=RuntimeError("DB connection lost")):

            # Should not raise
            await AuditService.emit(
                event_type="test.event",
                actor="user@example.com",
                action="test",
            )

        mock_logger.error.assert_called_once()
        call_kwargs = mock_logger.error.call_args
        assert "Failed to write audit event" in call_kwargs[0][0]

    @patch("shared_models.database.get_database_manager")
    async def test_emit_generates_uuid_event_id(self, mock_get_db_manager):
        """Each event gets a unique UUID event_id."""
        from shared_models.audit import AuditService

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        mock_db_manager = MagicMock()
        mock_db_manager.get_session = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_session),
                __aexit__=AsyncMock(return_value=None),
            )
        )
        mock_get_db_manager.return_value = mock_db_manager

        await AuditService.emit(
            event_type="test",
            actor="user@example.com",
            action="test",
        )

        event = mock_session.add.call_args[0][0]
        import uuid

        uuid.UUID(event.event_id)  # Should not raise
