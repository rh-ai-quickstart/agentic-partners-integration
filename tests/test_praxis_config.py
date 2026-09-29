"""Tests for Praxis policy and configuration files.

These tests verify the Praxis 0.7.1 upgrade: gateway-level JWT validation
is enabled via the identity/jwt plugin with Keycloak claim_mapper, replacing
the allow-all policy that was required under Praxis 0.7.0 (which had SSRF
blocks on RFC 1918 JWKS fetches and a kid-matching bug).

Each test reproduces a specific gap that existed before the fix, then
validates the corrected configuration.
"""

from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
POLICY_YAML = PROJECT_ROOT / "praxis" / "policy.yaml"
CONFIG_YAML = PROJECT_ROOT / "praxis" / "config.yaml"
HELM_VALUES = PROJECT_ROOT / "helm" / "values.yaml"
SEED_SERVICES = PROJECT_ROOT / "scripts" / "seed" / "seed-services.sh"
HELM_PRAXIS_DEPLOYMENT = PROJECT_ROOT / "helm" / "templates" / "praxis-deployment.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def policy():
    """Load praxis/policy.yaml."""
    return yaml.safe_load(POLICY_YAML.read_text())


@pytest.fixture(scope="module")
def config():
    """Load praxis/config.yaml."""
    return yaml.safe_load(CONFIG_YAML.read_text())


@pytest.fixture(scope="module")
def helm_values():
    """Load helm/values.yaml."""
    return yaml.safe_load(HELM_VALUES.read_text())


@pytest.fixture(scope="module")
def seed_services_text():
    """Read scripts/seed/seed-services.sh as text."""
    return SEED_SERVICES.read_text()


@pytest.fixture(scope="module")
def helm_praxis_deployment_text():
    """Read helm/templates/praxis-deployment.yaml as text."""
    return HELM_PRAXIS_DEPLOYMENT.read_text()


# ===================================================================
# GAP 1: Praxis 0.7.0 SSRF bug forced allow-all policy
# ===================================================================


class TestPolicyJwtPlugin:
    """Verify the identity/jwt plugin is correctly configured."""

    def test_has_plugins_list(self, policy):
        """GAP: old policy had no plugins section at all."""
        assert "plugins" in policy
        assert isinstance(policy["plugins"], list)
        assert len(policy["plugins"]) >= 1

    def test_plugin_is_identity_jwt(self, policy):
        """GAP: without identity/jwt, no JWT validation occurs at the gateway."""
        jwt_plugins = [
            p for p in policy["plugins"] if p.get("kind") == "identity/jwt"
        ]
        assert len(jwt_plugins) >= 1

    def test_plugin_has_trusted_issuers_with_jwks_url(self, policy):
        """GAP: 0.7.0 SSRF bug blocked JWKS fetches to RFC 1918 addresses."""
        jwt_plugin = next(
            p for p in policy["plugins"] if p.get("kind") == "identity/jwt"
        )
        cfg = jwt_plugin.get("config", {})
        issuers = cfg.get("trusted_issuers", [])
        assert len(issuers) >= 1
        dk = issuers[0].get("decoding_key", {})
        assert dk.get("kind") == "jwks_url"
        assert dk.get("url")
        assert "protocol/openid-connect/certs" in dk["url"]

    def test_plugin_uses_keycloak_claim_mapper(self, policy):
        jwt_plugin = next(
            p for p in policy["plugins"] if p.get("kind") == "identity/jwt"
        )
        assert jwt_plugin["config"].get("claim_mapper") == "keycloak"

    def test_plugin_has_skip_audience_validation(self, policy):
        jwt_plugin = next(
            p for p in policy["plugins"] if p.get("kind") == "identity/jwt"
        )
        issuer = jwt_plugin["config"]["trusted_issuers"][0]
        assert issuer.get("skip_audience_validation") is True

    def test_plugin_has_insecure_http_for_docker_network(self, policy):
        jwt_plugin = next(
            p for p in policy["plugins"] if p.get("kind") == "identity/jwt"
        )
        dk = jwt_plugin["config"]["trusted_issuers"][0]["decoding_key"]
        assert dk.get("insecure_http") is True

    def test_plugin_hooks_identity_resolve(self, policy):
        jwt_plugin = next(
            p for p in policy["plugins"] if p.get("kind") == "identity/jwt"
        )
        assert "identity.resolve" in jwt_plugin.get("hooks", [])

    def test_plugin_on_error_fail(self, policy):
        jwt_plugin = next(
            p for p in policy["plugins"] if p.get("kind") == "identity/jwt"
        )
        assert jwt_plugin.get("on_error") == "fail"

    def test_plugin_has_perform_http_capability(self, policy):
        jwt_plugin = next(
            p for p in policy["plugins"] if p.get("kind") == "identity/jwt"
        )
        caps = jwt_plugin.get("capabilities", [])
        assert "perform_http" in caps


