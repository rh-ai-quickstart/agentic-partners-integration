"""
FastAPI middleware for workload identity extraction.

Identity sources (checked in order):
  1. SPIFFE — mTLS peer cert (production) or X-SPIFFE-ID header (MOCK_SPIFFE)
  2. JWT Bearer token — ``sub`` / ``preferred_username`` mapped to a
     ``spiffe://<trust-domain>/user/<name>`` identity

Deny-by-default: requests to protected paths without a resolvable identity
are rejected with 403 Forbidden.  Health, docs, and auth paths are exempt.
"""

import base64
import json
import logging
import os

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .identity import TRUST_DOMAIN, WorkloadIdentity, extract_identity

logger = logging.getLogger(__name__)

IDENTITY_ENFORCEMENT: bool = (
    os.getenv("IDENTITY_ENFORCEMENT", "true").lower() == "true"
)

SKIP_PATHS = {
    "/health",
    "/health/detailed",
    "/ready",
    "/metrics",
    "/openapi.json",
    "/docs",
    "/redoc",
    "/auth/login",
    "/auth/me",
    "/auth/refresh",
    "/auth/config",
    "/auth/callback",
}

SKIP_PREFIXES = (
    "/auth/",
    "/docs",
    "/redoc",
    "/.well-known/",
)


def _identity_from_bearer(request: Request) -> WorkloadIdentity | None:
    """Derive a user identity from the JWT Bearer token payload.

    Does NOT verify the signature — that is the responsibility of the
    downstream route handler (auth_endpoints / jwt_auth) and the Praxis
    gateway.  This is purely identity extraction so the caller has a
    SPIFFE-shaped identity for policy evaluation.
    """
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        name = (
            payload.get("preferred_username")
            or payload.get("sub")
            or payload.get("azp")
        )
        if not name:
            return None
        return WorkloadIdentity(
            spiffe_id=f"spiffe://{TRUST_DOMAIN}/user/{name}"
        )
    except Exception:
        logger.debug("Failed to extract identity from Bearer token", exc_info=True)
        return None


class IdentityMiddleware(BaseHTTPMiddleware):
    """Middleware that extracts caller identity and attaches it to request.state.

    Deny-by-default: protected paths return 403 when no identity is present.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in SKIP_PATHS or path.startswith(SKIP_PREFIXES):
            request.state.identity = None
            return await call_next(request)

        identity = extract_identity(request) or _identity_from_bearer(request)
        request.state.identity = identity

        if IDENTITY_ENFORCEMENT and identity is None:
            return JSONResponse(
                status_code=403,
                content={"detail": "Caller identity required"},
            )

        return await call_next(request)
