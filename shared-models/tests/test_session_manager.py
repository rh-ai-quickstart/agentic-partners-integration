"""Tests for shared_models.session_manager module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shared_models.models import IntegrationType, SessionStatus
from shared_models.session_manager import BaseSessionManager
from shared_models.session_schemas import SessionCreate
from sqlalchemy.exc import IntegrityError


def _make_mock_session_row():
    """Create a mock RequestSession ORM row for model_validate."""
    row = MagicMock()
    row.session_id = "sess-123"
    row.user_id = "user-123"
    row.integration_type = IntegrationType.WEB
    row.status = SessionStatus.ACTIVE
    row.current_agent_id = None
    row.conversation_thread_id = None
    row.conversation_context = {}
    row.integration_metadata = {}
    row.user_context = {}
    row.total_requests = 0
    row.last_request_id = None
    row.version = 0
    from datetime import datetime, timezone

    row.created_at = datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)
    row.last_request_at = None
    return row


class TestCreateSession:
    """Tests for BaseSessionManager.create_session()."""

    async def test_creates_session_successfully(self, mock_db_session):
        mock_row = _make_mock_session_row()
        mock_db_session.refresh = AsyncMock(return_value=None)

        # After add and commit, refresh should make the session available
        # We patch model_validate to return a response based on the mock row
        manager = BaseSessionManager(mock_db_session)

        session_data = SessionCreate(
            user_id="user-123",
            integration_type="WEB",
        )

        with patch(
            "shared_models.session_manager.SessionResponse.model_validate"
        ) as mock_validate:
            mock_validate.return_value = MagicMock(session_id="sess-123")

            result = await manager.create_session(session_data)

            mock_db_session.add.assert_called_once()
            mock_db_session.commit.assert_called_once()
            mock_db_session.refresh.assert_called_once()
            assert result.session_id == "sess-123"

    async def test_retries_on_integrity_error(self, mock_db_session):
        """On IntegrityError with active session constraint, should retry."""
        manager = BaseSessionManager(mock_db_session)

        session_data = SessionCreate(
            user_id="user-123",
            integration_type="WEB",
        )

        # First call: IntegrityError; then get_active_session returns existing
        integrity_err = IntegrityError(
            statement="INSERT",
            params=None,
            orig=Exception(
                "duplicate key idx_one_active_session_per_user_integration unique active"
            ),
        )
        mock_db_session.commit.side_effect = [integrity_err]

        mock_existing_row = _make_mock_session_row()
        mock_existing_row.session_id = "existing-sess"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_existing_row
        mock_db_session.execute.return_value = mock_result

        with patch(
            "shared_models.session_manager.SessionResponse.model_validate"
        ) as mock_validate:
            mock_validate.return_value = MagicMock(session_id="existing-sess")
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await manager.create_session(session_data)

            assert result.session_id == "existing-sess"
            mock_db_session.rollback.assert_called()


class TestGetActiveSession:
    """Tests for BaseSessionManager.get_active_session()."""

    async def test_returns_active_session(self, mock_db_session):
        mock_row = _make_mock_session_row()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row
        mock_db_session.execute.return_value = mock_result

        manager = BaseSessionManager(mock_db_session)
        result = await manager.get_active_session("user-123", IntegrationType.WEB)

        assert result is mock_row
        mock_db_session.execute.assert_called_once()

    async def test_returns_none_when_no_active_session(self, mock_db_session):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute.return_value = mock_result

        manager = BaseSessionManager(mock_db_session)
        result = await manager.get_active_session("user-123", IntegrationType.WEB)

        assert result is None


class TestGetSession:
    """Tests for BaseSessionManager.get_session()."""

    async def test_returns_session_response(self, mock_db_session):
        mock_row = _make_mock_session_row()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row
        mock_db_session.execute.return_value = mock_result

        manager = BaseSessionManager(mock_db_session)

        with patch(
            "shared_models.session_manager.SessionResponse.model_validate"
        ) as mock_validate:
            mock_validate.return_value = MagicMock(session_id="sess-123")
            result = await manager.get_session("sess-123")

        assert result is not None
        assert result.session_id == "sess-123"

    async def test_returns_none_when_not_found(self, mock_db_session):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute.return_value = mock_result

        manager = BaseSessionManager(mock_db_session)
        result = await manager.get_session("nonexistent")

        assert result is None

    async def test_with_for_update(self, mock_db_session):
        """get_session with for_update=True should still work."""
        mock_row = _make_mock_session_row()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row
        mock_db_session.execute.return_value = mock_result

        manager = BaseSessionManager(mock_db_session)

        with patch(
            "shared_models.session_manager.SessionResponse.model_validate"
        ) as mock_validate:
            mock_validate.return_value = MagicMock(session_id="sess-123")
            result = await manager.get_session("sess-123", for_update=True)

        assert result is not None


class TestUpdateSession:
    """Tests for BaseSessionManager.update_session() with optimistic locking."""

    async def test_update_session_successfully(self, mock_db_session):
        mock_updated_row = _make_mock_session_row()
        mock_updated_row.version = 1
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_updated_row
        mock_db_session.execute.return_value = mock_result

        manager = BaseSessionManager(mock_db_session)

        with patch(
            "shared_models.session_manager.SessionResponse.model_validate"
        ) as mock_validate:
            mock_validate.return_value = MagicMock(session_id="sess-123", version=1)
            result = await manager.update_session(
                "sess-123",
                agent_id="agent-1",
                expected_version=0,
            )

        assert result is not None
        assert result.version == 1
        mock_db_session.commit.assert_called_once()

    async def test_returns_none_on_version_mismatch(self, mock_db_session):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute.return_value = mock_result

        manager = BaseSessionManager(mock_db_session)
        result = await manager.update_session(
            "sess-123",
            agent_id="agent-1",
            expected_version=5,
        )

        assert result is None

    async def test_update_session_without_version_check(self, mock_db_session):
        mock_updated_row = _make_mock_session_row()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_updated_row
        mock_db_session.execute.return_value = mock_result

        manager = BaseSessionManager(mock_db_session)

        with patch(
            "shared_models.session_manager.SessionResponse.model_validate"
        ) as mock_validate:
            mock_validate.return_value = MagicMock(session_id="sess-123")
            result = await manager.update_session(
                "sess-123",
                status=SessionStatus.INACTIVE,
            )

        assert result is not None


class TestUpdateSessionAllKwargs:
    """Tests for BaseSessionManager.update_session() with all kwargs."""

    async def test_update_with_all_parameters(self, mock_db_session):
        """update_session with all optional parameters set."""
        mock_updated_row = _make_mock_session_row()
        mock_updated_row.version = 1
        mock_updated_row.current_agent_id = "agent-new"
        mock_updated_row.conversation_thread_id = "thread-new"
        mock_updated_row.status = SessionStatus.INACTIVE
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_updated_row
        mock_db_session.execute.return_value = mock_result

        manager = BaseSessionManager(mock_db_session)

        with patch(
            "shared_models.session_manager.SessionResponse.model_validate"
        ) as mock_validate:
            mock_validate.return_value = MagicMock(
                session_id="sess-123",
                version=1,
                current_agent_id="agent-new",
            )
            result = await manager.update_session(
                "sess-123",
                agent_id="agent-new",
                conversation_thread_id="thread-new",
                status=SessionStatus.INACTIVE,
                conversation_context={"key": "value"},
                user_context={"user_key": "user_value"},
                expected_version=0,
            )

        assert result is not None
        assert result.version == 1
        mock_db_session.execute.assert_called_once()
        mock_db_session.commit.assert_called_once()

    async def test_update_with_status_string_in_kwargs(self, mock_db_session):
        """update_session with status passed as string via kwargs (backward compat)."""
        mock_updated_row = _make_mock_session_row()
        mock_updated_row.version = 1
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_updated_row
        mock_db_session.execute.return_value = mock_result

        manager = BaseSessionManager(mock_db_session)

        with patch(
            "shared_models.session_manager.SessionResponse.model_validate"
        ) as mock_validate:
            mock_validate.return_value = MagicMock(session_id="sess-123")
            result = await manager.update_session(
                "sess-123",
                status="INACTIVE",
            )

        assert result is not None

    async def test_update_with_string_status_enum_value(self, mock_db_session):
        """update_session where status has .value attribute (SessionStatus enum)."""
        mock_updated_row = _make_mock_session_row()
        mock_updated_row.version = 1
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_updated_row
        mock_db_session.execute.return_value = mock_result

        manager = BaseSessionManager(mock_db_session)

        with patch(
            "shared_models.session_manager.SessionResponse.model_validate"
        ) as mock_validate:
            mock_validate.return_value = MagicMock(session_id="sess-123")
            result = await manager.update_session(
                "sess-123",
                status=SessionStatus.ACTIVE,
            )

        assert result is not None

    async def test_update_returns_none_no_expected_version(self, mock_db_session):
        """update_session returns None when session not found (without version check)."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute.return_value = mock_result

        manager = BaseSessionManager(mock_db_session)
        result = await manager.update_session(
            "nonexistent",
            agent_id="agent-1",
        )

        assert result is None


