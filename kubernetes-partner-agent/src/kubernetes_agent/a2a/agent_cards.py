"""A2A Agent Card generation from YAML config.

Security contract: each card advertises machine-readable security
requirements (OAuth 2.0 client_credentials + mTLS) so external callers
know how to authenticate before making an A2A request.
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

_KEYCLOAK_BASE = os.getenv("KEYCLOAK_URL", "http://keycloak:8080").rstrip("/")
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
    """Build OAuth2 + mTLS security schemes for the agent card."""
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
            f"Required JWT audience (aud claim): '{spiffe_agent_id}'."
        ),
    )

    mtls_scheme = MutualTLSSecurityScheme(
        description=(
            f"Mutual TLS with a SPIFFE client certificate. "
            f"The certificate SAN must be a SPIFFE URI under "
            f"'spiffe://{_SPIFFE_TRUST_DOMAIN}'."
        )
    )

    return {
        "partner-oauth2": oauth2_scheme,
        "partner-mtls": mtls_scheme,
    }


def create_agent_card(
    agent_name: str, config: dict[str, Any], base_url: str
) -> AgentCard:
    """Create an A2A AgentCard from the agent's YAML config."""
    a2a_config = config.get("a2a", {})

    card_name = a2a_config.get(
        "card_name",
        agent_name.replace("-", " ").title() + " Agent",
    )
    card_description = a2a_config.get(
        "card_description",
        config.get("description", f"{card_name} specialist agent"),
    )

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
        supports_authenticated_extended_card=True,
    )
