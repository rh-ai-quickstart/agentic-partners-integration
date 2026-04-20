"""Tests for agent_service.schemas."""

import pytest
from agent_service.schemas import AgentInvokeRequest, AgentInvokeResponse
from pydantic import ValidationError


class TestAgentInvokeRequest:
    """Tests for AgentInvokeRequest validation."""

    def test_valid_request(self):
        req = AgentInvokeRequest(
            session_id="sess-1",
            user_id="user@example.com",
            message="Hello",
        )
        assert req.session_id == "sess-1"
        assert req.user_id == "user@example.com"
        assert req.message == "Hello"
        assert req.transfer_context is None

    def test_valid_request_with_transfer_context(self):
        ctx = {"departments": ["software"]}
        req = AgentInvokeRequest(
            session_id="sess-2",
            user_id="user2@example.com",
            message="Help me",
            transfer_context=ctx,
        )
        assert req.transfer_context == ctx

    def test_missing_session_id_raises(self):
        with pytest.raises(ValidationError):
            AgentInvokeRequest(
                user_id="u1",
                message="Hello",
            )

    def test_missing_user_id_raises(self):
        with pytest.raises(ValidationError):
            AgentInvokeRequest(
                session_id="s1",
                message="Hello",
            )

    def test_missing_message_raises(self):
        with pytest.raises(ValidationError):
            AgentInvokeRequest(
                session_id="s1",
                user_id="u1",
            )

    def test_empty_message_raises(self):
        with pytest.raises(ValidationError):
            AgentInvokeRequest(
                session_id="s1",
                user_id="u1",
                message="",
            )

    def test_transfer_context_is_optional(self):
        req = AgentInvokeRequest(
            session_id="s1",
            user_id="u1",
            message="Hi",
        )
        assert req.transfer_context is None


class TestAgentInvokeResponse:
    """Tests for AgentInvokeResponse field defaults."""

    def test_required_fields_only(self):
        resp = AgentInvokeResponse(
            content="response text",
            agent_id="routing-agent",
            session_id="sess-1",
        )
        assert resp.content == "response text"
        assert resp.agent_id == "routing-agent"
        assert resp.session_id == "sess-1"
        assert resp.routing_decision is None
        assert resp.metadata is None

    def test_all_fields(self):
        resp = AgentInvokeResponse(
            content="response",
            agent_id="sw-agent",
            session_id="sess-2",
            routing_decision="software-support",
            metadata={"key": "value"},
        )
        assert resp.routing_decision == "software-support"
        assert resp.metadata == {"key": "value"}

    def test_missing_content_raises(self):
        with pytest.raises(ValidationError):
            AgentInvokeResponse(
                agent_id="a",
                session_id="s",
            )
