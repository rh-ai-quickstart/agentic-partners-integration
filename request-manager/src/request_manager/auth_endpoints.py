"""
Authentication endpoints via Keycloak OIDC.

All user authentication goes through Keycloak using the Resource Owner
Password Grant. Only users configured in the Keycloak realm can log in.
JWTs are validated via Keycloak's JWKS endpoint (RS256).
"""

import os
from typing import Optional

import httpx
import jwt
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from shared_models.aaa_service import AAAService
from shared_models.audit import AuditService
from shared_models.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession

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
        jwks_url = (
            f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"
        )
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
        raise HTTPException(
            status_code=401, detail="Missing or invalid Authorization header"
        )

    token = authorization[7:]
    try:
        return _decode_keycloak_jwt(token)
    except jwt.ExpiredSignatureError:
        # Fire-and-forget: audit runs in its own session, won't block if DB is slow.
        import asyncio

        asyncio.ensure_future(
            AuditService.emit(
                event_type="auth.token.expired",
                actor="unknown",
                action="validate_token",
                resource="/adk/*",
                outcome="failure",
                reason="Token expired",
                service="request-manager",
            )
        )
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError as e:
        import asyncio

        asyncio.ensure_future(
            AuditService.emit(
                event_type="auth.token.invalid",
                actor="unknown",
                action="validate_token",
                resource="/adk/*",
                outcome="failure",
                reason=str(e),
                service="request-manager",
            )
        )
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
    refresh_token: Optional[str] = None
    user: LoginUser


class MeResponse(BaseModel):
    email: str
    role: str
    departments: list[str]


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    token: str
    refresh_token: Optional[str] = None


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
        await AuditService.emit(
            event_type="auth.login.failure",
            actor=request.email,
            action="login",
            resource="/auth/login",
            outcome="failure",
            reason=detail,
            service="request-manager",
        )
        raise HTTPException(status_code=401, detail=detail)

    token_data = resp.json()
    access_token = token_data["access_token"]

    # Decode the Keycloak token to extract claims
    try:
        payload = _decode_keycloak_jwt(access_token)
    except jwt.PyJWTError as e:
        raise HTTPException(
            status_code=401, detail=f"Failed to validate Keycloak token: {e}"
        )

    email = payload.get("email", request.email)
    departments = _extract_departments(payload)

    # Ensure user exists in DB (auto-create if needed)
    user = await AAAService.get_or_create_user(
        db,
        email=email,
        departments=departments,
    )
    role = user.role.value if user.role else "user"

    logger.info("Login successful", user=email, departments=departments)

    await AuditService.emit(
        event_type="auth.login.success",
        actor=email,
        action="login",
        resource="/auth/login",
        outcome="success",
        metadata={"departments": departments, "role": role},
        service="request-manager",
    )

    return LoginResponse(
        token=access_token,
        refresh_token=token_data.get("refresh_token"),
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
    request: RefreshRequest,
):
    """Exchange a Keycloak refresh token for a new access token."""
    token_url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": KEYCLOAK_CLIENT_ID,
                "refresh_token": request.refresh_token,
            },
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=401, detail="Refresh token expired. Please login again."
        )

    token_data = resp.json()
    return RefreshResponse(
        token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token"),
    )


@router.get("/config", response_model=ConfigResponse)
async def auth_config():
    """Return Keycloak configuration for the UI."""
    return ConfigResponse(
        keycloak_url=KEYCLOAK_URL,
        keycloak_realm=KEYCLOAK_REALM,
        client_id=KEYCLOAK_CLIENT_ID,
    )
