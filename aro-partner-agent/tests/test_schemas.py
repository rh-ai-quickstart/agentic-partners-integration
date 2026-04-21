"""Tests for aro_agent.schemas."""

import pytest
from pydantic import ValidationError

from aro_agent.schemas import AgentInvokeRequest, AgentInvokeResponse


class TestAgentInvokeRequest:
    def test_valid_request(self):
        req = AgentInvokeRequest(
            session_id="s1", user_id="carlos@example.com", message="Help"
        )
        assert req.session_id == "s1"
        assert req.user_id == "carlos@example.com"
        assert req.message == "Help"
        assert req.transfer_context is None

    def test_with_transfer_context(self):
        req = AgentInvokeRequest(
            session_id="s1",
            user_id="user@test.com",
            message="Hi",
            transfer_context={
                "conversation_history": [
                    {"role": "user", "content": "previous"}
                ]
            },
        )
        assert req.transfer_context["conversation_history"][0]["role"] == "user"

    def test_empty_message_rejected(self):
        with pytest.raises(ValidationError):
            AgentInvokeRequest(
                session_id="s1", user_id="user@test.com", message=""
            )

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            AgentInvokeRequest(message="Hi")

    def test_serialization_round_trip(self):
        req = AgentInvokeRequest(
            session_id="s1", user_id="u1", message="test"
        )
        data = req.model_dump()
        restored = AgentInvokeRequest(**data)
        assert restored == req


class TestAgentInvokeResponse:
    def test_valid_response(self):
        resp = AgentInvokeResponse(
            content="Answer here",
            agent_id="aro-support",
            session_id="s1",
        )
        assert resp.content == "Answer here"
        assert resp.routing_decision is None
        assert resp.metadata is None

    def test_response_with_metadata(self):
        resp = AgentInvokeResponse(
            content="Answer",
            agent_id="aro-support",
            session_id="s1",
            metadata={"mcp_enabled": True},
        )
        assert resp.metadata["mcp_enabled"] is True

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            AgentInvokeResponse(content="Answer")

    def test_serialization_round_trip(self):
        resp = AgentInvokeResponse(
            content="Answer",
            agent_id="aro-support",
            session_id="s1",
            routing_decision="network-support",
            metadata={"key": "value"},
        )
        data = resp.model_dump()
        restored = AgentInvokeResponse(**data)
        assert restored == resp
