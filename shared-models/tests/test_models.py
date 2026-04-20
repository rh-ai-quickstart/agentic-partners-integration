"""Tests for shared_models.models module."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from shared_models.models import (
    AgentResponse,
    ErrorResponse,
    IntegrationType,
    NormalizedRequest,
    RequestLog,
    RequestSession,
    SessionStatus,
    User,
    UserRole,
)


class TestIntegrationType:
    """Tests for IntegrationType enum."""

    def test_web_value(self):
        assert IntegrationType.WEB.value == "WEB"

    def test_is_string_enum(self):
        assert isinstance(IntegrationType.WEB, str)
        assert IntegrationType.WEB == "WEB"

    def test_from_string(self):
        assert IntegrationType("WEB") == IntegrationType.WEB


class TestSessionStatus:
    """Tests for SessionStatus enum."""

    def test_all_values_exist(self):
        assert SessionStatus.ACTIVE.value == "ACTIVE"
        assert SessionStatus.INACTIVE.value == "INACTIVE"
        assert SessionStatus.EXPIRED.value == "EXPIRED"
        assert SessionStatus.ARCHIVED.value == "ARCHIVED"

    def test_is_string_enum(self):
        assert isinstance(SessionStatus.ACTIVE, str)


class TestUserRole:
    """Tests for UserRole enum."""

    def test_all_values_exist(self):
        assert UserRole.ADMIN.value == "admin"
        assert UserRole.MANAGER.value == "manager"
        assert UserRole.ENGINEER.value == "engineer"
        assert UserRole.SUPPORT_STAFF.value == "support_staff"
        assert UserRole.USER.value == "user"

    def test_is_string_enum(self):
        assert isinstance(UserRole.USER, str)


class TestUserModel:
    """Tests for User ORM model."""

    def test_tablename(self):
        assert User.__tablename__ == "users"


class TestRequestSessionModel:
    """Tests for RequestSession ORM model."""

    def test_tablename(self):
        assert RequestSession.__tablename__ == "request_sessions"


class TestRequestLogModel:
    """Tests for RequestLog ORM model."""

    def test_tablename(self):
        assert RequestLog.__tablename__ == "request_logs"


class TestNormalizedRequest:
    """Tests for NormalizedRequest Pydantic model."""

    def test_valid_creation(self):
        req = NormalizedRequest(
            request_id="req-123",
            session_id="sess-456",
            user_id="user-789",
            integration_type="WEB",
            request_type="query",
            content="Hello world",
        )
        assert req.request_id == "req-123"
        assert req.session_id == "sess-456"
        assert req.user_id == "user-789"
        assert req.integration_type == "WEB"
        assert req.request_type == "query"
        assert req.content == "Hello world"

    def test_default_fields(self):
        req = NormalizedRequest(
            request_id="req-1",
            session_id="sess-1",
            user_id="user-1",
            integration_type="WEB",
            request_type="query",
            content="test",
        )
        assert req.integration_context == {}
        assert req.user_context == {}
        assert req.target_agent_id is None
        assert req.requires_routing is True
        assert isinstance(req.created_at, datetime)

    def test_normalize_integration_type_lowercase(self):
        """Lowercase integration_type should be normalized to uppercase."""
        req = NormalizedRequest(
            request_id="req-1",
            session_id="sess-1",
            user_id="user-1",
            integration_type="web",
            request_type="query",
            content="test",
        )
        assert req.integration_type == "WEB"

    def test_normalize_integration_type_mixed_case(self):
        """Mixed case integration_type should be normalized."""
        req = NormalizedRequest(
            request_id="req-1",
            session_id="sess-1",
            user_id="user-1",
            integration_type="Web",
            request_type="query",
            content="test",
        )
        assert req.integration_type == "WEB"

    def test_missing_required_fields(self):
        """Missing required fields should raise ValidationError."""
        with pytest.raises(ValidationError):
            NormalizedRequest(
                request_id="req-1",
                # missing session_id, user_id, etc.
            )

    def test_empty_content_rejected(self):
        """Empty content string should be rejected (min_length=1)."""
        with pytest.raises(ValidationError):
            NormalizedRequest(
                request_id="req-1",
                session_id="sess-1",
                user_id="user-1",
                integration_type="WEB",
                request_type="query",
                content="",
            )

    def test_empty_user_id_rejected(self):
        """Empty user_id should be rejected (min_length=1)."""
        with pytest.raises(ValidationError):
            NormalizedRequest(
                request_id="req-1",
                session_id="sess-1",
                user_id="",
                integration_type="WEB",
                request_type="query",
                content="test",
            )


class TestAgentResponse:
    """Tests for AgentResponse Pydantic model."""

    def test_valid_creation(self):
        resp = AgentResponse(
            request_id="req-1",
            session_id="sess-1",
            user_id="user-1",
            agent_id="agent-1",
            content="Response text",
        )
        assert resp.request_id == "req-1"
        assert resp.content == "Response text"
        assert resp.agent_id == "agent-1"

    def test_default_fields(self):
        resp = AgentResponse(
            request_id="req-1",
            session_id="sess-1",
            user_id="user-1",
            agent_id=None,
            content="text",
        )
        assert resp.response_type == "message"
        assert resp.metadata == {}
        assert resp.processing_time_ms is None
        assert resp.requires_followup is False
        assert resp.followup_actions == []
        assert isinstance(resp.created_at, datetime)

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            AgentResponse(request_id="req-1")


class TestErrorResponse:
    """Tests for ErrorResponse Pydantic model."""

    def test_valid_creation(self):
        err = ErrorResponse(
            error="Something went wrong",
            error_code="ERR_500",
        )
        assert err.error == "Something went wrong"
        assert err.error_code == "ERR_500"
        assert err.request_id is None
        assert err.details is None
        assert isinstance(err.timestamp, datetime)

    def test_with_optional_fields(self):
        err = ErrorResponse(
            error="Not found",
            error_code="ERR_404",
            request_id="req-1",
            details={"resource": "session"},
        )
        assert err.request_id == "req-1"
        assert err.details == {"resource": "session"}

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            ErrorResponse(error="test")  # missing error_code
