"""Tests for request_manager.adk_endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from request_manager.adk_endpoints import _append_conversation_turn

# ---------------------------------------------------------------------------
# _append_conversation_turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAppendConversationTurn:
    """Tests for the _append_conversation_turn helper."""

    async def test_appends_user_and_agent_messages(self):
        """Should append a user message and an agent response to the session."""
        mock_session = MagicMock()
        mock_session.conversation_context = {"messages": []}

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = mock_session

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        await _append_conversation_turn(
            db,
            session_id="sess-1",
            user_message="Hello",
            agent_response="Hi there!",
            agent_name="routing-agent",
        )

        ctx = mock_session.conversation_context
        assert len(ctx["messages"]) == 2
        assert ctx["messages"][0] == {"role": "user", "content": "Hello"}
        assert ctx["messages"][1]["role"] == "assistant"
        assert ctx["messages"][1]["content"] == "Hi there!"
        assert ctx["messages"][1]["agent"] == "routing-agent"

        db.commit.assert_awaited_once()

    async def test_truncates_to_40_entries(self):
        """When messages exceed 40, keep only the last 40."""
        # Start with 39 existing messages
        existing = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg-{i}"}
            for i in range(39)
        ]

        mock_session = MagicMock()
        mock_session.conversation_context = {"messages": existing.copy()}

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = mock_session

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        await _append_conversation_turn(
            db,
            session_id="sess-2",
            user_message="new user msg",
            agent_response="new agent response",
            agent_name="test-agent",
        )

        ctx = mock_session.conversation_context
        # 39 + 2 = 41, truncated to 40
        assert len(ctx["messages"]) == 40

    async def test_strips_thinking_tags(self):
        """Thinking tags in agent responses should be stripped."""
        mock_session = MagicMock()
        mock_session.conversation_context = {}

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = mock_session

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        await _append_conversation_turn(
            db,
            session_id="sess-3",
            user_message="question",
            agent_response="<thinking>internal reasoning</thinking>The actual answer.",
            agent_name="agent-x",
        )

        ctx = mock_session.conversation_context
        agent_msg = ctx["messages"][1]
        assert "<thinking>" not in agent_msg["content"]
        assert "The actual answer." in agent_msg["content"]

    async def test_no_session_found_returns_early(self):
        """When session is not found, should return without error."""
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        # Should not raise
        await _append_conversation_turn(
            db,
            session_id="nonexistent",
            user_message="hello",
            agent_response="world",
            agent_name="agent",
        )

        db.commit.assert_not_awaited()

    async def test_empty_conversation_context(self):
        """When conversation_context is None, start fresh."""
        mock_session = MagicMock()
        mock_session.conversation_context = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = mock_session

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        await _append_conversation_turn(
            db,
            session_id="sess-empty",
            user_message="first message",
            agent_response="first response",
            agent_name="agent",
        )

        ctx = mock_session.conversation_context
        assert len(ctx["messages"]) == 2

    async def test_marks_json_column_modified(self):
        """Should call flag_modified to ensure SQLAlchemy flushes JSON changes."""
        mock_session = MagicMock()
        mock_session.conversation_context = {}

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = mock_session

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        # flag_modified is imported inside the function from sqlalchemy.orm.attributes
        with patch("sqlalchemy.orm.attributes.flag_modified") as mock_flag:
            await _append_conversation_turn(
                db,
                session_id="sess-flag",
                user_message="msg",
                agent_response="resp",
                agent_name="a",
            )
            mock_flag.assert_called_once_with(mock_session, "conversation_context")


# ---------------------------------------------------------------------------
# adk_chat endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAdkChatEndpoint:
    """Tests for the /adk/chat endpoint."""

    @patch("request_manager.adk_endpoints._append_conversation_turn", new_callable=AsyncMock)
    @patch("request_manager.adk_endpoints.UnifiedRequestProcessor")
    @patch("request_manager.adk_endpoints.get_communication_strategy")
    @patch("request_manager.adk_endpoints.AAAMiddleware")
    @patch("request_manager.adk_endpoints.decode_token")
    async def test_adk_chat_success(
        self,
        mock_decode,
        mock_aaa,
        mock_get_strategy,
        mock_processor_cls,
        mock_append,
    ):
        """Successful chat returns ADKChatResponse."""
        from request_manager.adk_endpoints import ADKChatRequest, ADKUser, adk_chat

        mock_decode.return_value = {"email": "user@example.com"}

        mock_aaa.get_user_context = AsyncMock(
            return_value={
                "email": "user@example.com",
                "role": "user",
                "departments": ["engineering"],
            }
        )

        mock_processor = AsyncMock()
        mock_processor.process_request_sync = AsyncMock(
            return_value={
                "content": "Agent response text",
                "agent_id": "routing-agent",
                "session_id": "sess-abc",
                "metadata": {"handling_agent": "routing-agent"},
            }
        )
        mock_processor_cls.return_value = mock_processor

        # Mock HTTP request
        http_request = MagicMock()
        http_request.headers = {"Authorization": "Bearer valid-token"}

        db = AsyncMock()

        request = ADKChatRequest(
            message="Hello agent!",
            user=ADKUser(email="user@example.com"),
        )

        result = await adk_chat(request, http_request, db)

        assert result.response == "Agent response text"
        assert result.session_id == "sess-abc"
        assert result.agent == "routing-agent"

    @patch("request_manager.adk_endpoints.decode_token")
    async def test_adk_chat_requires_auth(self, mock_decode):
        """Missing or invalid auth should raise 401."""
        from request_manager.adk_endpoints import ADKChatRequest, ADKUser, adk_chat

        http_request = MagicMock()
        http_request.headers = {}  # No Authorization header

        db = AsyncMock()
        request = ADKChatRequest(
            message="Hello",
            user=ADKUser(email="user@example.com"),
        )

        with pytest.raises(HTTPException) as exc_info:
            await adk_chat(request, http_request, db)
        assert exc_info.value.status_code == 401

    @patch("request_manager.adk_endpoints._append_conversation_turn", new_callable=AsyncMock)
    @patch("request_manager.adk_endpoints.UnifiedRequestProcessor")
    @patch("request_manager.adk_endpoints.get_communication_strategy")
    @patch("request_manager.adk_endpoints.AAAMiddleware")
    @patch("request_manager.adk_endpoints.decode_token")
    async def test_adk_chat_handles_processor_error(
        self,
        mock_decode,
        mock_aaa,
        mock_get_strategy,
        mock_processor_cls,
        mock_append,
    ):
        """Internal errors should raise 500."""
        from request_manager.adk_endpoints import ADKChatRequest, ADKUser, adk_chat

        mock_decode.return_value = {"email": "user@example.com"}
        mock_aaa.get_user_context = AsyncMock(
            return_value={"email": "user@example.com", "departments": []}
        )

        mock_processor = AsyncMock()
        mock_processor.process_request_sync = AsyncMock(
            side_effect=RuntimeError("Internal failure")
        )
        mock_processor_cls.return_value = mock_processor

        http_request = MagicMock()
        http_request.headers = {"Authorization": "Bearer valid"}

        db = AsyncMock()
        request = ADKChatRequest(
            message="Break things",
            user=ADKUser(email="user@example.com"),
        )

        with pytest.raises(HTTPException) as exc_info:
            await adk_chat(request, http_request, db)
        assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# adk_audit_log endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAdkAuditLog:
    """Tests for the /adk/audit endpoint."""

    @patch("request_manager.adk_endpoints.AAAService")
    @patch("request_manager.adk_endpoints.decode_token")
    async def test_audit_log_returns_entries(self, mock_decode, mock_aaa):
        """Authenticated user gets audit log entries."""
        from request_manager.adk_endpoints import adk_audit_log

        mock_decode.return_value = {"email": "user@example.com"}

        mock_user = MagicMock()
        mock_user.user_id = "uid-1"
        mock_user.role = MagicMock()
        mock_user.role.value = "user"
        mock_aaa.get_user_by_email = AsyncMock(return_value=mock_user)

        # Mock DB results
        mock_log = MagicMock()
        mock_log.request_id = "req-1"
        mock_log.created_at = MagicMock()
        mock_log.created_at.isoformat.return_value = "2025-01-01T00:00:00"
        mock_log.request_content = "test message"
        mock_log.agent_id = "routing-agent"
        mock_log.response_content = "test response"
        mock_log.processing_time_ms = 100
        mock_log.session_id = "sess-1"

        db = AsyncMock()

        # First execute: get log rows; second: get count
        rows_result = MagicMock()
        rows_result.all.return_value = [(mock_log, "uid-1")]

        count_result = MagicMock()
        count_result.scalar.return_value = 1

        db.execute = AsyncMock(side_effect=[rows_result, count_result])

        http_request = MagicMock()
        http_request.headers = {"Authorization": "Bearer valid"}

        result = await adk_audit_log(http_request, limit=50, db=db)

        assert result.total == 1
        assert len(result.entries) == 1
        assert result.user_email == "user@example.com"

    @patch("request_manager.adk_endpoints.AAAService")
    @patch("request_manager.adk_endpoints.decode_token")
    async def test_audit_log_admin_sees_all(self, mock_decode, mock_aaa):
        """Admin users see logs from all users."""
        from request_manager.adk_endpoints import adk_audit_log

        mock_decode.return_value = {"email": "admin@example.com"}

        mock_user = MagicMock()
        mock_user.user_id = "admin-uid"
        mock_user.role = MagicMock()
        mock_user.role.value = "admin"
        mock_aaa.get_user_by_email = AsyncMock(return_value=mock_user)

        mock_log = MagicMock()
        mock_log.request_id = "req-2"
        mock_log.created_at = MagicMock()
        mock_log.created_at.isoformat.return_value = "2025-01-01T00:00:00"
        mock_log.request_content = "admin query"
        mock_log.agent_id = "routing-agent"
        mock_log.response_content = None
        mock_log.processing_time_ms = None
        mock_log.session_id = "sess-2"

        db = AsyncMock()

        rows_result = MagicMock()
        rows_result.all.return_value = [(mock_log, "other-uid")]

        count_result = MagicMock()
        count_result.scalar.return_value = 1

        # Third execute: email lookup
        email_result = MagicMock()
        email_result.all.return_value = [("other-uid", "other@example.com")]

        db.execute = AsyncMock(side_effect=[rows_result, count_result, email_result])

        http_request = MagicMock()
        http_request.headers = {"Authorization": "Bearer admin-token"}

        result = await adk_audit_log(http_request, limit=50, db=db)

        assert result.user_role == "admin"
        # Admin entries are prefixed with user email
        assert "[other@example.com]" in result.entries[0].message

    @patch("request_manager.adk_endpoints.decode_token")
    async def test_audit_log_requires_auth(self, mock_decode):
        """Missing auth raises 401."""
        from request_manager.adk_endpoints import adk_audit_log

        http_request = MagicMock()
        http_request.headers = {}

        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await adk_audit_log(http_request, limit=50, db=db)
        assert exc_info.value.status_code == 401

    @patch("request_manager.adk_endpoints.AAAService")
    @patch("request_manager.adk_endpoints.decode_token")
    async def test_audit_log_user_not_found(self, mock_decode, mock_aaa):
        """When user is not found in DB, raise 404."""
        from request_manager.adk_endpoints import adk_audit_log

        mock_decode.return_value = {"email": "ghost@example.com"}
        mock_aaa.get_user_by_email = AsyncMock(return_value=None)

        http_request = MagicMock()
        http_request.headers = {"Authorization": "Bearer valid"}

        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await adk_audit_log(http_request, limit=50, db=db)
        assert exc_info.value.status_code == 404
