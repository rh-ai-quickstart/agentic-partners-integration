"""Tests for request_manager.auth_endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from request_manager.auth_endpoints import (
    _extract_departments,
    decode_token,
)

# ---------------------------------------------------------------------------
# _extract_departments
# ---------------------------------------------------------------------------


class TestExtractDepartments:
    """Tests for the _extract_departments helper."""

    def test_filters_known_departments(self):
        """All non-system roles should be returned as departments."""
        payload = {
            "realm_access": {
                "roles": ["engineering", "software", "network", "admin", "kubernetes", "azure"]
            }
        }
        result = _extract_departments(payload)
        assert sorted(result) == ["admin", "azure", "engineering", "kubernetes", "network", "software"]

    def test_empty_roles(self):
        """An empty roles list should produce an empty result."""
        payload = {"realm_access": {"roles": []}}
        assert _extract_departments(payload) == []

    def test_missing_realm_access(self):
        """Missing realm_access key should return empty list."""
        assert _extract_departments({}) == []

    def test_missing_roles_key(self):
        """Missing roles key under realm_access should return empty list."""
        payload = {"realm_access": {}}
        assert _extract_departments(payload) == []

    def test_no_known_departments(self):
        """Non-system roles are returned as departments."""
        payload = {"realm_access": {"roles": ["viewer", "editor"]}}
        assert sorted(_extract_departments(payload)) == ["editor", "viewer"]

    def test_excludes_keycloak_system_roles(self):
        """Keycloak system roles should be filtered out."""
        payload = {
            "realm_access": {
                "roles": ["software", "default-roles-partner-agent", "offline_access", "uma_authorization"]
            }
        }
        assert _extract_departments(payload) == ["software"]

    def test_prefers_groups_claim(self):
        """When groups claim is present, use it instead of realm_access."""
        payload = {
            "groups": ["software", "kubernetes", "azure"],
            "realm_access": {"roles": ["software"]},
        }
        assert sorted(_extract_departments(payload)) == ["azure", "kubernetes", "software"]


# ---------------------------------------------------------------------------
# decode_token
# ---------------------------------------------------------------------------


class TestDecodeToken:
    """Tests for the decode_token function."""

    @patch("request_manager.auth_endpoints._decode_keycloak_jwt")
    def test_valid_token(self, mock_decode):
        """A valid Bearer token should return the decoded payload."""
        mock_decode.return_value = {"email": "user@example.com", "sub": "uid-1"}

        result = decode_token("Bearer valid-jwt")

        assert result["email"] == "user@example.com"
        mock_decode.assert_called_once_with("valid-jwt")

    def test_missing_authorization_header(self):
        """Empty authorization should raise 401."""
        with pytest.raises(HTTPException) as exc_info:
            decode_token("")
        assert exc_info.value.status_code == 401

    def test_no_bearer_prefix(self):
        """Authorization without 'Bearer ' prefix should raise 401."""
        with pytest.raises(HTTPException) as exc_info:
            decode_token("Basic abc123")
        assert exc_info.value.status_code == 401

    @patch("request_manager.auth_endpoints._decode_keycloak_jwt")
    def test_expired_token(self, mock_decode):
        """An expired token should raise 401 with 'Token expired'."""
        import jwt as pyjwt

        mock_decode.side_effect = pyjwt.ExpiredSignatureError()

        with pytest.raises(HTTPException) as exc_info:
            decode_token("Bearer expired-jwt")
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()

    @patch("request_manager.auth_endpoints._decode_keycloak_jwt")
    def test_invalid_jwt(self, mock_decode):
        """A malformed JWT should raise 401."""
        import jwt as pyjwt

        mock_decode.side_effect = pyjwt.PyJWTError("bad sig")

        with pytest.raises(HTTPException) as exc_info:
            decode_token("Bearer bad-jwt")
        assert exc_info.value.status_code == 401

    @patch("request_manager.auth_endpoints._decode_keycloak_jwt")
    def test_strips_bearer_prefix(self, mock_decode):
        """decode_token strips 'Bearer ' before passing to _decode_keycloak_jwt."""
        mock_decode.return_value = {"email": "a@b.com"}

        decode_token("Bearer xyz123")

        mock_decode.assert_called_once_with("xyz123")


# ---------------------------------------------------------------------------
# /auth/login endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestLoginEndpoint:
    """Tests for the /auth/login endpoint handler."""

    @patch("request_manager.auth_endpoints.AAAService")
    @patch("request_manager.auth_endpoints._decode_keycloak_jwt")
    @patch("request_manager.auth_endpoints.httpx.AsyncClient")
    async def test_login_success(self, mock_http_cls, mock_decode_jwt, mock_aaa):
        """Successful login returns token and user info."""
        from request_manager.auth_endpoints import LoginRequest, login

        # Mock HTTP response from Keycloak
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "jwt-access-token",
            "token_type": "Bearer",
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_http_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_http_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Mock JWT decode
        mock_decode_jwt.return_value = {
            "email": "user@example.com",
            "realm_access": {"roles": ["engineering"]},
        }

        # Mock AAAService
        mock_user = MagicMock()
        mock_user.role = MagicMock()
        mock_user.role.value = "engineer"
        mock_user.departments = ["engineering"]
        mock_aaa.get_or_create_user = AsyncMock(return_value=mock_user)
        mock_aaa.update_user_permissions = AsyncMock()

        db = AsyncMock()
        req = LoginRequest(email="user@example.com", password="secret")
        resp = await login(req, db)

        assert resp.token == "jwt-access-token"
        assert resp.user.email == "user@example.com"

    @patch("request_manager.auth_endpoints.httpx.AsyncClient")
    async def test_login_invalid_credentials(self, mock_http_cls):
        """Invalid credentials should raise 401."""
        from request_manager.auth_endpoints import LoginRequest, login

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {
            "error_description": "Invalid user credentials"
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_http_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_http_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        db = AsyncMock()
        req = LoginRequest(email="bad@example.com", password="wrong")

        with pytest.raises(HTTPException) as exc_info:
            await login(req, db)
        assert exc_info.value.status_code == 401

    @patch("request_manager.auth_endpoints.httpx.AsyncClient")
    async def test_login_keycloak_error_no_json(self, mock_http_cls):
        """When Keycloak returns error without JSON body, use default detail."""
        from request_manager.auth_endpoints import LoginRequest, login

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.side_effect = Exception("not JSON")

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_http_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_http_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        db = AsyncMock()
        req = LoginRequest(email="x@y.com", password="p")

        with pytest.raises(HTTPException) as exc_info:
            await login(req, db)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid credentials"


# ---------------------------------------------------------------------------
# /auth/me endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMeEndpoint:
    """Tests for the /auth/me endpoint handler."""

    @patch("request_manager.auth_endpoints.AAAService")
    @patch("request_manager.auth_endpoints._decode_keycloak_jwt")
    async def test_me_returns_user_info(self, mock_decode, mock_aaa):
        """Valid token returns user info."""
        from request_manager.auth_endpoints import me

        mock_decode.return_value = {
            "email": "alice@example.com",
            "realm_access": {"roles": ["engineering", "software"]},
        }

        mock_user = MagicMock()
        mock_user.role = MagicMock()
        mock_user.role.value = "admin"
        mock_aaa.get_user_by_email = AsyncMock(return_value=mock_user)

        db = AsyncMock()
        resp = await me(authorization="Bearer valid", db=db)

        assert resp.email == "alice@example.com"
        assert resp.role == "admin"
        assert "engineering" in resp.departments

    async def test_me_returns_401_without_auth(self):
        """Missing auth header raises 401."""
        from request_manager.auth_endpoints import me

        db = AsyncMock()
        with pytest.raises(HTTPException) as exc_info:
            await me(authorization=None, db=db)
        assert exc_info.value.status_code == 401

    async def test_me_returns_401_non_bearer(self):
        """Non-Bearer auth header raises 401."""
        from request_manager.auth_endpoints import me

        db = AsyncMock()
        with pytest.raises(HTTPException) as exc_info:
            await me(authorization="Basic abc", db=db)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# /auth/refresh endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRefreshEndpoint:
    """Tests for the /auth/refresh endpoint handler."""

    @patch("request_manager.auth_endpoints.httpx.AsyncClient")
    async def test_refresh_valid_token(self, mock_client_cls):
        """Valid refresh token returns new access token."""
        from request_manager.auth_endpoints import RefreshRequest, refresh

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
        }
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client_cls.return_value = mock_client

        resp = await refresh(request=RefreshRequest(refresh_token="old-refresh"))

        assert resp.token == "new-access-token"
        assert resp.refresh_token == "new-refresh-token"

    @patch("request_manager.auth_endpoints.httpx.AsyncClient")
    async def test_refresh_expired_token(self, mock_client_cls):
        """Expired refresh token raises 401."""
        from request_manager.auth_endpoints import RefreshRequest, refresh

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client_cls.return_value = mock_client

        with pytest.raises(HTTPException) as exc_info:
            await refresh(request=RefreshRequest(refresh_token="expired-refresh"))
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# /auth/config endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAuthConfigEndpoint:
    """Tests for the /auth/config endpoint."""

    async def test_returns_keycloak_config(self):
        """Should return Keycloak config values."""
        from request_manager.auth_endpoints import auth_config

        resp = await auth_config()

        # Values come from module-level defaults or env vars
        assert resp.keycloak_url is not None
        assert resp.keycloak_realm is not None
        assert resp.client_id is not None


# ---------------------------------------------------------------------------
# _get_jwks_client - caching behavior
# ---------------------------------------------------------------------------


class TestGetJwksClient:
    """Tests for _get_jwks_client caching behavior (lines 36-39)."""

    def test_creates_client_on_first_call(self):
        """First call creates a new PyJWKClient."""
        import request_manager.auth_endpoints as auth_mod

        # Reset the cached client
        original = auth_mod._jwks_client
        auth_mod._jwks_client = None

        try:
            with patch("jwt.PyJWKClient") as mock_pyjwk:
                mock_instance = MagicMock()
                mock_pyjwk.return_value = mock_instance

                result = auth_mod._get_jwks_client()

                mock_pyjwk.assert_called_once()
                assert result is mock_instance
        finally:
            auth_mod._jwks_client = original

    def test_returns_cached_client_on_subsequent_calls(self):
        """Subsequent calls return the same cached client (lines 36-39)."""
        import request_manager.auth_endpoints as auth_mod

        original = auth_mod._jwks_client
        mock_client = MagicMock()
        auth_mod._jwks_client = mock_client

        try:
            with patch("jwt.PyJWKClient") as mock_pyjwk:
                result = auth_mod._get_jwks_client()

                # Should NOT create a new client
                mock_pyjwk.assert_not_called()
                assert result is mock_client
        finally:
            auth_mod._jwks_client = original

    def test_jwks_url_construction(self):
        """JWKS URL is constructed from KEYCLOAK_URL and KEYCLOAK_REALM (lines 37-38)."""
        import request_manager.auth_endpoints as auth_mod

        original = auth_mod._jwks_client
        auth_mod._jwks_client = None

        try:
            with patch("jwt.PyJWKClient") as mock_pyjwk:
                mock_pyjwk.return_value = MagicMock()

                auth_mod._get_jwks_client()

                call_args = mock_pyjwk.call_args
                jwks_url = call_args[0][0]
                assert "/realms/" in jwks_url
                assert "/protocol/openid-connect/certs" in jwks_url
        finally:
            auth_mod._jwks_client = original


# ---------------------------------------------------------------------------
# _decode_keycloak_jwt - with mock JWKS client
# ---------------------------------------------------------------------------


class TestDecodeKeycloakJwt:
    """Tests for _decode_keycloak_jwt (lines 46-48)."""

    @patch("request_manager.auth_endpoints._get_jwks_client")
    def test_decodes_valid_token(self, mock_get_client):
        """Decodes a valid JWT using JWKS signing key."""
        from request_manager.auth_endpoints import _decode_keycloak_jwt

        mock_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_signing_key.key = "test-key"
        mock_client.get_signing_key_from_jwt.return_value = mock_signing_key
        mock_get_client.return_value = mock_client

        with patch(
            "jwt.decode", return_value={"sub": "user1", "email": "user@example.com"}
        ) as mock_decode:
            result = _decode_keycloak_jwt("test-token")

        assert result["email"] == "user@example.com"
        mock_client.get_signing_key_from_jwt.assert_called_once_with("test-token")
        mock_decode.assert_called_once()

    @patch("request_manager.auth_endpoints._get_jwks_client")
    def test_raises_on_invalid_token(self, mock_get_client):
        """Raises PyJWTError for invalid tokens."""
        import jwt as pyjwt
        from request_manager.auth_endpoints import _decode_keycloak_jwt

        mock_client = MagicMock()
        mock_client.get_signing_key_from_jwt.side_effect = pyjwt.PyJWTError("invalid")
        mock_get_client.return_value = mock_client

        with pytest.raises(pyjwt.PyJWTError):
            _decode_keycloak_jwt("bad-token")


# ---------------------------------------------------------------------------
# /auth/login - additional coverage (lines 160-161, 193-196)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestLoginEndpointExtended:
    """Extended tests for login endpoint to cover missing lines."""

    @patch("request_manager.auth_endpoints.AAAService")
    @patch("request_manager.auth_endpoints._decode_keycloak_jwt")
    @patch("request_manager.auth_endpoints.httpx.AsyncClient")
    async def test_login_jwt_validation_failure(
        self, mock_http_cls, mock_decode_jwt, mock_aaa
    ):
        """When Keycloak token validation fails, raise 401 (lines 160-161)."""
        import jwt as pyjwt
        from request_manager.auth_endpoints import LoginRequest, login

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "bad-jwt-from-keycloak",
            "token_type": "Bearer",
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_http_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_http_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # JWT decode fails
        mock_decode_jwt.side_effect = pyjwt.PyJWTError("signature invalid")

        db = AsyncMock()
        req = LoginRequest(email="user@example.com", password="pass")

        with pytest.raises(HTTPException) as exc_info:
            await login(req, db)
        assert exc_info.value.status_code == 401
        assert "Failed to validate" in exc_info.value.detail

    @patch("request_manager.auth_endpoints.AAAService")
    @patch("request_manager.auth_endpoints._decode_keycloak_jwt")
    @patch("request_manager.auth_endpoints.httpx.AsyncClient")
    async def test_login_user_without_role(
        self, mock_http_cls, mock_decode_jwt, mock_aaa
    ):
        """When user has no role, defaults to 'user'."""
        from request_manager.auth_endpoints import LoginRequest, login

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "jwt-tok"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_http_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_http_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_decode_jwt.return_value = {
            "email": "user@example.com",
            "realm_access": {"roles": []},
        }

        mock_user = MagicMock()
        mock_user.role = None  # No role
        mock_user.departments = []
        mock_aaa.get_or_create_user = AsyncMock(return_value=mock_user)
        mock_aaa.update_user_permissions = AsyncMock()

        db = AsyncMock()
        req = LoginRequest(email="user@example.com", password="pass")
        resp = await login(req, db)

        assert resp.user.role == "user"


# ---------------------------------------------------------------------------
# /auth/me - extended coverage (lines 193-196)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMeEndpointExtended:
    """Extended tests for /auth/me endpoint."""

    @patch("request_manager.auth_endpoints.AAAService")
    @patch("request_manager.auth_endpoints._decode_keycloak_jwt")
    async def test_me_expired_token(self, mock_decode, mock_aaa):
        """Expired token raises 401 (lines 193-194)."""
        import jwt as pyjwt
        from request_manager.auth_endpoints import me

        mock_decode.side_effect = pyjwt.ExpiredSignatureError()

        db = AsyncMock()
        with pytest.raises(HTTPException) as exc_info:
            await me(authorization="Bearer expired-token", db=db)
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()

    @patch("request_manager.auth_endpoints.AAAService")
    @patch("request_manager.auth_endpoints._decode_keycloak_jwt")
    async def test_me_invalid_token(self, mock_decode, mock_aaa):
        """Invalid JWT raises 401 (lines 195-196)."""
        import jwt as pyjwt
        from request_manager.auth_endpoints import me

        mock_decode.side_effect = pyjwt.PyJWTError("bad signature")

        db = AsyncMock()
        with pytest.raises(HTTPException) as exc_info:
            await me(authorization="Bearer bad-jwt", db=db)
        assert exc_info.value.status_code == 401

    @patch("request_manager.auth_endpoints.AAAService")
    @patch("request_manager.auth_endpoints._decode_keycloak_jwt")
    async def test_me_user_not_in_db(self, mock_decode, mock_aaa):
        """When user not found in DB, role defaults to 'user'."""
        from request_manager.auth_endpoints import me

        mock_decode.return_value = {
            "email": "unknown@example.com",
            "preferred_username": "unknown",
            "realm_access": {"roles": ["admin"]},
        }

        mock_aaa.get_user_by_email = AsyncMock(return_value=None)

        db = AsyncMock()
        resp = await me(authorization="Bearer valid", db=db)

        assert resp.email == "unknown@example.com"
        assert resp.role == "user"

    @patch("request_manager.auth_endpoints.AAAService")
    @patch("request_manager.auth_endpoints._decode_keycloak_jwt")
    async def test_me_falls_back_to_preferred_username(self, mock_decode, mock_aaa):
        """When email is missing from payload, falls back to preferred_username."""
        from request_manager.auth_endpoints import me

        mock_decode.return_value = {
            "preferred_username": "fallback-user",
            "realm_access": {"roles": ["engineering"]},
        }

        mock_user = MagicMock()
        mock_user.role = MagicMock()
        mock_user.role.value = "engineer"
        mock_aaa.get_user_by_email = AsyncMock(return_value=mock_user)

        db = AsyncMock()
        resp = await me(authorization="Bearer valid", db=db)

        assert resp.email == "fallback-user"


# ---------------------------------------------------------------------------
# /auth/refresh - extended coverage (lines 231-232)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRefreshEndpointExtended:
    """Extended tests for /auth/refresh endpoint."""

    @patch("request_manager.auth_endpoints.httpx.AsyncClient")
    async def test_refresh_returns_new_refresh_token(self, mock_client_cls):
        """Keycloak returns a rotated refresh token."""
        from request_manager.auth_endpoints import RefreshRequest, refresh

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "rotated-access",
            "refresh_token": "rotated-refresh",
        }
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client_cls.return_value = mock_client

        resp = await refresh(request=RefreshRequest(refresh_token="old"))

        assert resp.token == "rotated-access"
        assert resp.refresh_token == "rotated-refresh"

    @patch("request_manager.auth_endpoints.httpx.AsyncClient")
    async def test_refresh_no_refresh_token_in_response(self, mock_client_cls):
        """When Keycloak omits refresh_token, field is None."""
        from request_manager.auth_endpoints import RefreshRequest, refresh

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "new-token"}
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client_cls.return_value = mock_client

        resp = await refresh(request=RefreshRequest(refresh_token="rt"))

        assert resp.token == "new-token"
        assert resp.refresh_token is None
