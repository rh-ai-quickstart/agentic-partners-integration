"""JWT Authentication Middleware for the kubernetes-partner-agent.

Validates Bearer JWT on all endpoints except /health and agent card
discovery paths. Uses Keycloak JWKS for signature verification.
"""

import logging
import os
from typing import Optional

import jwt
from jwt import PyJWKClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

KEYCLOAK_URL: str = os.getenv("KEYCLOAK_URL", "")
KEYCLOAK_REALM: str = os.getenv("KEYCLOAK_REALM", "partner-agent")
JWT_AUTH_ENABLED: bool = os.getenv("JWT_AUTH_ENABLED", "true").lower() == "true"

PUBLIC_PATHS = {
    "/health",
    "/.well-known/agent.json",
    "/.well-known/agent-card.json",
}

_jwks_client: Optional[PyJWKClient] = None


def _get_jwks_client() -> Optional[PyJWKClient]:
    global _jwks_client
    if _jwks_client is None and KEYCLOAK_URL:
        jwks_url = (
            f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
            "/protocol/openid-connect/certs"
        )
        _jwks_client = PyJWKClient(jwks_url, cache_keys=True, max_cached_keys=16)
    return _jwks_client


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """Validates Bearer JWT on non-public endpoints."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/") or "/"

        if path in PUBLIC_PATHS:
            return await call_next(request)

        if not JWT_AUTH_ENABLED:
            return await call_next(request)

        if not KEYCLOAK_URL:
            logger.warning("JWT auth enabled but KEYCLOAK_URL not set — skipping validation")
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"error": "Authentication required"},
            )

        token = auth_header[7:]
        expected_issuer = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"

        try:
            client = _get_jwks_client()
            if client is None:
                return await call_next(request)

            signing_key = client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=expected_issuer,
                options={
                    "verify_exp": True,
                    "verify_iss": True,
                    "verify_aud": False,
                },
            )
            request.state.jwt_claims = payload
        except jwt.PyJWTError:
            return JSONResponse(
                status_code=401,
                content={"error": "Authentication failed"},
            )

        return await call_next(request)
