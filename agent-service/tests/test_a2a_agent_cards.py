"""Tests for agent_service.a2a.agent_cards."""

from agent_service.a2a.agent_cards import create_agent_card

# Minimal configs that mirror the YAML structure
SOFTWARE_CONFIG = {
    "name": "software-support",
    "description": "Handles software issues",
    "departments": ["software"],
    "a2a": {
        "card_name": "Software Support Agent",
        "card_description": "Software support specialist.",
        "skills": [
            {
                "id": "software_troubleshooting",
                "name": "Software Troubleshooting",
                "description": "Diagnoses software issues.",
                "tags": ["software", "bugs", "errors", "crashes", "troubleshooting"],
                "examples": ["My app crashes"],
            },
            {
                "id": "software_installation",
                "name": "Software Installation & Configuration",
                "description": "Assists with installation.",
                "tags": ["installation", "configuration", "setup", "dependencies"],
                "examples": ["How do I install this?"],
            },
        ],
    },
}

NETWORK_CONFIG = {
    "name": "network-support",
    "description": "Handles network issues",
    "departments": ["network"],
    "a2a": {
        "card_name": "Network Support Agent",
        "card_description": "Network support specialist.",
        "skills": [
            {
                "id": "network_diagnostics",
                "name": "Network Diagnostics & Troubleshooting",
                "description": "Diagnoses network issues.",
                "tags": [
                    "network",
                    "connectivity",
                    "VPN",
                    "DNS",
                    "firewall",
                    "routing",
                ],
                "examples": ["VPN keeps dropping"],
            },
            {
                "id": "network_configuration",
                "name": "Network Configuration",
                "description": "Assists with configuration.",
                "tags": ["configuration", "firewall-rules", "VPN-setup", "DNS-config"],
                "examples": ["How do I set up DNS?"],
            },
        ],
    },
}

KUBERNETES_CONFIG = {
    "name": "kubernetes-support",
    "description": "Handles Kubernetes issues, pod failures, deployment problems",
    "departments": ["kubernetes"],
    "a2a": {
        "card_name": "Kubernetes Support Agent",
        "card_description": "Kubernetes support specialist.",
        "skills": [
            {
                "id": "kubernetes_troubleshooting",
                "name": "Kubernetes Troubleshooting",
                "description": "Diagnoses Kubernetes issues.",
                "tags": ["kubernetes", "pods", "deployments", "containers", "k8s"],
                "examples": ["My pods are in CrashLoopBackOff"],
            },
            {
                "id": "kubernetes_configuration",
                "name": "Kubernetes Configuration & Best Practices",
                "description": "Assists with Kubernetes resource configuration.",
                "tags": ["configuration", "YAML", "resources", "limits", "probes"],
                "examples": ["How do I set resource limits?"],
            },
        ],
    },
}


class TestCreateAgentCard:
    """Tests for the generic create_agent_card function."""

    def test_software_support_card(self):
        card = create_agent_card(
            "software-support",
            SOFTWARE_CONFIG,
            "http://localhost:8080/a2a/software-support/",
        )
        assert card.name == "Software Support Agent"

    def test_network_support_card(self):
        card = create_agent_card(
            "network-support",
            NETWORK_CONFIG,
            "http://localhost:8080/a2a/network-support/",
        )
        assert card.name == "Network Support Agent"

    def test_base_url_is_set(self):
        url = "http://example.com/a2a/sw/"
        card = create_agent_card("software-support", SOFTWARE_CONFIG, url)
        assert card.url == url

    def test_has_skills(self):
        card = create_agent_card(
            "software-support", SOFTWARE_CONFIG, "http://localhost:8080/"
        )
        assert len(card.skills) == 2
        skill_ids = [s.id for s in card.skills]
        assert "software_troubleshooting" in skill_ids
        assert "software_installation" in skill_ids

    def test_capabilities(self):
        card = create_agent_card(
            "software-support", SOFTWARE_CONFIG, "http://localhost:8080/"
        )
        assert card.capabilities.streaming is False
        assert card.capabilities.push_notifications is False
        assert card.capabilities.state_transition_history is True

    def test_protocol_version(self):
        card = create_agent_card(
            "software-support", SOFTWARE_CONFIG, "http://localhost:8080/"
        )
        assert card.protocol_version == "0.3.0"

    def test_version(self):
        card = create_agent_card(
            "software-support", SOFTWARE_CONFIG, "http://localhost:8080/"
        )
        assert card.version == "0.1.0"

    def test_network_skills_have_tags(self):
        card = create_agent_card(
            "network-support", NETWORK_CONFIG, "http://localhost:8080/"
        )
        diag_skill = next(s for s in card.skills if s.id == "network_diagnostics")
        assert "VPN" in diag_skill.tags
        assert "DNS" in diag_skill.tags

    def test_fallback_card_name(self):
        """When a2a.card_name is missing, falls back to agent name."""
        config = {"name": "db-support", "description": "DB agent"}
        card = create_agent_card("db-support", config, "http://localhost:8080/")
        assert card.name == "Db Support Agent"

    def test_fallback_description(self):
        """When a2a.card_description is missing, falls back to top-level description."""
        config = {"name": "db-support", "description": "Handles DB issues"}
        card = create_agent_card("db-support", config, "http://localhost:8080/")
        assert card.description == "Handles DB issues"

    def test_kubernetes_support_card(self):
        card = create_agent_card(
            "kubernetes-support",
            KUBERNETES_CONFIG,
            "http://localhost:8080/a2a/kubernetes-support/",
        )
        assert card.name == "Kubernetes Support Agent"

    def test_kubernetes_skills_have_tags(self):
        card = create_agent_card(
            "kubernetes-support", KUBERNETES_CONFIG, "http://localhost:8080/"
        )
        troubleshoot_skill = next(
            s for s in card.skills if s.id == "kubernetes_troubleshooting"
        )
        assert "kubernetes" in troubleshoot_skill.tags
        assert "pods" in troubleshoot_skill.tags

    def test_kubernetes_has_two_skills(self):
        card = create_agent_card(
            "kubernetes-support", KUBERNETES_CONFIG, "http://localhost:8080/"
        )
        assert len(card.skills) == 2
        skill_ids = [s.id for s in card.skills]
        assert "kubernetes_troubleshooting" in skill_ids
        assert "kubernetes_configuration" in skill_ids

    def test_empty_skills(self):
        """Config with no skills produces a card with empty skills list."""
        config = {"name": "empty-agent", "a2a": {"card_name": "Empty"}}
        card = create_agent_card("empty-agent", config, "http://localhost:8080/")
        assert card.skills == []
