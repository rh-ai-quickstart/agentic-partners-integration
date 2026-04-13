"""Tests for request_manager.schemas."""

import pytest
from pydantic import ValidationError

from request_manager.schemas import BaseRequest, HealthCheck, WebRequest
from shared_models.models import IntegrationType


class TestBaseRequest:
    """Tests for BaseRequest schema validation."""

    def test_valid_base_request(self):
        """A properly formed BaseRequest should parse without error."""
        req = BaseRequest(
            integration_type=IntegrationType.WEB,
            user_id="user@example.com",
            content="Hello, agent!",
        )
        assert req.integration_type == IntegrationType.WEB
        assert req.user_id == "user@example.com"
        assert req.content == "Hello, agent!"
        assert req.request_type == "message"
        assert req.metadata == {}

    def test_user_id_min_length(self):
        """user_id must have at least 1 character."""
        with pytest.raises(ValidationError) as exc_info:
            BaseRequest(
                integration_type=IntegrationType.WEB,
                user_id="",
                content="Hello",
            )
        assert "user_id" in str(exc_info.value)

    def test_user_id_max_length(self):
        """user_id must not exceed 255 characters."""
        with pytest.raises(ValidationError) as exc_info:
            BaseRequest(
                integration_type=IntegrationType.WEB,
                user_id="x" * 256,
                content="Hello",
            )
        assert "user_id" in str(exc_info.value)

    def test_content_min_length(self):
        """content must have at least 1 character."""
        with pytest.raises(ValidationError) as exc_info:
            BaseRequest(
                integration_type=IntegrationType.WEB,
                user_id="user1",
                content="",
            )
        assert "content" in str(exc_info.value)

    def test_content_required(self):
        """content is a required field."""
        with pytest.raises(ValidationError):
            BaseRequest(
                integration_type=IntegrationType.WEB,
                user_id="user1",
            )

    def test_user_id_required(self):
        """user_id is a required field."""
        with pytest.raises(ValidationError):
            BaseRequest(
                integration_type=IntegrationType.WEB,
                content="some content",
            )

    def test_integration_type_required(self):
        """integration_type is a required field."""
        with pytest.raises(ValidationError):
            BaseRequest(
                user_id="user1",
                content="some content",
            )

    def test_request_type_default(self):
        """request_type defaults to 'message'."""
        req = BaseRequest(
            integration_type=IntegrationType.WEB,
            user_id="user1",
            content="content",
        )
        assert req.request_type == "message"

    def test_request_type_max_length(self):
        """request_type must not exceed 100 characters."""
        with pytest.raises(ValidationError):
            BaseRequest(
                integration_type=IntegrationType.WEB,
                user_id="user1",
                content="content",
                request_type="x" * 101,
            )

    def test_metadata_default_empty_dict(self):
        """metadata defaults to an empty dict."""
        req = BaseRequest(
            integration_type=IntegrationType.WEB,
            user_id="user1",
            content="content",
        )
        assert req.metadata == {}

    def test_metadata_accepts_dict(self):
        """metadata can be provided as a dict."""
        req = BaseRequest(
            integration_type=IntegrationType.WEB,
            user_id="user1",
            content="content",
            metadata={"key": "value", "nested": {"a": 1}},
        )
        assert req.metadata["key"] == "value"

    def test_normalize_integration_type_lowercase(self):
        """The validator should accept lowercase integration_type strings."""
        req = BaseRequest(
            integration_type="web",
            user_id="user1",
            content="content",
        )
        assert req.integration_type == IntegrationType.WEB

    def test_normalize_integration_type_mixed_case(self):
        """The validator should accept mixed-case integration_type strings."""
        req = BaseRequest(
            integration_type="Web",
            user_id="user1",
            content="content",
        )
        assert req.integration_type == IntegrationType.WEB

    def test_normalize_integration_type_enum_value(self):
        """The validator should accept an IntegrationType enum value directly."""
        req = BaseRequest(
            integration_type=IntegrationType.WEB,
            user_id="user1",
            content="content",
        )
        assert req.integration_type == IntegrationType.WEB

    def test_invalid_integration_type(self):
        """An invalid integration_type string should raise ValidationError."""
        with pytest.raises(ValidationError):
            BaseRequest(
                integration_type="INVALID",
                user_id="user1",
                content="content",
            )


class TestWebRequest:
    """Tests for WebRequest schema."""

    def test_default_integration_type(self):
        """WebRequest defaults integration_type to WEB."""
        req = WebRequest(
            user_id="user@example.com",
            content="Hello!",
        )
        assert req.integration_type == IntegrationType.WEB

    def test_optional_fields_default_none(self):
        """Optional fields default to None."""
        req = WebRequest(
            user_id="user1",
            content="content",
        )
        assert req.session_token is None
        assert req.client_ip is None
        assert req.user_agent is None

    def test_optional_fields_set(self):
        """Optional fields can be set."""
        req = WebRequest(
            user_id="user1",
            content="content",
            session_token="tok123",
            client_ip="192.168.1.1",
            user_agent="Mozilla/5.0",
        )
        assert req.session_token == "tok123"
        assert req.client_ip == "192.168.1.1"
        assert req.user_agent == "Mozilla/5.0"

    def test_session_token_max_length(self):
        """session_token must not exceed 500 characters."""
        with pytest.raises(ValidationError):
            WebRequest(
                user_id="user1",
                content="content",
                session_token="x" * 501,
            )

    def test_client_ip_max_length(self):
        """client_ip must not exceed 45 characters."""
        with pytest.raises(ValidationError):
            WebRequest(
                user_id="user1",
                content="content",
                client_ip="x" * 46,
            )

    def test_user_agent_max_length(self):
        """user_agent must not exceed 500 characters."""
        with pytest.raises(ValidationError):
            WebRequest(
                user_id="user1",
                content="content",
                user_agent="x" * 501,
            )

    def test_inherits_base_request_fields(self):
        """WebRequest inherits all BaseRequest fields."""
        req = WebRequest(
            user_id="user1",
            content="content",
            request_type="custom",
            metadata={"source": "test"},
        )
        assert req.request_type == "custom"
        assert req.metadata == {"source": "test"}


class TestHealthCheck:
    """Tests for HealthCheck schema."""

    def test_defaults(self):
        """HealthCheck has sensible defaults."""
        hc = HealthCheck()
        assert hc.status == "healthy"
        assert hc.version == "0.1.0"
        assert hc.database_connected is False
        assert hc.services == {}
        assert hc.timestamp is not None

    def test_custom_values(self):
        """HealthCheck accepts custom values."""
        hc = HealthCheck(
            status="degraded",
            version="1.0.0",
            database_connected=True,
            services={"agent": "ok"},
        )
        assert hc.status == "degraded"
        assert hc.version == "1.0.0"
        assert hc.database_connected is True
        assert hc.services == {"agent": "ok"}
