"""Tests for token_exchange.py — covering remaining uncovered lines."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import jwt

from request_manager.token_exchange import (
    TokenExchangeClient,
    TokenExchangeError,
    _get_dcr_actor_token,
    _get_spiffe_client_assertion,
)


def _make_test_token(**claims):
    """Create a test JWT with given claims."""
    payload = {"sub": "user-123", "aud": "partner-agent-ui", "exp": 9999999999}
    payload.update(claims)
    return jwt.encode(payload, "test-secret", algorithm="HS256")


# ---------------------------------------------------------------------------
# _get_dcr_actor_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGetDcrActorToken:
    """Tests for _get_dcr_actor_token (lines 84, 96-97, 111-130)."""

    @patch("request_manager.token_exchange.DCR_ENABLED", False)
    async def test_returns_none_when_dcr_disabled(self):
        """When DCR_ENABLED is False, return None immediately (line 84)."""
        result = await _get_dcr_actor_token("https://keycloak/token")
        assert result is None

    @patch("request_manager.token_exchange.DCR_ENABLED", True)
    async def test_dcr_enabled_full_path_with_spire(self):
        """DCR enabled: SPIRE fetch + credential retrieval + HTTP call (lines 96-97, 111-123)."""
        mock_svid = MagicMock()
        mock_svid.spiffe_id = "spiffe://test/service/request-manager"

        mock_spire = MagicMock()
        mock_spire.fetch_svid.return_value = mock_svid

        mock_dcr = MagicMock()
        mock_dcr.get_credentials.return_value = ("dcr-client-id", "dcr-secret")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "dcr-actor-token"}

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        with (
            patch(
                "shared_models.spire_client.get_spire_client",
                return_value=mock_spire,
            ),
            patch(
                "shared_models.dcr_client.get_dcr_client",
                return_value=mock_dcr,
            ),
            patch(
                "shared_models.dcr_client.get_registered_dcr_credentials",
            ),
            patch(
                "request_manager.token_exchange.httpx.AsyncClient",
            ) as mock_httpx_cls,
        ):
            mock_httpx_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_httpx_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _get_dcr_actor_token("https://keycloak/token")

        assert result == "dcr-actor-token"

    @patch("request_manager.token_exchange.DCR_ENABLED", True)
    async def test_dcr_spire_failure_falls_back_to_env(self):
        """When SPIRE fetch fails, fall back to env var (lines 98-104)."""
        mock_dcr = MagicMock()
        mock_dcr.get_credentials.return_value = ("dcr-id", "dcr-secret")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "fallback-token"}

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        with (
            patch(
                "shared_models.spire_client.get_spire_client",
                side_effect=RuntimeError("SPIRE unavailable"),
            ),
            patch(
                "shared_models.dcr_client.get_dcr_client",
                return_value=mock_dcr,
            ),
            patch(
                "shared_models.dcr_client.get_registered_dcr_credentials",
            ),
            patch(
                "request_manager.token_exchange.httpx.AsyncClient",
            ) as mock_httpx_cls,
        ):
            mock_httpx_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_httpx_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _get_dcr_actor_token("https://keycloak/token")

        assert result == "fallback-token"

    @patch("request_manager.token_exchange.DCR_ENABLED", True)
    async def test_dcr_spire_returns_none_svid(self):
        """When SPIRE returns None SVID, fall back to env (lines 96-97 None branch)."""
        mock_spire = MagicMock()
        mock_spire.fetch_svid.return_value = None

        mock_dcr = MagicMock()
        mock_dcr.get_credentials.return_value = ("dcr-id", "dcr-secret")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "ok"}

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        with (
            patch(
                "shared_models.spire_client.get_spire_client",
                return_value=mock_spire,
            ),
            patch(
                "shared_models.dcr_client.get_dcr_client",
                return_value=mock_dcr,
            ),
            patch(
                "shared_models.dcr_client.get_registered_dcr_credentials",
            ),
            patch(
                "request_manager.token_exchange.httpx.AsyncClient",
            ) as mock_httpx_cls,
        ):
            mock_httpx_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_httpx_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _get_dcr_actor_token("https://keycloak/token")

        assert result == "ok"

    @patch("request_manager.token_exchange.DCR_ENABLED", True)
    async def test_dcr_no_credentials_returns_none(self):
        """When DCR client has no credentials, return None (line 110)."""
        mock_spire = MagicMock()
        mock_svid = MagicMock()
        mock_svid.spiffe_id = "spiffe://test/svc"
        mock_spire.fetch_svid.return_value = mock_svid

        mock_dcr = MagicMock()
        mock_dcr.get_credentials.return_value = None

        with (
            patch(
                "shared_models.spire_client.get_spire_client",
                return_value=mock_spire,
            ),
            patch(
                "shared_models.dcr_client.get_dcr_client",
                return_value=mock_dcr,
            ),
            patch(
                "shared_models.dcr_client.get_registered_dcr_credentials",
                return_value=None,
            ),
        ):
            result = await _get_dcr_actor_token("https://keycloak/token")

        assert result is None

    @patch("request_manager.token_exchange.DCR_ENABLED", True)
    async def test_dcr_fallback_to_registered_credentials(self):
        """When dcr.get_credentials returns None, falls back to get_registered_dcr_credentials (line 108)."""
        mock_spire = MagicMock()
        mock_svid = MagicMock()
        mock_svid.spiffe_id = "spiffe://test/svc"
        mock_spire.fetch_svid.return_value = mock_svid

        mock_dcr = MagicMock()
        mock_dcr.get_credentials.return_value = None

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "from-registered"}

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        with (
            patch(
                "shared_models.spire_client.get_spire_client",
                return_value=mock_spire,
            ),
            patch(
                "shared_models.dcr_client.get_dcr_client",
                return_value=mock_dcr,
            ),
            patch(
                "shared_models.dcr_client.get_registered_dcr_credentials",
                return_value=("reg-id", "reg-secret"),
            ),
            patch(
                "request_manager.token_exchange.httpx.AsyncClient",
            ) as mock_httpx_cls,
        ):
            mock_httpx_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_httpx_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _get_dcr_actor_token("https://keycloak/token")

        assert result == "from-registered"

    @patch("request_manager.token_exchange.DCR_ENABLED", True)
    async def test_dcr_non_200_response(self):
        """When DCR token request returns non-200, return None (lines 124-127)."""
        mock_spire = MagicMock()
        mock_svid = MagicMock()
        mock_svid.spiffe_id = "spiffe://test/svc"
        mock_spire.fetch_svid.return_value = mock_svid

        mock_dcr = MagicMock()
        mock_dcr.get_credentials.return_value = ("dcr-id", "dcr-secret")

        mock_response = MagicMock()
        mock_response.status_code = 401

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        with (
            patch(
                "shared_models.spire_client.get_spire_client",
                return_value=mock_spire,
            ),
            patch(
                "shared_models.dcr_client.get_dcr_client",
                return_value=mock_dcr,
            ),
            patch(
                "shared_models.dcr_client.get_registered_dcr_credentials",
            ),
            patch(
                "request_manager.token_exchange.httpx.AsyncClient",
            ) as mock_httpx_cls,
        ):
            mock_httpx_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_httpx_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _get_dcr_actor_token("https://keycloak/token")

        assert result is None

    @patch("request_manager.token_exchange.DCR_ENABLED", True)
    async def test_dcr_exception_returns_none(self):
        """When DCR flow raises exception, return None (lines 128-130)."""
        with patch(
            "shared_models.spire_client.get_spire_client",
            side_effect=RuntimeError("SPIRE broken"),
        ), patch(
            "shared_models.dcr_client.get_dcr_client",
            side_effect=RuntimeError("DCR broken"),
        ):
            result = await _get_dcr_actor_token("https://keycloak/token")

        assert result is None


# ---------------------------------------------------------------------------
# exchange_for_agent — missing access_token branch (lines 395-426)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExchangeForAgentMissingAccessToken:
    """Tests for exchange_for_agent missing access_token in response."""

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_missing_access_token_raises(self, mock_httpx, mock_audit):
        """When response is 200 but has no access_token, raise error (lines 394-428)."""
        test_token = _make_test_token()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "token_type": "Bearer",
            "expires_in": 300,
            # No access_token!
        }

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        mock_audit.emit = AsyncMock()

        client = TokenExchangeClient()
        with pytest.raises(TokenExchangeError, match="missing access_token"):
            await client.exchange_for_agent(
                subject_token=test_token,
                target_agent="agent-x",
                actor_service="request-manager",
            )

        # Verify audit event was emitted for the failure
        mock_audit.emit.assert_called_once()
        call_args = mock_audit.emit.call_args[1]
        assert call_args["outcome"] == "failure"
        assert "missing access_token" in call_args["reason"]

        await client.close()

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_missing_access_token_audit_failure(self, mock_httpx, mock_audit):
        """When audit emit fails during missing access_token path (lines 419-424)."""
        test_token = _make_test_token()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"token_type": "Bearer"}

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        mock_audit.emit = AsyncMock(side_effect=Exception("DB error"))

        client = TokenExchangeClient()
        with pytest.raises(TokenExchangeError, match="missing access_token"):
            await client.exchange_for_agent(
                subject_token=test_token,
                target_agent="agent-x",
                actor_service="request-manager",
            )

        await client.close()


# ---------------------------------------------------------------------------
# exchange_for_agent — audit emit failure on non-200 (lines 378-379)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExchangeForAgentAuditFailure:
    """Tests for audit emit failure during non-200 response."""

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_non200_audit_emit_failure(self, mock_httpx, mock_audit):
        """When audit emit fails during non-200 response, still raises TokenExchangeError (lines 378-383)."""
        test_token = _make_test_token()

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"error": "bad_request"}
        mock_response.text = "bad_request"

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        mock_audit.emit = AsyncMock(side_effect=Exception("audit broken"))

        client = TokenExchangeClient()
        with pytest.raises(TokenExchangeError):
            await client.exchange_for_agent(
                subject_token=test_token,
                target_agent="agent-x",
                actor_service="svc",
            )

        await client.close()


# ---------------------------------------------------------------------------
# exchange_for_agent — HTTPStatusError (lines 482-517)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExchangeForAgentHTTPStatusError:
    """Tests for HTTPStatusError handling in exchange_for_agent."""

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_http_status_error_raises_exchange_error(self, mock_httpx, mock_audit):
        """HTTPStatusError is caught and raises TokenExchangeError (lines 482-521)."""
        test_token = _make_test_token()

        mock_err_response = MagicMock()
        mock_err_response.status_code = 502
        mock_err_response.headers = {"content-type": "text/plain"}
        mock_err_response.json.side_effect = Exception("not json")
        mock_err_response.text = "Bad Gateway"

        mock_client = MagicMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "502 Bad Gateway",
                request=MagicMock(),
                response=mock_err_response,
            )
        )
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        mock_audit.emit = AsyncMock()

        client = TokenExchangeClient()
        with pytest.raises(TokenExchangeError, match="HTTP 502"):
            await client.exchange_for_agent(
                subject_token=test_token,
                target_agent="agent-x",
                actor_service="svc",
            )

        # Verify audit was emitted
        assert mock_audit.emit.called
        call_args = mock_audit.emit.call_args[1]
        assert call_args["outcome"] == "failure"
        assert "502" in call_args["reason"]

        await client.close()

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_http_status_error_audit_failure(self, mock_httpx, mock_audit):
        """HTTPStatusError with audit failure still raises (lines 510-515)."""
        test_token = _make_test_token()

        mock_err_response = MagicMock()
        mock_err_response.status_code = 503
        mock_err_response.headers = {"content-type": "text/plain"}
        mock_err_response.json.side_effect = Exception("no json")
        mock_err_response.text = "Service Unavailable"

        mock_client = MagicMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "503",
                request=MagicMock(),
                response=mock_err_response,
            )
        )
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        mock_audit.emit = AsyncMock(side_effect=Exception("audit down"))

        client = TokenExchangeClient()
        with pytest.raises(TokenExchangeError, match="HTTP 503"):
            await client.exchange_for_agent(
                subject_token=test_token,
                target_agent="agent-x",
                actor_service="svc",
            )

        await client.close()


# ---------------------------------------------------------------------------
# exchange_for_agent — TimeoutException audit failure (lines 549-550)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExchangeForAgentTimeoutAuditFailure:
    """Tests for timeout + audit failure."""

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_timeout_audit_failure(self, mock_httpx, mock_audit):
        """Timeout with audit failure still re-raises timeout (lines 549-554)."""
        test_token = _make_test_token()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.TimeoutException("timed out")
        )
        mock_client.timeout = MagicMock(read=30.0)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        mock_audit.emit = AsyncMock(side_effect=Exception("audit broken"))

        client = TokenExchangeClient()
        with pytest.raises(httpx.TimeoutException):
            await client.exchange_for_agent(
                subject_token=test_token,
                target_agent="agent-x",
                actor_service="svc",
            )

        await client.close()


# ---------------------------------------------------------------------------
# exchange_for_agent — HTTPError (network error) (lines 559-592)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExchangeForAgentNetworkError:
    """Tests for network error (HTTPError) handling."""

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_network_error_raises_exchange_error(self, mock_httpx, mock_audit):
        """HTTPError (generic) is caught and raises TokenExchangeError (lines 558-594)."""
        test_token = _make_test_token()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        mock_audit.emit = AsyncMock()

        client = TokenExchangeClient()
        with pytest.raises(TokenExchangeError, match="Network error"):
            await client.exchange_for_agent(
                subject_token=test_token,
                target_agent="agent-x",
                actor_service="svc",
            )

        # Verify audit event was emitted
        assert mock_audit.emit.called
        call_args = mock_audit.emit.call_args[1]
        assert call_args["outcome"] == "failure"
        assert "Network error" in call_args["reason"]

        await client.close()

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_network_error_audit_failure(self, mock_httpx, mock_audit):
        """HTTPError with audit failure still raises TokenExchangeError (lines 585-590)."""
        test_token = _make_test_token()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        mock_audit.emit = AsyncMock(side_effect=Exception("audit down"))

        client = TokenExchangeClient()
        with pytest.raises(TokenExchangeError, match="Network error"):
            await client.exchange_for_agent(
                subject_token=test_token,
                target_agent="agent-x",
                actor_service="svc",
            )

        await client.close()


# ---------------------------------------------------------------------------
# exchange_with_delegation — act claim extraction failure (lines 675-676)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExchangeWithDelegationErrors:
    """Tests for exchange_with_delegation error paths."""

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_act_claim_extraction_failure(self, mock_httpx, mock_audit):
        """When subject_token decode fails, continue without act claim (lines 675-676)."""
        # A token that can't be decoded for act claim extraction
        bad_token = "totally-not-a-jwt"

        # But the exchange itself succeeds
        new_token = _make_test_token(aud="agent-b")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": new_token,
            "token_type": "Bearer",
            "expires_in": 300,
        }

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        mock_audit.emit = AsyncMock()

        client = TokenExchangeClient()
        result = await client.exchange_with_delegation(
            subject_token=bad_token,
            target_agent="agent-b",
            actor_service="agent-a",
        )

        assert "access_token" in result
        assert "delegation_chain" in result

        await client.close()


# ---------------------------------------------------------------------------
# _extract_error_detail (lines 763-768)
# ---------------------------------------------------------------------------


class TestExtractErrorDetail:
    """Tests for _extract_error_detail."""

    def test_json_with_error_description(self):
        """JSON response with error_description returns it (line 762)."""
        client = TokenExchangeClient()
        response = MagicMock()
        response.headers = {"content-type": "application/json"}
        response.json.return_value = {
            "error_description": "Token is invalid",
            "error": "invalid_grant",
        }

        result = client._extract_error_detail(response)
        assert result == "Token is invalid"

    def test_json_with_error_only(self):
        """JSON response with only error returns it (line 764)."""
        client = TokenExchangeClient()
        response = MagicMock()
        response.headers = {"content-type": "application/json"}
        response.json.return_value = {"error": "unauthorized_client"}

        result = client._extract_error_detail(response)
        assert result == "unauthorized_client"

    def test_json_with_neither(self):
        """JSON response without error or error_description returns str(data) (line 765)."""
        client = TokenExchangeClient()
        response = MagicMock()
        response.headers = {"content-type": "application/json"}
        response.json.return_value = {"message": "something wrong"}

        result = client._extract_error_detail(response)
        assert "something wrong" in result

    def test_non_json_response(self):
        """Non-JSON response returns text[:200] (line 766)."""
        client = TokenExchangeClient()
        response = MagicMock()
        response.headers = {"content-type": "text/html"}
        response.text = "<html>Error page</html>"
        response.status_code = 500

        result = client._extract_error_detail(response)
        assert result == "<html>Error page</html>"

    def test_exception_during_parsing(self):
        """When parsing fails, return HTTP status code (lines 767-768)."""
        client = TokenExchangeClient()
        response = MagicMock()
        response.headers = {"content-type": "application/json"}
        response.json.side_effect = Exception("parse error")
        response.status_code = 503

        result = client._extract_error_detail(response)
        assert result == "HTTP 503"


# ---------------------------------------------------------------------------
# close / __aenter__ / __aexit__ (lines 777, 781)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTokenExchangeClientContextManager:
    """Tests for close() and async context manager."""

    async def test_close_calls_aclose(self):
        """close() calls client.aclose() (line 772)."""
        client = TokenExchangeClient()
        client.client = AsyncMock()
        await client.close()
        client.client.aclose.assert_awaited_once()

    async def test_async_context_manager(self):
        """Async context manager enters and exits correctly (lines 776-781)."""
        client = TokenExchangeClient()
        client.client = AsyncMock()

        async with client as c:
            assert c is client

        client.client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# exchange_for_agent — DCR actor token path (lines 282, 294, 301-302)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExchangeForAgentDCRPath:
    """Tests for DCR actor token path in exchange_for_agent."""

    @patch("request_manager.token_exchange.DCR_ENABLED", True)
    @patch("request_manager.token_exchange._get_dcr_actor_token")
    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_dcr_actor_token_used(self, mock_httpx, mock_audit, mock_dcr_fn):
        """When DCR returns actor token, it's included as actor_token (lines 280-302)."""
        test_token = _make_test_token()
        mock_dcr_fn.return_value = "dcr-svc-token"

        new_token = _make_test_token(aud="agent-x")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": new_token,
            "token_type": "Bearer",
            "expires_in": 300,
        }

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        mock_audit.emit = AsyncMock()

        client = TokenExchangeClient()
        result = await client.exchange_for_agent(
            subject_token=test_token,
            target_agent="agent-x",
            actor_service="request-manager",
        )

        assert result["access_token"] == new_token

        # Verify actor_token was sent in payload
        call_args = mock_client.post.call_args
        payload = call_args[1]["data"]
        assert payload["actor_token"] == "dcr-svc-token"
        assert payload["actor_token_type"] == "urn:ietf:params:oauth:token-type:access_token"

        await client.close()

    @patch("request_manager.token_exchange.KEYCLOAK_CLIENT_SECRET", "my-secret")
    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_client_secret_included_in_payload(self, mock_httpx, mock_audit):
        """When KEYCLOAK_CLIENT_SECRET is set, it is included in the exchange payload (line 294)."""
        test_token = _make_test_token()

        new_token = _make_test_token(aud="agent-x")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": new_token,
            "token_type": "Bearer",
            "expires_in": 300,
        }

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        mock_audit.emit = AsyncMock()

        client = TokenExchangeClient()
        await client.exchange_for_agent(
            subject_token=test_token,
            target_agent="agent-x",
            actor_service="request-manager",
        )

        # Verify client_secret was sent in payload
        call_args = mock_client.post.call_args
        payload = call_args[1]["data"]
        assert payload["client_secret"] == "my-secret"

        await client.close()


