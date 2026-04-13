"""
Authentication endpoints via Keycloak OIDC.

All user authentication goes through Keycloak using the Resource Owner
Password Grant. Only users configured in the Keycloak realm can log in.
JWTs are validated via Keycloak's JWKS endpoint (RS256).
"""

import os
from typing import Optional

import jwt
import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from shared_models.database import get_db
from shared_models.aaa_service import AAAService

logger = structlog.get_logger()

# ── Configuration ────────────────────────────────────────────────────────────

KEYCLOAK_URL: str = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
KEYCLOAK_REALM: str = os.getenv("KEYCLOAK_REALM", "partner-agent")
KEYCLOAK_CLIENT_ID: str = os.getenv("KEYCLOAK_CLIENT_ID", "partner-agent-ui")

# Cached JWKS client (created lazily)
_jwks_client: Optional[jwt.PyJWKClient] = None


def _get_jwks_client() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        jwks_url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"
        _jwks_client = jwt.PyJWKClient(jwks_url)
    return _jwks_client


# ── JWT Helpers ──────────────────────────────────────────────────────────────

def _decode_keycloak_jwt(token: str) -> dict:
    """Decode a Keycloak-issued JWT using JWKS. Raises on failure."""
    client = _get_jwks_client()
    signing_key = client.get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=KEYCLOAK_CLIENT_ID,
        options={"verify_aud": True},
    )


def decode_token(authorization: str) -> dict:
    """Decode a JWT from an Authorization header value.

    Returns the decoded payload dict. Raises HTTPException 401 on failure.
    Used by adk_endpoints to extract user identity.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization[7:]
    try:
        return _decode_keycloak_jwt(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


def _extract_departments(payload: dict) -> list[str]:
    """Extract departments from Keycloak token claims.

    Keycloak stores realm roles in realm_access.roles.
    We filter to only our department roles (engineering, software, network, admin).
    """
    known_departments = {"engineering", "software", "network", "admin"}
    realm_access = payload.get("realm_access", {})
    roles = realm_access.get("roles", [])
    return [r for r in roles if r in known_departments]


# ── Request/Response Models ──────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str


class LoginUser(BaseModel):
    email: str
    role: str
    departments: list[str]


class LoginResponse(BaseModel):
    token: str
    user: LoginUser


class MeResponse(BaseModel):
    email: str
    role: str
    departments: list[str]


class RefreshResponse(BaseModel):
    token: str


class ConfigResponse(BaseModel):
    keycloak_url: str
    keycloak_realm: str
    client_id: str


# ── Router ───────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate user via Keycloak Resource Owner Password Grant."""
    token_url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            token_url,
            data={
                "grant_type": "password",
                "client_id": KEYCLOAK_CLIENT_ID,
                "username": request.email,
                "password": request.password,
            },
        )

    if resp.status_code != 200:
        detail = "Invalid credentials"
        try:
            err = resp.json()
            detail = err.get("error_description", detail)
        except Exception:
            pass
        raise HTTPException(status_code=401, detail=detail)

    token_data = resp.json()
    access_token = token_data["access_token"]

    # Decode the Keycloak token to extract claims
    try:
        payload = _decode_keycloak_jwt(access_token)
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"Failed to validate Keycloak token: {e}")

    email = payload.get("email", request.email)
    departments = _extract_departments(payload)

    # Ensure user exists in DB (auto-create if needed)
    user = await AAAService.get_or_create_user(
        db, email=email, departments=departments,
    )
    role = user.role.value if user.role else "user"

    logger.info("Login successful", user=email, departments=departments)

    return LoginResponse(
        token=access_token,
        user=LoginUser(email=email, role=role, departments=departments),
    )


@router.get("/me", response_model=MeResponse)
async def me(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Return current user info from Keycloak JWT claims."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header required")

    token = authorization[7:]

    try:
        payload = _decode_keycloak_jwt(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

    email = payload.get("email", payload.get("preferred_username", ""))
    departments = _extract_departments(payload)

    # Get role from DB
    user = await AAAService.get_user_by_email(db, email)
    role = user.role.value if user and user.role else "user"

    return MeResponse(email=email, role=role, departments=departments)


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    authorization: Optional[str] = Header(None),
):
    """Validate an existing JWT. If still valid, return it.

    Full refresh_token flow (using Keycloak's refresh_token grant) can be
    added when needed. For now, the UI refreshes by re-validating the
    current access token.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header required")

    token = authorization[7:]

    try:
        _decode_keycloak_jwt(token)
        return RefreshResponse(token=token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Token expired. Please login again.",
        )
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


@router.get("/config", response_model=ConfigResponse)
async def auth_config():
    """Return Keycloak configuration for the UI."""
    return ConfigResponse(
        keycloak_url=KEYCLOAK_URL,
        keycloak_realm=KEYCLOAK_REALM,
        client_id=KEYCLOAK_CLIENT_ID,
    )
