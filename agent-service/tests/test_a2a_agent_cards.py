"""Tests for agent_service.a2a.agent_cards."""

from agent_service.a2a.agent_cards import (
    create_network_support_card,
    create_software_support_card,
)


class TestSoftwareSupportCard:
    """Tests for create_software_support_card()."""

    def test_returns_agent_card(self):
        card = create_software_support_card("http://localhost:8080/a2a/software-support/")
        assert card.name == "Software Support Agent"

    def test_base_url_is_set(self):
        url = "http://example.com/a2a/sw/"
        card = create_software_support_card(url)
        assert card.url == url

    def test_has_skills(self):
        card = create_software_support_card("http://localhost:8080/")
        assert len(card.skills) == 2
        skill_ids = [s.id for s in card.skills]
        assert "software_troubleshooting" in skill_ids
        assert "software_installation" in skill_ids

    def test_capabilities(self):
        card = create_software_support_card("http://localhost:8080/")
        assert card.capabilities.streaming is False
        assert card.capabilities.push_notifications is False
        assert card.capabilities.state_transition_history is True

    def test_protocol_version(self):
        card = create_software_support_card("http://localhost:8080/")
        assert card.protocol_version == "0.3.0"

    def test_version(self):
        card = create_software_support_card("http://localhost:8080/")
        assert card.version == "0.1.0"


class TestNetworkSupportCard:
    """Tests for create_network_support_card()."""

    def test_returns_agent_card(self):
        card = create_network_support_card("http://localhost:8080/a2a/network-support/")
        assert card.name == "Network Support Agent"

    def test_base_url_is_set(self):
        url = "http://example.com/a2a/net/"
        card = create_network_support_card(url)
        assert card.url == url

    def test_has_skills(self):
        card = create_network_support_card("http://localhost:8080/")
        assert len(card.skills) == 2
        skill_ids = [s.id for s in card.skills]
        assert "network_diagnostics" in skill_ids
        assert "network_configuration" in skill_ids

    def test_capabilities(self):
        card = create_network_support_card("http://localhost:8080/")
        assert card.capabilities.streaming is False
        assert card.capabilities.push_notifications is False
        assert card.capabilities.state_transition_history is True

    def test_skills_have_tags(self):
        card = create_network_support_card("http://localhost:8080/")
        diag_skill = next(s for s in card.skills if s.id == "network_diagnostics")
        assert "VPN" in diag_skill.tags
        assert "DNS" in diag_skill.tags