# ---------------------------------------------------------------------------
# exchange_for_agent — RFC 8693 V2 audience parameter (line 290)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExchangeForAgentAudienceParam:
    """Tests for the audience parameter in the V2 token exchange payload."""

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_audience_included_in_payload(self, mock_httpx, mock_audit):
        """The exchange payload includes 'audience' set to target_agent (line 290)."""
        test_token = _make_test_token()

        new_token = _make_test_token(aud="kubernetes-agent")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": new_token,
            "token_type": "Bearer",
            "expires_in": 300,
        }

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        mock_audit.emit = AsyncMock()

        client = TokenExchangeClient()
        await client.exchange_for_agent(
            subject_token=test_token,
            target_agent="kubernetes-agent",
            actor_service="request-manager",
        )

        call_args = mock_client.post.call_args
        payload = call_args[1]["data"]
        assert payload["audience"] == "kubernetes-agent"

        await client.close()

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_audience_matches_target_agent(self, mock_httpx, mock_audit):
        """The audience value always matches the target_agent parameter."""
        test_token = _make_test_token()

        for target in ["agent-service", "network-agent", "software-support"]:
            new_token = _make_test_token(aud=target)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "access_token": new_token,
                "token_type": "Bearer",
                "expires_in": 300,
            }

            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.aclose = AsyncMock()
            mock_httpx.return_value = mock_client

            mock_audit.emit = AsyncMock()

            client = TokenExchangeClient()
            await client.exchange_for_agent(
                subject_token=test_token,
                target_agent=target,
                actor_service="request-manager",
            )

            call_args = mock_client.post.call_args
            payload = call_args[1]["data"]
            assert payload["audience"] == target, f"audience mismatch for target {target}"

            await client.close()


