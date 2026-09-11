"""Tests for agent card security_schemes population (Changes 2 + root card).

Verifies that create_agent_card() now returns machine-readable OAuth2 and
mTLS security requirements so external callers can construct valid A2A
requests without out-of-band documentation.
"""

import os

import pytest
from a2a.types import AgentCard, OAuth2SecurityScheme, MutualTLSSecurityScheme

from agent_service.a2a.agent_cards import create_agent_card


MINIMAL_CONFIG = {
    "name": "kubernetes-support",
    "description": "Handles Kubernetes issues",
    "departments": ["kubernetes"],
    "a2a": {
        "card_name": "Kubernetes Support Agent",
        "card_description": "K8s specialist.",
        "skills": [
            {
                "id": "k8s_troubleshoot",
                "name": "K8s Troubleshooting",
                "description": "Diagnoses pod issues.",
                "tags": ["kubernetes", "pods"],
                "examples": ["Pod in CrashLoopBackOff"],
            }
        ],
    },
}

BASE_URL = "http://agent-service:8080/a2a/kubernetes-support/"


# ── Basic fields (regression guard) ──────────────────────────────────────────

class TestAgentCardBasicFields:
    def test_name_populated(self):
        card = create_agent_card("kubernetes-support", MINIMAL_CONFIG, BASE_URL)
        assert card.name == "Kubernetes Support Agent"

    def test_url_populated(self):
        card = create_agent_card("kubernetes-support", MINIMAL_CONFIG, BASE_URL)
        assert card.url == BASE_URL

    def test_skills_populated(self):
        card = create_agent_card("kubernetes-support", MINIMAL_CONFIG, BASE_URL)
        assert len(card.skills) == 1
        assert card.skills[0].id == "k8s_troubleshoot"


# ── Security schemes ──────────────────────────────────────────────────────────

class TestAgentCardSecuritySchemes:
    """Verify security_schemes are now populated (Change 2)."""

    def setup_method(self):
        self.card = create_agent_card("kubernetes-support", MINIMAL_CONFIG, BASE_URL)

    def test_security_schemes_not_none(self):
        assert self.card.security_schemes is not None

    def test_oauth2_scheme_present(self):
        assert "partner-oauth2" in self.card.security_schemes

    def test_mtls_scheme_present(self):
        assert "partner-mtls" in self.card.security_schemes

    def test_oauth2_scheme_type(self):
        scheme = self.card.security_schemes["partner-oauth2"]
        assert isinstance(scheme, OAuth2SecurityScheme)

    def test_mtls_scheme_type(self):
        scheme = self.card.security_schemes["partner-mtls"]
        assert isinstance(scheme, MutualTLSSecurityScheme)

    def test_oauth2_has_client_credentials_flow(self):
        # SecurityScheme is a Pydantic union — access via model_dump (camelCase keys)
        d = self.card.security_schemes["partner-oauth2"].model_dump()
        assert d.get("flows") is not None
        assert d["flows"].get("clientCredentials") is not None

    def test_oauth2_token_url_set(self):
        d = self.card.security_schemes["partner-oauth2"].model_dump()
        token_url = d["flows"]["clientCredentials"]["tokenUrl"]
        assert token_url
        assert "openid-connect/token" in token_url

    def test_oauth2_scopes_include_agent_invoke(self):
        d = self.card.security_schemes["partner-oauth2"].model_dump()
        scopes = d["flows"]["clientCredentials"]["scopes"]
        assert "agent:invoke" in scopes

    def test_oauth2_metadata_url_set(self):
        d = self.card.security_schemes["partner-oauth2"].model_dump()
        meta_url = d.get("oauth2MetadataUrl") or d.get("oauth2_metadata_url") or ""
        assert meta_url
        assert "openid-configuration" in meta_url

    def test_oauth2_description_mentions_audience(self):
        d = self.card.security_schemes["partner-oauth2"].model_dump()
        desc = (d.get("description") or "").lower()
        assert "aud" in desc or "audience" in desc

    def test_oauth2_description_mentions_agent_spiffe_id(self):
        d = self.card.security_schemes["partner-oauth2"].model_dump()
        assert "kubernetes-support" in (d.get("description") or "")

    def test_mtls_description_mentions_spiffe_trust_domain(self):
        d = self.card.security_schemes["partner-mtls"].model_dump()
        assert "spiffe://" in (d.get("description") or "")


# ── Security requirements ─────────────────────────────────────────────────────

class TestAgentCardSecurityRequirements:
    """Verify the security AND-requirement list is correctly set."""

    def setup_method(self):
        self.card = create_agent_card("kubernetes-support", MINIMAL_CONFIG, BASE_URL)

    def test_security_list_not_none(self):
        assert self.card.security is not None

    def test_security_list_has_one_requirement(self):
        # One AND-requirement object requiring both OAuth2 and mTLS
        assert len(self.card.security) == 1

    def test_security_requires_oauth2(self):
        req = self.card.security[0]
        assert "partner-oauth2" in req

    def test_security_requires_mtls(self):
        req = self.card.security[0]
        assert "partner-mtls" in req

    def test_oauth2_requires_invoke_scope(self):
        req = self.card.security[0]
        assert "agent:invoke" in req["partner-oauth2"]


# ── Authenticated extended card flag ─────────────────────────────────────────

class TestAuthenticatedExtendedCard:
    def test_supports_authenticated_extended_card_true(self):
        card = create_agent_card("kubernetes-support", MINIMAL_CONFIG, BASE_URL)
        assert card.supports_authenticated_extended_card is True


# ── Environment variable overrides ───────────────────────────────────────────

class TestEnvironmentOverrides:
    def test_custom_token_url_via_env(self, monkeypatch):
        monkeypatch.setenv(
            "KEYCLOAK_TOKEN_URL",
            "https://sso.example.com/realms/prod/protocol/openid-connect/token",
        )
        # Re-import to pick up the monkeypatched env var
        import importlib
        import agent_service.a2a.agent_cards as cards_module
        importlib.reload(cards_module)

        card = cards_module.create_agent_card(
            "kubernetes-support", MINIMAL_CONFIG, BASE_URL
        )
        scheme = card.security_schemes["partner-oauth2"]
        assert "sso.example.com" in scheme.flows.client_credentials.token_url

        # Restore
        importlib.reload(cards_module)

    def test_custom_spiffe_trust_domain_via_env(self, monkeypatch):
        monkeypatch.setenv("SPIFFE_TRUST_DOMAIN", "acme.corp")
        import importlib
        import agent_service.a2a.agent_cards as cards_module
        importlib.reload(cards_module)

        card = cards_module.create_agent_card(
            "kubernetes-support", MINIMAL_CONFIG, BASE_URL
        )
        scheme = card.security_schemes["partner-mtls"]
        assert "acme.corp" in scheme.description

        importlib.reload(cards_module)
