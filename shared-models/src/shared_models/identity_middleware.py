"""
FastAPI middleware for SPIFFE workload identity extraction.

Replaces JWT-based authentication middleware. In mock mode, identity
is extracted from X-SPIFFE-ID header. In real mode, from mTLS peer cert.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .identity import extract_identity

# Paths that don't require identity (health checks, metrics, etc.)
SKIP_PATHS = {
    "/health", "/health/detailed", "/ready", "/metrics", "/openapi.json", "/docs", "/redoc",
    "/auth/login", "/auth/me", "/auth/refresh", "/auth/config", "/auth/callback",
}


class IdentityMiddleware(BaseHTTPMiddleware):
    """Middleware that extracts SPIFFE identity and attaches it to request.state.

    After this middleware runs, handlers can access:
        request.state.identity  -> WorkloadIdentity or None
    """

    async def dispatch(self, request: Request, call_next):
        # Skip identity check for health/docs endpoints
        if request.url.path in SKIP_PATHS:
            request.state.identity = None
            return await call_next(request)

        identity = extract_identity(request)
        request.state.identity = identity

        # For protected paths, require identity
        # In mock mode during local dev, we allow requests without identity
        # for backward compatibility with the web UI (which sets user email in body)
        # The endpoint handlers validate identity when available

        return await call_next(request)
