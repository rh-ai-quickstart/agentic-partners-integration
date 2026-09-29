"""Tests for Helm secret key consistency and partner agent card security.

FINDING 9: The _env-helpers.tpl template referenced 'openai-api-key' from
the llm-credentials secret, but secrets.yaml only defined 'ai-openai-api-key'.
The fix added 'openai-api-key' to secrets.yaml so both keys exist. These
tests verify the key is present and that every secretKeyRef in _env-helpers.tpl
has a matching key in secrets.yaml (no dangling references).

FINDING 10: The kubernetes-partner-agent's A2A agent card lacked security
metadata. The fix added OAuth2 + mTLS security schemes and requirements so
external callers know how to authenticate. These tests verify the card
advertises both schemes with the expected properties.
"""

import re
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SECRETS_YAML = PROJECT_ROOT / "helm" / "templates" / "secrets.yaml"
ENV_HELPERS_TPL = PROJECT_ROOT / "helm" / "templates" / "_env-helpers.tpl"

# Allow importing from kubernetes-partner-agent/src
_KUBE_AGENT_SRC = PROJECT_ROOT / "kubernetes-partner-agent" / "src"
sys.path.insert(0, str(_KUBE_AGENT_SRC))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def secrets_text():
    """Read helm/templates/secrets.yaml as raw text."""
    return SECRETS_YAML.read_text()


@pytest.fixture(scope="module")
def env_helpers_text():
    """Read helm/templates/_env-helpers.tpl as raw text."""
    return ENV_HELPERS_TPL.read_text()


@pytest.fixture(scope="module")
def secrets_llm_keys(secrets_text):
    """Extract all data keys defined under the llm-credentials Secret.

    Scans from the 'llm-credentials' header to the next '---' separator and
    collects every YAML key inside the ``data:`` block.
    """
    in_llm_secret = False
    in_data = False
    keys: set[str] = set()
    for line in secrets_text.splitlines():
        if "llm-credentials" in line:
            in_llm_secret = True
            continue
        if in_llm_secret and line.strip().startswith("---"):
            break
        if in_llm_secret and line.strip() == "data:":
            in_data = True
            continue
        if in_llm_secret and in_data:
            m = re.match(r"\s+([\w-]+):", line)
            if m:
                keys.add(m.group(1))
    return keys


@pytest.fixture(scope="module")
def env_helpers_llm_secret_refs(env_helpers_text):
    """Extract every secretKeyRef key that references llm-credentials.

    Returns a set of key names (e.g. 'openai-api-key', 'ai-model').
    """
    keys: set[str] = set()
    lines = env_helpers_text.splitlines()
    for i, line in enumerate(lines):
        if "llm-credentials" in line:
            # Walk forward to find the 'key:' line
            for j in range(i + 1, min(i + 5, len(lines))):
                km = re.match(r"\s+key:\s+([\w-]+)", lines[j])
                if km:
                    keys.add(km.group(1))
                    break
    return keys


@pytest.fixture(scope="module")
def partner_agent_card():
    """Create a partner agent card with a minimal config."""
    from kubernetes_agent.a2a.agent_cards import create_agent_card

    config = {
        "description": "Kubernetes support specialist",
        "a2a": {
            "card_name": "Kubernetes Support Agent",
            "card_description": "Handles Kubernetes troubleshooting",
            "skills": [
                {
                    "id": "k8s-debug",
                    "name": "Kubernetes Debugging",
                    "description": "Debug Kubernetes issues",
                    "tags": ["kubernetes"],
                    "examples": ["Why is my pod crashing?"],
                }
            ],
        },
    }
    return create_agent_card(
        agent_name="kubernetes-support",
        config=config,
        base_url="http://localhost:8080",
    )


# ===================================================================
# FINDING 9: Helm secret key mismatch
# ===================================================================


