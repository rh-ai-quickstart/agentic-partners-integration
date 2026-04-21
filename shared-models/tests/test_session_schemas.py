"""Tests for shared_models.session_schemas module."""

import pytest
from pydantic import ValidationError

from shared_models.models import IntegrationType, SessionStatus
from shared_models.session_schemas import SessionCreate, SessionResponse


class TestSessionCreate:
    """Tests for SessionCreate Pydantic model."""

    def test_valid_creation(self):
        session = SessionCreate(
            user_id="user-123",
            integration_type="WEB",
        )
        assert session.user_id == "user-123"
        assert session.integration_type == IntegrationType.WEB

    def test_default_fields(self):
        session = SessionCreate(
            user_id="user-123",
            integration_type="WEB",
        )
        assert session.integration_metadata == {}
        assert session.user_context == {}
        assert session.channel_id is None
        assert session.thread_id is None
        assert session.external_session_id is None

    def test_missing_user_id(self):
        with pytest.raises(ValidationError):
            SessionCreate(integration_type="WEB")

    def test_empty_user_id_rejected(self):
        """Empty user_id should fail min_length=1 validation."""
        with pytest.raises(ValidationError):
            SessionCreate(user_id="", integration_type="WEB")

    def test_normalize_integration_type_lowercase(self):
        """Lowercase integration_type should be normalized to uppercase."""
        session = SessionCreate(
            user_id="user-1",
            integration_type="web",
        )
        assert session.integration_type == IntegrationType.WEB

    def test_normalize_integration_type_mixed_case(self):
        session = SessionCreate(
            user_id="user-1",
            integration_type="Web",
        )
        assert session.integration_type == IntegrationType.WEB

    def test_with_optional_fields(self):
        session = SessionCreate(
            user_id="user-123",
            integration_type="WEB",
            channel_id="ch-1",
            thread_id="th-1",
            external_session_id="ext-1",
            integration_metadata={"key": "value"},
            user_context={"name": "Test"},
        )
        assert session.channel_id == "ch-1"
        assert session.thread_id == "th-1"
        assert session.external_session_id == "ext-1"
        assert session.integration_metadata == {"key": "value"}
        assert session.user_context == {"name": "Test"}


class TestSessionResponse:
    """Tests for SessionResponse Pydantic model."""

    def test_from_attributes_config(self):
        """SessionResponse should have from_attributes=True in Config."""
        assert SessionResponse.model_config.get("from_attributes") is True

    def test_valid_creation(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        resp = SessionResponse(
            session_id="sess-1",
            user_id="user-1",
            integration_type=IntegrationType.WEB,
            status=SessionStatus.ACTIVE,
            current_agent_id=None,
            conversation_thread_id=None,
            conversation_context={},
            integration_metadata={},
            user_context={},
            total_requests=0,
            last_request_id=None,
            version=0,
            created_at=now,
            updated_at=now,
        )
        assert resp.session_id == "sess-1"
        assert resp.status == SessionStatus.ACTIVE
        assert resp.total_requests == 0
        assert resp.version == 0

    def test_default_version(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        resp = SessionResponse(
            session_id="sess-1",
            user_id="user-1",
            integration_type=IntegrationType.WEB,
            status=SessionStatus.ACTIVE,
            current_agent_id=None,
            conversation_thread_id=None,
            conversation_context={},
            integration_metadata={},
            user_context={},
            total_requests=0,
            last_request_id=None,
            created_at=now,
            updated_at=now,
        )
        assert resp.version == 0

    def test_last_request_at_default_none(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        resp = SessionResponse(
            session_id="sess-1",
            user_id="user-1",
            integration_type=IntegrationType.WEB,
            status=SessionStatus.ACTIVE,
            current_agent_id=None,
            conversation_thread_id=None,
            conversation_context={},
            integration_metadata={},
            user_context={},
            total_requests=0,
            last_request_id=None,
            created_at=now,
            updated_at=now,
        )
        assert resp.last_request_at is None