class TestCreateSessionRetryExhaustion:
    """Tests for BaseSessionManager.create_session() retry exhaustion."""

    async def test_raises_after_max_retries(self, mock_db_session):
        """create_session should raise IntegrityError after exhausting retries."""
        manager = BaseSessionManager(mock_db_session)

        session_data = SessionCreate(
            user_id="user-123",
            integration_type="WEB",
        )

        # Every commit raises the constraint violation
        integrity_err = IntegrityError(
            statement="INSERT",
            params=None,
            orig=Exception(
                "duplicate key idx_one_active_session_per_user_integration unique active"
            ),
        )
        mock_db_session.commit.side_effect = integrity_err

        # get_active_session returns None each time (no existing session found)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute.return_value = mock_result

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(IntegrityError):
                await manager.create_session(session_data, max_retries=3)

    async def test_non_constraint_integrity_error_reraises(self, mock_db_session):
        """create_session should re-raise non-constraint IntegrityError immediately."""
        manager = BaseSessionManager(mock_db_session)

        session_data = SessionCreate(
            user_id="user-123",
            integration_type="WEB",
        )

        integrity_err = IntegrityError(
            statement="INSERT",
            params=None,
            orig=Exception("foreign key violation on some_other_column"),
        )
        mock_db_session.commit.side_effect = integrity_err

        with pytest.raises(IntegrityError):
            await manager.create_session(session_data, max_retries=3)

        # Should have rolled back but NOT retried (only 1 attempt)
        mock_db_session.rollback.assert_called_once()


class TestIncrementRequestCount:
    """Tests for BaseSessionManager.increment_request_count()."""

    async def test_increments_count(self, mock_db_session):
        manager = BaseSessionManager(mock_db_session)
        await manager.increment_request_count("sess-123", "req-456")

        mock_db_session.execute.assert_called_once()
        mock_db_session.commit.assert_called_once()