# ---------------------------------------------------------------------------
# Post-exchange sub verification (lines 431-456)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPostExchangeSubVerification:
    """Tests for the post-exchange subject verification guard."""

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_sub_match_passes(self, mock_httpx, mock_audit):
        """When original and exchanged tokens have the same sub, exchange succeeds."""
        test_token = _make_test_token(sub="user-42")
        exchanged_token = _make_test_token(sub="user-42", aud="agent-x")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": exchanged_token,
            "token_type": "Bearer",
            "expires_in": 300,
        }

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client
        mock_audit.emit = AsyncMock()

        client = TokenExchangeClient()
        result = await client.exchange_for_agent(
            subject_token=test_token,
            target_agent="agent-x",
            actor_service="request-manager",
        )
        assert result["access_token"] == exchanged_token
        await client.close()

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_sub_mismatch_raises(self, mock_httpx, mock_audit):
        """When original and exchanged tokens have different subs, raise TokenExchangeError."""
        test_token = _make_test_token(sub="user-42")
        exchanged_token = _make_test_token(sub="attacker-99", aud="agent-x")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": exchanged_token,
            "token_type": "Bearer",
            "expires_in": 300,
        }

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client
        mock_audit.emit = AsyncMock()

        client = TokenExchangeClient()
        with pytest.raises(TokenExchangeError, match="Subject changed during token exchange"):
            await client.exchange_for_agent(
                subject_token=test_token,
                target_agent="agent-x",
                actor_service="request-manager",
            )
        await client.close()

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_sub_decode_failure_skips_check(self, mock_httpx, mock_audit):
        """When subject_token can't be decoded, the sub check is skipped silently."""
        exchanged_token = _make_test_token(sub="user-42", aud="agent-x")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": exchanged_token,
            "token_type": "Bearer",
            "expires_in": 300,
        }

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client
        mock_audit.emit = AsyncMock()

        client = TokenExchangeClient()
        result = await client.exchange_for_agent(
            subject_token="not-a-valid-jwt",
            target_agent="agent-x",
            actor_service="request-manager",
        )
        assert result["access_token"] == exchanged_token
        await client.close()


