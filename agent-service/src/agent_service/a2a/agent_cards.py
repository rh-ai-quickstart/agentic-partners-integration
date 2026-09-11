"""A2A Agent Card generation from YAML config.

Generates AgentCard instances dynamically from the 'a2a' section
of each agent's YAML config file.  No per-agent functions needed —
adding a new agent YAML with an 'a2a' block is sufficient.

Security contract
-----------------
Each card now advertises machine-readable security requirements so that
external callers (e.g. an Ericsson orchestrator) know exactly how to
authenticate before making an A2A request:

* ``security_schemes`` — declares the OAuth 2.0 client_credentials flow
  (token endpoint, required scopes) and the mutual-TLS requirement with
  the accepted SPIFFE trust domain.
* ``security`` — a single AND-requirement object requiring BOTH schemes.
* ``supports_authenticated_extended_card`` — set to True so authenticated
  callers can fetch a richer card that includes internal scope details.

The token endpoint and SPIFFE trust domain are read from environment
variables so the same code works in all deployment environments:

  KEYCLOAK_TOKEN_URL   (default: http://keycloak:8090/realms/partner-agent/…)
  KEYCLOAK_METADATA_URL (default: derived from KEYCLOAK_TOKEN_URL)
  SPIFFE_TRUST_DOMAIN  (default: partner.example.com)
"""

import os
from typing import Any

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    ClientCredentialsOAuthFlow,
    MutualTLSSecurityScheme,
    OAuth2SecurityScheme,
    OAuthFlows,
)

# ── Environment-driven defaults ───────────────────────────────────────────────
_KEYCLOAK_BASE = os.getenv(
    "KEYCLOAK_URL",
    "http://keycloak:8090",
).rstrip("/")
_KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "partner-agent")
_TOKEN_URL = os.getenv(
    "KEYCLOAK_TOKEN_URL",
    f"{_KEYCLOAK_BASE}/realms/{_KEYCLOAK_REALM}/protocol/openid-connect/token",
)
_METADATA_URL = os.getenv(
    "KEYCLOAK_METADATA_URL",
    f"{_KEYCLOAK_BASE}/realms/{_KEYCLOAK_REALM}/.well-known/openid-configuration",
)
_SPIFFE_TRUST_DOMAIN = os.getenv("SPIFFE_TRUST_DOMAIN", "partner.example.com")


def _build_security_schemes(agent_name: str) -> dict[str, Any]:
    """Build the security_schemes dict for an AgentCard.

    Returns two named schemes:
    - ``partner-oauth2``: OAuth2 client_credentials flow against Keycloak.
      The required ``aud`` claim is the agent's SPIFFE URI; documented in
      the scheme description because the A2A spec has no dedicated aud field.
    - ``partner-mtls``: mutual TLS with SPIFFE client certificate from the
      declared trust domain.
    """
    spiffe_agent_id = f"spiffe://{_SPIFFE_TRUST_DOMAIN}/agent/{agent_name}"

    oauth2_scheme = OAuth2SecurityScheme(
        flows=OAuthFlows(
            client_credentials=ClientCredentialsOAuthFlow(
                token_url=_TOKEN_URL,
                scopes={
                    "agent:invoke": "Send tasks to this agent",
                    "agent:read": "Read task status from this agent",
                },
            )
        ),
        oauth2_metadata_url=_METADATA_URL,
        description=(
            f"OAuth 2.0 client_credentials flow. "
            f"Required JWT audience (aud claim): '{spiffe_agent_id}'. "
            f"Authentication MUST use private_key_jwt (RFC 7521) — "
            f"shared secrets are not accepted. "
            f"Server metadata (token endpoint, supported scopes): {_METADATA_URL}"
        ),
    )

    mtls_scheme = MutualTLSSecurityScheme(
        description=(
            f"Mutual TLS with a SPIFFE client certificate. "
            f"The certificate Subject Alternative Name must be a "
            f"SPIFFE URI under the trust domain "
            f"'spiffe://{_SPIFFE_TRUST_DOMAIN}' or a federated partner "
            f"trust domain pre-configured in SPIRE."
        )
    )

    return {
        "partner-oauth2": oauth2_scheme,
        "partner-mtls": mtls_scheme,
    }


def create_agent_card(
    agent_name: str, config: dict[str, Any], base_url: str
) -> AgentCard:
    """Create a fully-populated A2A AgentCard from an agent's YAML config.

    Reads the 'a2a' section of the config for card_name, card_description,
    and skills.  Falls back to sensible defaults when fields are missing.

    The returned card includes machine-readable security requirements so
    that external callers can construct valid authenticated A2A requests
    without out-of-band documentation.

    Args:
        agent_name: Agent identifier (e.g. "software-support")
        config: The agent's full YAML config dict
        base_url: Base URL where this agent's A2A endpoint is served

    Returns:
        A fully populated AgentCard with security_schemes and security set.
    """
    a2a_config = config.get("a2a", {})

    # Card-level metadata
    card_name = a2a_config.get(
        "card_name",
        agent_name.replace("-", " ").title() + " Agent",
    )
    card_description = a2a_config.get(
        "card_description",
        config.get("description", f"{card_name} specialist agent"),
    )

    # Build skills from config
    skills = []
    for skill_cfg in a2a_config.get("skills", []):
        skills.append(
            AgentSkill(
                id=skill_cfg["id"],
                name=skill_cfg["name"],
                description=skill_cfg.get("description", ""),
                tags=skill_cfg.get("tags", []),
                examples=skill_cfg.get("examples", []),
            )
        )

    # Security schemes — declares OAuth2 + mTLS requirements.
    # The AND-requirement in ``security`` means callers must satisfy BOTH.
    security_schemes = _build_security_schemes(agent_name)
    security_requirements = [
        {
            "partner-oauth2": ["agent:invoke"],
            "partner-mtls": [],
        }
    ]

    return AgentCard(
        name=card_name,
        description=card_description.strip(),
        version="0.1.0",
        url=base_url,
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
        ),
        skills=skills,
        security_schemes=security_schemes,
        security=security_requirements,
        # Authenticated callers get a richer card with internal scope details.
        # The A2A SDK serves this at /agent/authenticatedExtendedCard.
        supports_authenticated_extended_card=True,
    )
