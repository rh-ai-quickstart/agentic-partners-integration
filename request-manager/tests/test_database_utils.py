"""Tests for request_manager.database_utils."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from request_manager.database_utils import (
    cleanup_old_sessions,
    create_request_log_entry_unified,
    delete_inactive_sessions,
    expire_old_sessions,
)


@pytest.mark.asyncio
class TestCreateRequestLogEntryUnified:
    """Tests for create_request_log_entry_unified()."""

    @patch("shared_models.configure_logging")
    @patch("shared_models.models.RequestLog")
    async def test_creates_log_with_db(self, mock_rl_cls, mock_log, mock_db_session):
        """When db is provided, adds the log to that session and commits."""
        with patch(
            "request_manager.communication_strategy.get_pod_name", return_value="pod-abc"
        ):
            await create_request_log_entry_unified(
                request_id="req-1",
                session_id="sess-1",
                user_id="user@example.com",
                content="Help me",
                request_type="message",
                integration_type="WEB",
                db=mock_db_session,
            )

        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_awaited_once()

    @patch("shared_models.configure_logging")
    @patch("shared_models.models.RequestLog")
    async def test_creates_log_without_pod_name(self, mock_rl_cls, mock_log, mock_db_session):
        """When set_pod_name=False, pod_name should not be fetched."""
        await create_request_log_entry_unified(
            request_id="req-2",
            session_id="sess-2",
            user_id="user@example.com",
            content="Help",
            request_type="message",
            integration_type="WEB",
            db=mock_db_session,
            set_pod_name=False,
        )

        mock_db_session.add.assert_called_once()

    @patch("shared_models.configure_logging")
    @patch("shared_models.models.RequestLog", side_effect=Exception("boom"))
    async def test_does_not_raise_on_failure(self, mock_rl_cls, mock_log, mock_db_session):
        """Failure to create a log entry should not propagate."""
        # Should NOT raise
        await create_request_log_entry_unified(
            request_id="req-3",
            session_id="sess-3",
            user_id="user@example.com",
            content="Oops",
            request_type="message",
            integration_type="WEB",
            db=mock_db_session,
        )

    @patch("shared_models.configure_logging")
    @patch("shared_models.models.RequestLog")
    async def test_creates_log_without_db(self, mock_rl_cls, mock_log):
        """When db is None, obtains a session from the database manager."""
        # Set up the async context manager chain
        inner_session = AsyncMock()

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=inner_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_db_mgr = MagicMock()
        mock_db_mgr.get_session.return_value = mock_ctx

        with patch(
            "shared_models.get_database_manager", return_value=mock_db_mgr
        ), patch(
            "request_manager.communication_strategy.get_pod_name", return_value=None
        ):
            await create_request_log_entry_unified(
                request_id="req-4",
                session_id="sess-4",
                user_id="user@example.com",
                content="No DB param",
                request_type="message",
                integration_type="WEB",
                db=None,
            )

        inner_session.add.assert_called_once()
        inner_session.commit.assert_awaited_once()


@pytest.mark.asyncio
class TestCleanupOldSessions:
    """Tests for cleanup_old_sessions()."""

    async def test_keeps_most_recent_deactivates_others(self, mock_db_session):
        """When multiple active sessions exist, keep most recent and deactivate the rest."""
        # Arrange two sessions
        sess1 = MagicMock()
        sess1.session_id = "keep-me"
        sess2 = MagicMock()
        sess2.session_id = "deactivate-me"

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [sess1, sess2]
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        mock_db_session.execute = AsyncMock(return_value=result_mock)

        count = await cleanup_old_sessions(mock_db_session, "user-1")

        assert count == 1
        mock_db_session.commit.assert_awaited()

    async def test_no_sessions_returns_zero(self, mock_db_session):
        """When no active sessions exist, return 0."""
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        mock_db_session.execute = AsyncMock(return_value=result_mock)

        count = await cleanup_old_sessions(mock_db_session, "user-2")

        assert count == 0

    async def test_single_session_no_cleanup(self, mock_db_session):
        """When only one active session exists, no cleanup needed."""
        sess = MagicMock()
        sess.session_id = "only-one"
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [sess]
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        mock_db_session.execute = AsyncMock(return_value=result_mock)

        count = await cleanup_old_sessions(mock_db_session, "user-3")

        assert count == 0

    async def test_exception_returns_zero(self, mock_db_session):
        """When an exception occurs, return 0."""
        mock_db_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))

        count = await cleanup_old_sessions(mock_db_session, "user-err")

        assert count == 0

    async def test_filters_by_integration_type(self, mock_db_session):
        """When integration_type is provided, it should be used for filtering."""
        sess1 = MagicMock()
        sess1.session_id = "keep"
        sess2 = MagicMock()
        sess2.session_id = "deactivate"

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [sess1, sess2]
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        mock_db_session.execute = AsyncMock(return_value=result_mock)

        count = await cleanup_old_sessions(mock_db_session, "user-1", integration_type="WEB")

        assert count == 1


@pytest.mark.asyncio
class TestDeleteInactiveSessions:
    """Tests for delete_inactive_sessions()."""

    async def test_deletes_old_inactive_sessions(self, mock_db_session):
        """Should delete inactive sessions older than specified days."""
        cursor_result = MagicMock()
        cursor_result.rowcount = 5
        mock_db_session.execute = AsyncMock(return_value=cursor_result)

        count = await delete_inactive_sessions(mock_db_session, older_than_days=30)

        assert count == 5
        mock_db_session.commit.assert_awaited_once()

    async def test_returns_zero_on_no_matches(self, mock_db_session):
        """Should return 0 when no sessions match."""
        cursor_result = MagicMock()
        cursor_result.rowcount = 0
        mock_db_session.execute = AsyncMock(return_value=cursor_result)

        count = await delete_inactive_sessions(mock_db_session, older_than_days=7)

        assert count == 0

    async def test_returns_zero_and_rollback_on_error(self, mock_db_session):
        """Should return 0 and rollback on error."""
        mock_db_session.execute = AsyncMock(side_effect=RuntimeError("DB down"))

        count = await delete_inactive_sessions(mock_db_session, older_than_days=30)

        assert count == 0
        mock_db_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
class TestExpireOldSessions:
    """Tests for expire_old_sessions()."""

    async def test_expires_sessions_past_due(self, mock_db_session):
        """Should expire active sessions whose expires_at has passed."""
        cursor_result = MagicMock()
        cursor_result.rowcount = 3
        mock_db_session.execute = AsyncMock(return_value=cursor_result)

        count = await expire_old_sessions(mock_db_session)

        assert count == 3
        mock_db_session.commit.assert_awaited_once()

    async def test_returns_zero_when_none_expired(self, mock_db_session):
        """Should return 0 when no sessions need expiring."""
        cursor_result = MagicMock()
        cursor_result.rowcount = 0
        mock_db_session.execute = AsyncMock(return_value=cursor_result)

        count = await expire_old_sessions(mock_db_session)

        assert count == 0

    async def test_returns_zero_and_rollback_on_error(self, mock_db_session):
        """Should return 0 and rollback on error."""
        mock_db_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))

        count = await expire_old_sessions(mock_db_session)

        assert count == 0
        mock_db_session.rollback.assert_awaited_once()