# ---------------------------------------------------------------------------
# _get_spiffe_client_assertion
# ---------------------------------------------------------------------------


class TestGetSpiffeClientAssertion:
    """Tests for _get_spiffe_client_assertion (lines 84-93)."""

    @patch("request_manager.token_exchange.SPIRE_AUTH_MODE", "iat")
    def test_returns_none_when_not_spiffe_mode(self):
        """When SPIRE_AUTH_MODE != 'spiffe', return None immediately."""
        result = _get_spiffe_client_assertion()
        assert result is None

    @patch("request_manager.token_exchange.SPIRE_AUTH_MODE", "spiffe")
    @patch("request_manager.token_exchange.KEYCLOAK_URL", "http://keycloak:8080")
    @patch("request_manager.token_exchange.KEYCLOAK_REALM", "partner-agent")
    def test_returns_jwt_svid_in_spiffe_mode(self):
        """When SPIRE_AUTH_MODE=spiffe, fetch JWT-SVID and return it."""
        mock_spire = MagicMock()
        mock_spire.fetch_jwt_svid.return_value = "eyJhbGciOiJSUzI1NiJ9.svid-body"

        with patch(
            "shared_models.spire_client.get_spire_client", return_value=mock_spire
        ):
            result = _get_spiffe_client_assertion()

        assert result == "eyJhbGciOiJSUzI1NiJ9.svid-body"
        mock_spire.fetch_jwt_svid.assert_called_once_with(
            audience="http://keycloak:8080/realms/partner-agent"
        )

    @patch("request_manager.token_exchange.SPIRE_AUTH_MODE", "spiffe")
    def test_returns_none_on_spire_exception(self):
        """When SPIRE fetch fails, log and return None."""
        mock_spire = MagicMock()
        mock_spire.fetch_jwt_svid.side_effect = RuntimeError("SPIRE agent down")

        with patch(
            "shared_models.spire_client.get_spire_client", return_value=mock_spire
        ):
            result = _get_spiffe_client_assertion()

        assert result is None


