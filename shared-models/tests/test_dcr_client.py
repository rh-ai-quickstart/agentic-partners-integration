"""Unit tests for the DCRClient (Change 5b).

Tests cover:
- Feature flag: active when DCR_ENABLED=true (default), no-op when false
- Registration flow: builds correct RFC 7591 payload and POSTs to Keycloak
- Caching: skips registration if RAT is still valid
- Re-registration: re-POSTs when RAT is rejected by Keycloak
- Error handling: surfaces useful messages on SPIRE / Keycloak failures
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx


# ── Feature-flag disabled (default) ─────────────────────────────────────────

class TestDCRDisabledByDefault:
    @pytest.mark.asyncio
    async def test_ensure_registered_active_when_default(self, monkeypatch):
        """When DCR_ENABLED is unset, it defaults to true — registration proceeds."""
        monkeypatch.delenv("DCR_ENABLED", raising=False)

        import importlib
        import shared_models.dcr_client as dcr_module
        importlib.reload(dcr_module)

        client = dcr_module.DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test-agent",
            client_name="test-agent",
        )

        with patch.object(client, "_register", new_callable=AsyncMock) as mock_register:
            await client.ensure_registered()
            mock_register.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_registered_noop_when_explicitly_false(self, monkeypatch):
        monkeypatch.setenv("DCR_ENABLED", "false")
        import importlib
        import shared_models.dcr_client as dcr_module
        importlib.reload(dcr_module)

        client = dcr_module.DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test-agent",
            client_name="test-agent",
        )

        with patch.object(client, "_register", new_callable=AsyncMock) as mock_register:
            await client.ensure_registered()
            mock_register.assert_not_called()

        importlib.reload(dcr_module)


# ── Registration flow ─────────────────────────────────────────────────────────

class TestDCRRegistrationFlow:
    @pytest.mark.asyncio
    async def test_register_posts_correct_payload(self, monkeypatch):
        """DCR POST includes SPIFFE URI in client_name and correct grant types."""
        monkeypatch.setenv("DCR_ENABLED", "true")
        monkeypatch.setenv("KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN", "test-iat-token")

        import importlib
        import shared_models.dcr_client as dcr_module
        importlib.reload(dcr_module)

        client = dcr_module.DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/kubernetes-agent",
            client_name="kubernetes-agent",
        )

        posted_payload = {}
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "registration_access_token": "rat-token-abc",
            "registration_client_uri": "http://keycloak/realms/partner-agent/clients-registrations/openid-connect/abc",
            "client_id": "generated-uuid",
            "client_secret": "generated-secret",
        }

        async def capture_post(url, json=None, headers=None, **kwargs):
            posted_payload.update(json or {})
            return mock_resp

        with patch("httpx.AsyncClient") as mock_http:
            mock_http_instance = AsyncMock()
            mock_http_instance.post = capture_post
            mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
            mock_http_instance.__aexit__ = AsyncMock(return_value=False)
            mock_http.return_value = mock_http_instance

            await client._register()

        assert "spiffe://partner.example.com/agent/kubernetes-agent" in posted_payload["client_name"]
        assert "kubernetes-agent" in posted_payload["client_name"]
        assert "client_credentials" in posted_payload["grant_types"]
        assert "urn:ietf:params:oauth:grant-type:token-exchange" in posted_payload["grant_types"]
        assert "client_id" not in posted_payload

        importlib.reload(dcr_module)

    @pytest.mark.asyncio
    async def test_register_stores_rat(self, monkeypatch):
        """After successful DCR, RAT and registration URI are stored."""
        monkeypatch.setenv("DCR_ENABLED", "true")
        monkeypatch.setenv("KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN", "test-iat-token")

        import importlib
        import shared_models.dcr_client as dcr_module
        importlib.reload(dcr_module)

        client = dcr_module.DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/kubernetes-agent",
            client_name="kubernetes-agent",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "registration_access_token": "rat-abc-123",
            "registration_client_uri": "http://keycloak/clients-registrations/abc",
            "client_id": "generated-uuid",
            "client_secret": "generated-secret",
        }

        with patch("httpx.AsyncClient") as mock_http:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            mock_http.return_value = instance
            await client._register()

        assert client._registration_access_token == "rat-abc-123"
        assert "abc" in client._registration_client_uri

        importlib.reload(dcr_module)


# ── RAT validation + re-registration ─────────────────────────────────────────

class TestDCRReRegistration:
    @pytest.mark.asyncio
    async def test_skips_registration_if_rat_valid(self, monkeypatch):
        """Does not re-register if existing RAT passes Keycloak GET."""
        monkeypatch.setenv("DCR_ENABLED", "true")

        import importlib
        import shared_models.dcr_client as dcr_module
        importlib.reload(dcr_module)

        client = dcr_module.DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )
        client._registration_access_token = "valid-rat"
        client._registration_client_uri = "http://keycloak/clients-registrations/valid"

        with patch.object(client, "_verify_registration", new_callable=AsyncMock, return_value=True):
            with patch.object(client, "_register", new_callable=AsyncMock) as mock_reg:
                await client.ensure_registered()
                mock_reg.assert_not_called()

        importlib.reload(dcr_module)

    @pytest.mark.asyncio
    async def test_reregisters_if_rat_rejected(self, monkeypatch):
        """Re-registers if existing RAT returns 401 from Keycloak."""
        monkeypatch.setenv("DCR_ENABLED", "true")

        import importlib
        import shared_models.dcr_client as dcr_module
        importlib.reload(dcr_module)

        client = dcr_module.DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )
        client._registration_access_token = "stale-rat"
        client._registration_client_uri = "http://keycloak/clients-registrations/stale"

        with patch.object(client, "_verify_registration", new_callable=AsyncMock, return_value=False):
            with patch.object(client, "_register", new_callable=AsyncMock) as mock_reg:
                await client.ensure_registered()
                mock_reg.assert_called_once()

        importlib.reload(dcr_module)


# ── Error handling ────────────────────────────────────────────────────────────

class TestDCRErrorHandling:
    @pytest.mark.asyncio
    async def test_raises_if_no_iat(self, monkeypatch):
        """RuntimeError raised when KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN is not set."""
        monkeypatch.setenv("DCR_ENABLED", "true")
        monkeypatch.delenv("KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN", raising=False)

        import importlib
        import shared_models.dcr_client as dcr_module
        importlib.reload(dcr_module)

        client = dcr_module.DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )

        with pytest.raises(RuntimeError, match="KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN"):
            await client._register()

        importlib.reload(dcr_module)

    @pytest.mark.asyncio
    async def test_raises_if_keycloak_returns_error(self, monkeypatch):
        """RuntimeError raised when Keycloak DCR POST returns 4xx."""
        monkeypatch.setenv("DCR_ENABLED", "true")
        monkeypatch.setenv("KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN", "test-iat-token")

        import importlib
        import shared_models.dcr_client as dcr_module
        importlib.reload(dcr_module)

        client = dcr_module.DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = '{"error": "invalid_request"}'

        with patch("httpx.AsyncClient") as mock_http:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            mock_http.return_value = instance

            with pytest.raises(RuntimeError, match="400"):
                await client._register()

        importlib.reload(dcr_module)


# ── ensure_registered retry loop ─────────────────────────────────────────────

class TestDCRRetryLoop:
    @pytest.mark.asyncio
    async def test_retry_loop_exhausted(self, monkeypatch):
        """ensure_registered raises after exhausting retries."""
        monkeypatch.setenv("DCR_ENABLED", "true")
        monkeypatch.setenv("DCR_MAX_RETRIES", "2")
        monkeypatch.setenv("DCR_RETRY_DELAY_SECONDS", "0")

        import importlib
        import shared_models.dcr_client as dcr_module
        importlib.reload(dcr_module)

        client = dcr_module.DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )

        with patch.object(
            client, "_register", new_callable=AsyncMock,
            side_effect=RuntimeError("keycloak down"),
        ):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(dcr_module, "logger"):
                    with pytest.raises(RuntimeError, match="DCR registration failed after"):
                        await client.ensure_registered()

        importlib.reload(dcr_module)

    @pytest.mark.asyncio
    async def test_verify_then_reregister(self, monkeypatch):
        """When verify fails, re-register is attempted."""
        monkeypatch.setenv("DCR_ENABLED", "true")

        import importlib
        import shared_models.dcr_client as dcr_module
        importlib.reload(dcr_module)

        client = dcr_module.DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )
        client._registration_access_token = "stale"
        client._registration_client_uri = "http://keycloak/stale"

        with patch.object(client, "_verify_registration", new_callable=AsyncMock, return_value=False):
            with patch.object(client, "_register", new_callable=AsyncMock) as mock_reg:
                await client.ensure_registered()
                mock_reg.assert_called_once()

        importlib.reload(dcr_module)


# ── get_credentials ──────────────────────────────────────────────────────────

class TestDCRGetCredentials:
    def test_returns_none_when_not_registered(self):
        from shared_models.dcr_client import DCRClient
        client = DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )
        assert client.get_credentials() is None

    def test_returns_credentials_when_registered(self):
        from shared_models.dcr_client import DCRClient
        client = DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )
        client._keycloak_client_id = "client-id"
        client._keycloak_client_secret = "client-secret"
        creds = client.get_credentials()
        assert creds == ("client-id", "client-secret")

    def test_returns_none_when_partial_credentials(self):
        from shared_models.dcr_client import DCRClient
        client = DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )
        client._keycloak_client_id = "client-id"
        client._keycloak_client_secret = None
        assert client.get_credentials() is None


# ── _verify_registration ─────────────────────────────────────────────────────

class TestDCRVerifyRegistration:
    @pytest.mark.asyncio
    async def test_verify_success(self):
        from shared_models.dcr_client import DCRClient
        client = DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )
        client._registration_access_token = "valid-rat"
        client._registration_client_uri = "http://keycloak/clients/abc"

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("httpx.AsyncClient") as mock_http:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            mock_http.return_value = instance

            result = await client._verify_registration()
        assert result is True

    @pytest.mark.asyncio
    async def test_verify_non_200(self):
        from shared_models.dcr_client import DCRClient
        client = DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )
        client._registration_access_token = "stale-rat"
        client._registration_client_uri = "http://keycloak/clients/abc"

        mock_resp = MagicMock()
        mock_resp.status_code = 401

        with patch("httpx.AsyncClient") as mock_http:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            mock_http.return_value = instance

            result = await client._verify_registration()
        assert result is False

    @pytest.mark.asyncio
    async def test_verify_exception(self):
        from shared_models.dcr_client import DCRClient
        client = DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )
        client._registration_access_token = "valid-rat"
        client._registration_client_uri = "http://keycloak/clients/abc"

        with patch("httpx.AsyncClient") as mock_http:
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=Exception("network error"))
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            mock_http.return_value = instance

            result = await client._verify_registration()
        assert result is False


# ── _register additional branches ────────────────────────────────────────────

class TestDCRRegisterBranches:
    @pytest.mark.asyncio
    async def test_network_error(self, monkeypatch):
        """Network error during DCR POST is wrapped in RuntimeError."""
        monkeypatch.setenv("KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN", "test-iat")

        import importlib
        import shared_models.dcr_client as dcr_module
        importlib.reload(dcr_module)

        import httpx

        client = dcr_module.DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )

        with patch("httpx.AsyncClient") as mock_http:
            instance = AsyncMock()
            instance.post = AsyncMock(
                side_effect=httpx.RequestError("connection refused", request=MagicMock())
            )
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            mock_http.return_value = instance

            with pytest.raises(RuntimeError, match="network error"):
                await client._register()

        importlib.reload(dcr_module)

    @pytest.mark.asyncio
    async def test_missing_rat_in_response(self, monkeypatch):
        """RuntimeError when Keycloak response is missing registration_access_token."""
        monkeypatch.setenv("KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN", "test-iat")

        import importlib
        import shared_models.dcr_client as dcr_module
        importlib.reload(dcr_module)

        client = dcr_module.DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "client_id": "generated-uuid",
            "client_secret": "generated-secret",
            # No registration_access_token!
        }

        with patch("httpx.AsyncClient") as mock_http:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            mock_http.return_value = instance

            with pytest.raises(RuntimeError, match="missing registration_access_token"):
                await client._register()

        importlib.reload(dcr_module)


# ── get_dcr_client singleton ─────────────────────────────────────────────────

class TestGetDcrClient:
    def test_singleton(self):
        import shared_models.dcr_client as mod
        original = mod._dcr_clients.copy()
        mod._dcr_clients.clear()
        try:
            c1 = mod.get_dcr_client("spiffe://test/a", "agent-a")
            c2 = mod.get_dcr_client("spiffe://test/a", "agent-a")
            assert c1 is c2
        finally:
            mod._dcr_clients.clear()
            mod._dcr_clients.update(original)

    def test_different_spiffe_ids_get_different_clients(self):
        import shared_models.dcr_client as mod
        original = mod._dcr_clients.copy()
        mod._dcr_clients.clear()
        try:
            c1 = mod.get_dcr_client("spiffe://test/a", "agent-a")
            c2 = mod.get_dcr_client("spiffe://test/b", "agent-b")
            assert c1 is not c2
        finally:
            mod._dcr_clients.clear()
            mod._dcr_clients.update(original)


# ── get_registered_dcr_credentials ───────────────────────────────────────────

class TestGetRegisteredDcrCredentials:
    def test_returns_credentials_from_registered_client(self):
        import shared_models.dcr_client as mod
        original = mod._dcr_clients.copy()
        mod._dcr_clients.clear()
        try:
            client = mod.DCRClient(spiffe_id="spiffe://test/a", client_name="a")
            client._keycloak_client_id = "id-1"
            client._keycloak_client_secret = "secret-1"
            mod._dcr_clients["spiffe://test/a"] = client

            creds = mod.get_registered_dcr_credentials()
            assert creds == ("id-1", "secret-1")
        finally:
            mod._dcr_clients.clear()
            mod._dcr_clients.update(original)

    def test_returns_none_when_no_registered_clients(self):
        import shared_models.dcr_client as mod
        original = mod._dcr_clients.copy()
        mod._dcr_clients.clear()
        try:
            creds = mod.get_registered_dcr_credentials()
            assert creds is None
        finally:
            mod._dcr_clients.clear()
            mod._dcr_clients.update(original)

    def test_returns_none_when_client_not_registered(self):
        import shared_models.dcr_client as mod
        original = mod._dcr_clients.copy()
        mod._dcr_clients.clear()
        try:
            client = mod.DCRClient(spiffe_id="spiffe://test/a", client_name="a")
            mod._dcr_clients["spiffe://test/a"] = client

            creds = mod.get_registered_dcr_credentials()
            assert creds is None
        finally:
            mod._dcr_clients.clear()
            mod._dcr_clients.update(original)


# ── _get_jwt_svid_assertion ────────────────────────────────────────────────

class TestGetJwtSvidAssertion:
    def test_success_returns_jwt(self, monkeypatch):
        """When SPIRE returns a JWT-SVID, it is returned as-is."""
        monkeypatch.setenv("KEYCLOAK_URL", "http://keycloak:8080")
        monkeypatch.setenv("KEYCLOAK_REALM", "partner-agent")

        import importlib
        import shared_models.dcr_client as dcr_module
        importlib.reload(dcr_module)

        client = dcr_module.DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )

        mock_spire = MagicMock()
        mock_spire.fetch_jwt_svid.return_value = "eyJhbGciOiJSUzI1NiJ9.test.jwt"

        with patch("shared_models.spire_client.get_spire_client", return_value=mock_spire):
            result = client._get_jwt_svid_assertion()

        assert result == "eyJhbGciOiJSUzI1NiJ9.test.jwt"
        mock_spire.fetch_jwt_svid.assert_called_once_with(
            audience="http://keycloak:8080/realms/partner-agent"
        )

        importlib.reload(dcr_module)

    def test_failure_returns_none(self, monkeypatch):
        """When SPIRE raises an exception, None is returned."""
        monkeypatch.setenv("KEYCLOAK_URL", "http://keycloak:8080")
        monkeypatch.setenv("KEYCLOAK_REALM", "partner-agent")

        import importlib
        import shared_models.dcr_client as dcr_module
        importlib.reload(dcr_module)

        client = dcr_module.DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )

        mock_spire = MagicMock()
        mock_spire.fetch_jwt_svid.side_effect = RuntimeError("SPIRE socket not found")

        with patch("shared_models.spire_client.get_spire_client", return_value=mock_spire):
            result = client._get_jwt_svid_assertion()

        assert result is None

        importlib.reload(dcr_module)


# ── _register with SPIRE_AUTH_MODE ─────────────────────────────────────────

class TestDCRRegisterSpiffeAuthMode:
    @pytest.mark.asyncio
    async def test_spiffe_mode_with_jwt_svid_available(self, monkeypatch):
        """SPIRE_AUTH_MODE=spiffe + JWT-SVID available: payload includes jwks_uri, auth uses JWT-SVID."""
        monkeypatch.setenv("SPIRE_AUTH_MODE", "spiffe")
        monkeypatch.setenv("SPIRE_OIDC_JWKS_URI", "http://spire-oidc:8443/.well-known/jwks.json")
        monkeypatch.setenv("KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN", "test-iat")

        import importlib
        import shared_models.dcr_client as dcr_module
        importlib.reload(dcr_module)

        client = dcr_module.DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )

        captured_payload = {}
        captured_headers = {}

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "registration_access_token": "rat-spiffe",
            "registration_client_uri": "http://keycloak/clients/spiffe-client",
            "client_id": "spiffe-uuid",
            "client_secret": "spiffe-secret",
        }

        async def capture_post(url, json=None, headers=None, **kwargs):
            captured_payload.update(json or {})
            captured_headers.update(headers or {})
            return mock_resp

        with patch.object(client, "_get_jwt_svid_assertion", return_value="eyJ.spiffe.jwt"):
            with patch("httpx.AsyncClient") as mock_http:
                instance = AsyncMock()
                instance.post = capture_post
                instance.__aenter__ = AsyncMock(return_value=instance)
                instance.__aexit__ = AsyncMock(return_value=False)
                mock_http.return_value = instance

                await client._register()

        assert captured_payload["token_endpoint_auth_method"] == "private_key_jwt"
        assert captured_payload["token_endpoint_auth_signing_alg"] == "RS256"
        assert captured_payload["jwks_uri"] == "http://spire-oidc:8443/.well-known/jwks.json"
        assert captured_headers["Authorization"] == "Bearer eyJ.spiffe.jwt"

        importlib.reload(dcr_module)

    @pytest.mark.asyncio
    async def test_spiffe_mode_fallback_to_iat(self, monkeypatch):
        """SPIRE_AUTH_MODE=spiffe + JWT-SVID unavailable: falls back to IAT bearer."""
        monkeypatch.setenv("SPIRE_AUTH_MODE", "spiffe")
        monkeypatch.setenv("KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN", "fallback-iat")

        import importlib
        import shared_models.dcr_client as dcr_module
        importlib.reload(dcr_module)

        client = dcr_module.DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )

        captured_headers = {}

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "registration_access_token": "rat-fallback",
            "registration_client_uri": "http://keycloak/clients/fallback",
            "client_id": "fallback-uuid",
            "client_secret": "fallback-secret",
        }

        async def capture_post(url, json=None, headers=None, **kwargs):
            captured_headers.update(headers or {})
            return mock_resp

        with patch.object(client, "_get_jwt_svid_assertion", return_value=None):
            with patch("httpx.AsyncClient") as mock_http:
                instance = AsyncMock()
                instance.post = capture_post
                instance.__aenter__ = AsyncMock(return_value=instance)
                instance.__aexit__ = AsyncMock(return_value=False)
                mock_http.return_value = instance

                await client._register()

        assert captured_headers["Authorization"] == "Bearer fallback-iat"

        importlib.reload(dcr_module)

    @pytest.mark.asyncio
    async def test_spiffe_mode_no_svid_no_iat_raises(self, monkeypatch):
        """SPIRE_AUTH_MODE=spiffe + no JWT-SVID + no IAT: raises RuntimeError."""
        monkeypatch.setenv("SPIRE_AUTH_MODE", "spiffe")
        monkeypatch.delenv("KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN", raising=False)

        import importlib
        import shared_models.dcr_client as dcr_module
        importlib.reload(dcr_module)

        client = dcr_module.DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )

        with patch.object(client, "_get_jwt_svid_assertion", return_value=None):
            with pytest.raises(RuntimeError, match="SPIFFE JWT-SVID unavailable"):
                await client._register()

        importlib.reload(dcr_module)

    @pytest.mark.asyncio
    async def test_iat_mode_still_works(self, monkeypatch):
        """Default SPIRE_AUTH_MODE=iat: existing IAT-based registration behavior unchanged."""
        monkeypatch.setenv("SPIRE_AUTH_MODE", "iat")
        monkeypatch.setenv("KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN", "test-iat-token")

        import importlib
        import shared_models.dcr_client as dcr_module
        importlib.reload(dcr_module)

        client = dcr_module.DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )

        captured_headers = {}
        captured_payload = {}

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "registration_access_token": "rat-iat",
            "registration_client_uri": "http://keycloak/clients/iat",
            "client_id": "iat-uuid",
            "client_secret": "iat-secret",
        }

        async def capture_post(url, json=None, headers=None, **kwargs):
            captured_headers.update(headers or {})
            captured_payload.update(json or {})
            return mock_resp

        with patch("httpx.AsyncClient") as mock_http:
            instance = AsyncMock()
            instance.post = capture_post
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            mock_http.return_value = instance

            await client._register()

        assert captured_headers["Authorization"] == "Bearer test-iat-token"
        assert "token_endpoint_auth_method" not in captured_payload
        assert "jwks_uri" not in captured_payload

        importlib.reload(dcr_module)
