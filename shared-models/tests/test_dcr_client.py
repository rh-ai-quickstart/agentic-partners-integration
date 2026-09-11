"""Unit tests for the DCRClient (Change 5b).

Tests cover:
- Feature flag: no-op when DCR_ENABLED=false (default)
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
    async def test_ensure_registered_noop_when_disabled(self, monkeypatch):
        """When DCR_ENABLED is unset, ensure_registered() does nothing."""
        monkeypatch.delenv("DCR_ENABLED", raising=False)

        from shared_models.dcr_client import DCRClient
        client = DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test-agent",
            client_name="test-agent",
        )

        with patch.object(client, "_register", new_callable=AsyncMock) as mock_register:
            await client.ensure_registered()
            mock_register.assert_not_called()

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
        """DCR POST includes SPIFFE URI as client_id and jwks_uri for SPIRE."""
        monkeypatch.setenv("DCR_ENABLED", "true")

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
        }

        async def capture_post(url, json=None, headers=None, **kwargs):
            posted_payload.update(json or {})
            return mock_resp

        mock_spire_client = MagicMock()
        mock_spire_client.fetch_jwt_svid.return_value = "svid.jwt.token"

        with patch("shared_models.dcr_client.get_spire_client", return_value=mock_spire_client):
            with patch("httpx.AsyncClient") as mock_http:
                mock_http_instance = AsyncMock()
                mock_http_instance.post = capture_post
                mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
                mock_http_instance.__aexit__ = AsyncMock(return_value=False)
                mock_http.return_value = mock_http_instance

                await client._register()

        assert posted_payload["client_id"] == "spiffe://partner.example.com/agent/kubernetes-agent"
        assert posted_payload["client_name"] == "kubernetes-agent"
        assert posted_payload["token_endpoint_auth_method"] == "private_key_jwt"
        assert "jwks_uri" in posted_payload
        assert "client_credentials" in posted_payload["grant_types"]
        assert "urn:ietf:params:oauth:grant-type:token-exchange" in posted_payload["grant_types"]

        importlib.reload(dcr_module)

    @pytest.mark.asyncio
    async def test_register_stores_rat(self, monkeypatch):
        """After successful DCR, RAT and registration URI are stored."""
        monkeypatch.setenv("DCR_ENABLED", "true")

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
        }

        mock_spire = MagicMock()
        mock_spire.fetch_jwt_svid.return_value = "jwt.token"

        with patch("shared_models.dcr_client.get_spire_client", return_value=mock_spire):
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
    async def test_raises_if_spire_unavailable(self, monkeypatch):
        """RuntimeError raised when SPIRE JWT-SVID fetch fails."""
        monkeypatch.setenv("DCR_ENABLED", "true")

        import importlib
        import shared_models.dcr_client as dcr_module
        importlib.reload(dcr_module)

        client = dcr_module.DCRClient(
            spiffe_id="spiffe://partner.example.com/agent/test",
            client_name="test",
        )

        mock_spire = MagicMock()
        mock_spire.fetch_jwt_svid.side_effect = RuntimeError("SPIRE not available")

        with patch("shared_models.dcr_client.get_spire_client", return_value=mock_spire):
            with pytest.raises(RuntimeError, match="SPIRE"):
                await client._register()

        importlib.reload(dcr_module)

    @pytest.mark.asyncio
    async def test_raises_if_keycloak_returns_error(self, monkeypatch):
        """RuntimeError raised when Keycloak DCR POST returns 4xx."""
        monkeypatch.setenv("DCR_ENABLED", "true")

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

        mock_spire = MagicMock()
        mock_spire.fetch_jwt_svid.return_value = "jwt.token"

        with patch("shared_models.dcr_client.get_spire_client", return_value=mock_spire):
            with patch("httpx.AsyncClient") as mock_http:
                instance = AsyncMock()
                instance.post = AsyncMock(return_value=mock_resp)
                instance.__aenter__ = AsyncMock(return_value=instance)
                instance.__aexit__ = AsyncMock(return_value=False)
                mock_http.return_value = instance

                with pytest.raises(RuntimeError, match="400"):
                    await client._register()

        importlib.reload(dcr_module)