# ---------------------------------------------------------------------------
# SPIFFE assertion branch in exchange_for_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("request_manager.token_exchange.AuditService")
@patch("request_manager.token_exchange.httpx.AsyncClient")
class TestSpiffeAssertionExchange:
    """Tests for SPIFFE client_assertion branch in exchange_for_agent (lines 323-326)."""

    @patch(
        "request_manager.token_exchange._get_spiffe_client_assertion",
        return_value="spiffe-jwt-svid-token",
    )
    @patch("request_manager.token_exchange._get_dcr_actor_token", new_callable=AsyncMock)
    async def test_spiffe_assertion_uses_client_assertion_omits_client_id(
        self, mock_dcr, mock_spiffe, mock_httpx, mock_audit
    ):
        """When SPIFFE assertion available, use client_assertion and omit client_id."""
        mock_dcr.return_value = None
        exchanged_token = _make_test_token(sub="user-42", aud="agent-service")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": exchanged_token,
            "token_type": "Bearer",
            "expires_in": 300,
        }

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client
        mock_audit.emit = AsyncMock()

        subject_token = _make_test_token(sub="user-42", aud="partner-agent-ui")
        client = TokenExchangeClient()
        result = await client.exchange_for_agent(
            subject_token=subject_token,
            target_agent="agent-service",
            actor_service="request-manager",
        )

        assert result["access_token"] == exchanged_token

        call_kwargs = mock_client.post.call_args
        posted_data = call_kwargs.kwargs.get("data") or call_kwargs[1].get("data")
        assert posted_data["client_assertion_type"] == (
            "urn:ietf:params:oauth:client-assertion-type:jwt-spiffe"
        )
        assert posted_data["client_assertion"] == "spiffe-jwt-svid-token"
        assert "client_id" not in posted_data
        assert posted_data["audience"] == "agent-service"

        await client.close()

    @patch(
        "request_manager.token_exchange._get_spiffe_client_assertion",
        return_value=None,
    )
    @patch("request_manager.token_exchange._get_dcr_actor_token", new_callable=AsyncMock)
    async def test_fallback_to_client_id_when_no_spiffe(
        self, mock_dcr, mock_spiffe, mock_httpx, mock_audit
    ):
        """When SPIFFE assertion is None, fall back to client_id + client_secret."""
        mock_dcr.return_value = None
        exchanged_token = _make_test_token(sub="user-42", aud="agent-service")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": exchanged_token,
            "token_type": "Bearer",
            "expires_in": 300,
        }

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client
        mock_audit.emit = AsyncMock()

        subject_token = _make_test_token(sub="user-42", aud="partner-agent-ui")
        client = TokenExchangeClient()
        result = await client.exchange_for_agent(
            subject_token=subject_token,
            target_agent="agent-service",
            actor_service="request-manager",
        )

        assert result["access_token"] == exchanged_token

        call_kwargs = mock_client.post.call_args
        posted_data = call_kwargs.kwargs.get("data") or call_kwargs[1].get("data")
        assert "client_id" in posted_data
        assert "client_assertion" not in posted_data

        await client.close()
