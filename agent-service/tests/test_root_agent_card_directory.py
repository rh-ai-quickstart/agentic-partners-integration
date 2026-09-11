"""Integration tests for the root /.well-known/agent-card.json endpoint.

Verifies that the agent-service gateway card directory is served at the
expected URL so external callers can discover all specialist agents.
"""

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """FastAPI test client for the agent-service app."""
    from agent_service.main import app
    return TestClient(app, raise_server_exceptions=False)


class TestRootAgentCardDirectory:
    def test_endpoint_returns_200(self, client):
        resp = client.get("/.well-known/agent-card.json")
        assert resp.status_code == 200

    def test_response_is_json(self, client):
        resp = client.get("/.well-known/agent-card.json")
        data = resp.json()
        assert isinstance(data, dict)

    def test_response_has_agent_cards_key(self, client):
        resp = client.get("/.well-known/agent-card.json")
        data = resp.json()
        assert "agent_cards" in data

    def test_response_has_gateway_key(self, client):
        resp = client.get("/.well-known/agent-card.json")
        data = resp.json()
        assert "gateway" in data

    def test_agent_cards_maps_names_to_urls(self, client):
        resp = client.get("/.well-known/agent-card.json")
        data = resp.json()
        agent_cards = data["agent_cards"]
        assert isinstance(agent_cards, dict)
        # At least one agent must be registered
        assert len(agent_cards) > 0

    def test_each_card_url_ends_with_well_known_path(self, client):
        resp = client.get("/.well-known/agent-card.json")
        data = resp.json()
        for name, url in data["agent_cards"].items():
            assert "/.well-known/agent-card.json" in url, (
                f"Card URL for {name} does not contain /.well-known/agent-card.json: {url}"
            )

    def test_each_card_url_contains_agent_name(self, client):
        resp = client.get("/.well-known/agent-card.json")
        data = resp.json()
        for name, url in data["agent_cards"].items():
            assert name in url, (
                f"Card URL for agent '{name}' does not contain the agent name: {url}"
            )

    def test_response_has_note_field(self, client):
        resp = client.get("/.well-known/agent-card.json")
        data = resp.json()
        assert "note" in data
        assert "security_schemes" in data["note"].lower() or "oauth" in data["note"].lower()
