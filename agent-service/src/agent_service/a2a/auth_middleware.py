"""A2A Authentication Middleware.

Enforces JWT validation on all A2A sub-app paths except agent card
discovery endpoints. This middleware runs directly on the Starlette
sub-app (not the FastAPI host), ensuring A2A requests cannot bypass
authentication by hitting the mounted sub-app directly.
"""

import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# Paths that are allowed without authentication (agent card discovery)
A2A_PUBLIC_PATHS = {
    "/.well-known/agent.json",
    "/.well-known/agent-card.json",
}

# Whether A2A authentication enforcement is enabled
A2A_AUTH_ENABLED = os.getenv("A2A_AUTH_ENABLED", "true").lower() == "true"


class A2AAuthMiddleware(BaseHTTPMiddleware):
    """Middleware enforcing JWT authentication on A2A endpoints.

    Validates Bearer tokens using the same Keycloak JWKS validation
    as the main FastAPI app (jwt_auth.validate_bearer_token). Returns
    generic 401 on any failure to avoid leaking validation details.
    """

    async def dispatch(self, request: Request, call_next):
        # Allow agent card discovery without authentication
        if request.url.path in A2A_PUBLIC_PATHS:
            return await call_next(request)

        if not A2A_AUTH_ENABLED:
            return await call_next(request)

        # Import here to avoid circular imports at module load time
        from agent_service.jwt_auth import (
            JWT_VALIDATION_ENABLED,
            JWTAuthError,
            validate_bearer_token,
        )

        if not JWT_VALIDATION_ENABLED:
            logger.warning("A2A auth middleware active but JWT validation is disabled globally")
            return await call_next(request)

        auth_header = request.headers.get("Authorization")

        if not auth_header:
            logger.warning(
                "A2A request rejected: no Authorization header",
                extra={"path": request.url.path, "method": request.method},
            )
            return JSONResponse(
                status_code=401,
                content={"error": "Authentication required"},
            )

        try:
            claims = validate_bearer_token(auth_header)
            # Attach validated claims to request state for downstream use
            request.state.jwt_claims = claims
            logger.debug(
                "A2A request authenticated",
                extra={
                    "path": request.url.path,
                    "subject": claims.subject,
                    "azp": claims.azp,
                },
            )
        except JWTAuthError:
            logger.warning(
                "A2A request rejected: JWT validation failed",
                extra={"path": request.url.path, "method": request.method},
            )
            return JSONResponse(
                status_code=401,
                content={"error": "Authentication failed"},
            )

        return await call_next(request)
