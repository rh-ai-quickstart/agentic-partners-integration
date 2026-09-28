"""Per-hop JWT validation for agent-service (defense-in-depth).

Validates Bearer tokens independently of request-manager so that each
service in the call chain verifies the JWT signature, expiration, and
audience rather than trusting upstream headers alone.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import jwt
from jwt import PyJWKClient

logger = logging.getLogger(__name__)

KEYCLOAK_URL: str = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
KEYCLOAK_REALM: str = os.getenv("KEYCLOAK_REALM", "partner-agent")
JWT_VALIDATION_ENABLED: bool = (
    os.getenv("JWT_VALIDATION_ENABLED", "true").lower() == "true"
)
JWT_EXPECTED_AUDIENCE: str = os.getenv("JWT_EXPECTED_AUDIENCE", "agent-service")

_jwks_client: Optional[PyJWKClient] = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        jwks_url = (
            f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
            "/protocol/openid-connect/certs"
        )
        _jwks_client = PyJWKClient(jwks_url, cache_keys=True, max_cached_keys=16)
    return _jwks_client


@dataclass
class TokenClaims:
    """Validated JWT claims extracted from the Bearer token."""

    subject: str
    issuer: str
    audience: List[str]
    email: Optional[str] = None
    groups: List[str] = field(default_factory=list)
    act: Optional[Dict[str, Any]] = None
    azp: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_delegated(self) -> bool:
        return self.act is not None

    @property
    def actor_subject(self) -> Optional[str]:
        if self.act and isinstance(self.act, dict):
            return self.act.get("sub")
        return self.azp


class JWTAuthError(Exception):
    """Raised when JWT validation fails at the agent-service layer."""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message)


def validate_bearer_token(
    authorization: Optional[str],
    expected_audience: Optional[str] = None,
) -> TokenClaims:
    """Validate a Bearer token and return extracted claims.

    Args:
        authorization: The Authorization header value ("Bearer <token>").
        expected_audience: Override the default expected audience.

    Returns:
        TokenClaims with validated subject, audience, and delegation info.

    Raises:
        JWTAuthError: If validation fails for any reason.
    """
    if not JWT_VALIDATION_ENABLED:
        raise JWTAuthError("JWT validation is disabled")

    if not authorization or not authorization.startswith("Bearer "):
        raise JWTAuthError("Authentication failed")

    token = authorization[7:]
    aud = expected_audience or JWT_EXPECTED_AUDIENCE

    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=aud,
            options={"verify_exp": True, "verify_aud": True},
        )
    except jwt.ExpiredSignatureError:
        raise JWTAuthError("Authentication failed")
    except jwt.InvalidAudienceError:
        raise JWTAuthError("Authentication failed")
    except jwt.PyJWTError:
        raise JWTAuthError("Authentication failed")

    subject = payload.get("sub")
    if not subject:
        raise JWTAuthError("Authentication failed")

    audience_raw = payload.get("aud", [])
    if isinstance(audience_raw, str):
        audience_raw = [audience_raw]

    groups = payload.get("groups", [])
    if isinstance(groups, str):
        groups = [groups]

    return TokenClaims(
        subject=subject,
        issuer=payload.get("iss", ""),
        audience=audience_raw,
        email=payload.get("email") or payload.get("preferred_username"),
        groups=[g.strip("/") for g in groups if g],
        act=payload.get("act"),
        azp=payload.get("azp"),
        raw=payload,
    )