class TestPolicyAuthentication:
    """Verify the global authentication references the JWT plugin."""

    def test_global_has_authentication(self, policy):
        """GAP: old policy had no authentication section."""
        g = policy.get("global", {})
        assert "authentication" in g

    def test_authentication_references_plugin_name(self, policy):
        g = policy["global"]
        auth_list = g["authentication"]
        assert isinstance(auth_list, list)
        plugin_names = [p["name"] for p in policy["plugins"]]
        for ref in auth_list:
            assert ref in plugin_names


class TestPolicyAuthorization:
    """Verify the pre_invocation authorization rules enforce authentication."""

    def test_has_authorization_rules(self, policy):
        """GAP: old policy had no authorization or used allow-all."""
        pre = policy["global"]["authorization"]["pre_invocation"]
        assert len(pre) >= 1

    def test_requires_authenticated(self, policy):
        """Authorization must require authentication via APL require()."""
        pre = policy["global"]["authorization"]["pre_invocation"]
        assert "require(authenticated)" in pre

    def test_no_allow_rule(self, policy):
        """GAP: the 0.7.0 policy had 'allow' which permits unauthenticated access."""
        pre = policy["global"]["authorization"]["pre_invocation"]
        assert "allow" not in pre


class TestPolicyAttributeFiles:
    def test_has_attribute_files(self, policy):
        g = policy.get("global", {})
        attr = g.get("attribute_files", [])
        assert "/etc/praxis/agent_capabilities.yaml" in attr


# ===================================================================
# GAP 2: Version pinned to 0.7.0 across config, Helm, and seed
# ===================================================================


class TestConfigYaml:
    """Verify praxis/config.yaml has the policy filter and correct settings."""

    def test_security_chain_has_policy_filter(self, config):
        chains = config.get("filter_chains", [])
        security_chain = next(
            (c for c in chains if c["name"] == "security"), None
        )
        assert security_chain is not None
        filter_types = [f.get("filter") for f in security_chain.get("filters", [])]
        assert "policy" in filter_types

    def test_policy_filter_has_allow_private_idp(self, config):
        """Policy filter must allow JWKS fetches to RFC 1918 IdP addresses."""
        chains = config.get("filter_chains", [])
        security_chain = next(c for c in chains if c["name"] == "security")
        policy_filter = next(
            f for f in security_chain["filters"] if f.get("filter") == "policy"
        )
        assert policy_filter.get("allow_private_idp") is True

    def test_allow_private_endpoints(self, config):
        insecure = config.get("insecure_options", {})
        assert insecure.get("allow_private_endpoints") is True

    def test_no_070_limitation_comments(self, config):
        raw = CONFIG_YAML.read_text()
        assert "cannot do JWT validation" not in raw
        assert "Auth is enforced at the application layer" not in raw

    def test_mentions_071(self):
        raw = CONFIG_YAML.read_text()
        assert "0.7.1" in raw


class TestHelmValuesVersion:
    def test_praxis_tag_is_071(self, helm_values):
        tag = helm_values.get("praxis", {}).get("tag", "")
        assert tag == "0.7.1"

    def test_praxis_tag_not_070(self, helm_values):
        tag = helm_values.get("praxis", {}).get("tag", "")
        assert tag != "0.7.0"


class TestSeedServicesVersion:
    def test_references_071(self, seed_services_text):
        assert "praxis:0.7.1" in seed_services_text

    def test_no_070_reference(self, seed_services_text):
        assert "praxis:0.7.0" not in seed_services_text


class TestHelmTemplatePolicy:
    """Verify the inline ConfigMap in the Helm template matches the policy."""

    def test_helm_policy_has_identity_jwt(self, helm_praxis_deployment_text):
        assert "identity/jwt" in helm_praxis_deployment_text

    def test_helm_policy_has_keycloak_claim_mapper(self, helm_praxis_deployment_text):
        assert "claim_mapper: keycloak" in helm_praxis_deployment_text

    def test_helm_policy_requires_authenticated(self, helm_praxis_deployment_text):
        assert "require(authenticated)" in helm_praxis_deployment_text

    def test_helm_policy_no_allow_all(self, helm_praxis_deployment_text):
        lines = helm_praxis_deployment_text.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped == "- allow":
                pytest.fail(
                    "Helm praxis-policy ConfigMap must not have '- allow' rule"
                )

    def test_helm_policy_has_perform_http_capability(self, helm_praxis_deployment_text):
        assert "perform_http" in helm_praxis_deployment_text

    def test_helm_policy_has_skip_audience_validation(self, helm_praxis_deployment_text):
        assert "skip_audience_validation: true" in helm_praxis_deployment_text

    def test_helm_policy_uses_port_8080_for_keycloak(self, helm_praxis_deployment_text):
        assert "keycloak:8080" in helm_praxis_deployment_text

    def test_helm_config_has_allow_private_idp(self, helm_praxis_deployment_text):
        assert "allow_private_idp: true" in helm_praxis_deployment_text

    def test_helm_probes_use_admin_port(self, helm_praxis_deployment_text):
        """K8s probes must use admin /ready endpoint, not the proxy port."""
        assert "9901/ready" in helm_praxis_deployment_text