class TestHelmSecretKeys:
    """Verify secrets.yaml defines all keys that _env-helpers.tpl references."""

    def test_secrets_has_openai_api_key(self, secrets_text):
        """The fix added 'openai-api-key' so OPENAI_API_KEY env var resolves."""
        assert "openai-api-key:" in secrets_text

    def test_secrets_has_ai_openai_api_key(self, secrets_text):
        """Original key 'ai-openai-api-key' must still exist for AI_OPENAI_API_KEY."""
        assert "ai-openai-api-key:" in secrets_text

    def test_env_helpers_openai_key_matches_secret(
        self, secrets_llm_keys, env_helpers_llm_secret_refs
    ):
        """Every llm-credentials secretKeyRef in _env-helpers.tpl must have
        a corresponding key defined in secrets.yaml's llm-credentials data."""
        openai_refs = {
            k for k in env_helpers_llm_secret_refs if "openai" in k.lower()
        }
        assert openai_refs, "Expected at least one openai key reference"
        missing = openai_refs - secrets_llm_keys
        assert not missing, (
            f"Keys referenced in _env-helpers.tpl but missing from "
            f"secrets.yaml llm-credentials: {missing}"
        )

    def test_no_dead_secret_references(
        self, secrets_llm_keys, env_helpers_llm_secret_refs
    ):
        """All secret keys referenced in _env-helpers.tpl must exist in
        secrets.yaml -- no dangling references allowed."""
        missing = env_helpers_llm_secret_refs - secrets_llm_keys
        assert not missing, (
            f"Dead secret references in _env-helpers.tpl "
            f"(keys not in secrets.yaml): {missing}"
        )


# ===================================================================
# FINDING 10: Agent card security for kubernetes-partner-agent
# ===================================================================


try:
    import a2a  # noqa: F401
    _HAS_A2A = True
except ImportError:
    _HAS_A2A = False


@pytest.mark.skipif(not _HAS_A2A, reason="a2a SDK not installed in this venv")
class TestPartnerAgentCardSecurity:
    """Verify the partner agent card advertises OAuth2 + mTLS security."""

    def test_partner_agent_card_has_security_schemes(self, partner_agent_card):
        """Card must advertise 'partner-oauth2' and 'partner-mtls' schemes."""
        schemes = partner_agent_card.security_schemes
        assert schemes is not None, "security_schemes must not be None"
        scheme_names = set(schemes.keys())
        assert "partner-oauth2" in scheme_names, (
            "Missing 'partner-oauth2' security scheme"
        )
        assert "partner-mtls" in scheme_names, (
            "Missing 'partner-mtls' security scheme"
        )

    def test_partner_agent_card_has_security_requirements(
        self, partner_agent_card
    ):
        """Card must have a security list referencing both schemes."""
        security = partner_agent_card.security
        assert security is not None, "security requirements must not be None"
        assert len(security) >= 1, "Expected at least one security requirement"
        first_req = security[0]
        assert "partner-oauth2" in first_req, (
            "Security requirement must reference 'partner-oauth2'"
        )
        assert "partner-mtls" in first_req, (
            "Security requirement must reference 'partner-mtls'"
        )

    def test_partner_agent_card_oauth2_has_token_url(
        self, partner_agent_card
    ):
        """OAuth2 scheme must have a token_url for client_credentials flow."""
        schemes = partner_agent_card.security_schemes
        oauth2_wrapper = schemes["partner-oauth2"]
        # SecurityScheme is a Pydantic RootModel; unwrap to get the actual
        # OAuth2SecurityScheme via .root
        oauth2 = oauth2_wrapper.root
        assert oauth2.flows is not None, "OAuth2 scheme must have flows"
        cc = oauth2.flows.client_credentials
        assert cc is not None, "OAuth2 must have client_credentials flow"
        assert cc.token_url, "client_credentials flow must have a token_url"
        assert "token" in cc.token_url, (
            "token_url should point to a token endpoint"
        )

    def test_partner_agent_card_supports_extended_card(
        self, partner_agent_card
    ):
        """Card must set supports_authenticated_extended_card=True."""
        assert partner_agent_card.supports_authenticated_extended_card is True
