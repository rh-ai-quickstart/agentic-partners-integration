"""Tests for token_exchange.py audit event emission."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from request_manager.token_exchange import TokenExchangeClient, TokenExchangeError


@pytest.mark.asyncio
class TestTokenExchangeAudit:
    """Tests for audit event emission in TokenExchangeClient."""

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_successful_exchange_emits_audit_event(self, mock_httpx, mock_audit):
        """Successful token exchange emits success audit event."""
        # Mock JWT token
        test_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJhdWQiOiJwYXJ0bmVyLWFnZW50LXVpIiwiZW1haWwiOiJ0ZXN0QGV4YW1wbGUuY29tIiwiYWN0Ijp7InN1YiI6InJlcXVlc3QtbWFuYWdlciJ9fQ.test"

        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new_token_123",
            "token_type": "Bearer",
            "expires_in": 300,
        }

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        # Mock audit service
        mock_audit.emit = AsyncMock()

        # Create client and exchange token
        client = TokenExchangeClient()
        result = await client.exchange_for_agent(
            subject_token=test_token,
            target_agent="kubernetes-agent",
            actor_service="request-manager"
        )

        # Verify result
        assert result["access_token"] == "new_token_123"

        # Verify audit event was emitted
        mock_audit.emit.assert_called_once()
        call_args = mock_audit.emit.call_args[1]

        assert call_args["event_type"] == "token.exchange"
        assert call_args["actor"] == "request-manager"
        assert call_args["action"] == "exchange_token"
        assert call_args["resource"] == "kubernetes-agent"
        assert call_args["outcome"] == "success"
        assert call_args["service"] == "request-manager"

        # Verify metadata contains required fields
        metadata = call_args["metadata"]
        assert "original_aud" in metadata
        assert metadata["new_aud"] == "kubernetes-agent"
        assert metadata["target_agent"] == "kubernetes-agent"
        assert metadata["actor_service"] == "request-manager"
        assert "act_claim" in metadata

        await client.close()

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_failed_exchange_emits_audit_event(self, mock_httpx, mock_audit):
        """Failed token exchange emits failure audit event."""
        # Mock JWT token
        test_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJhdWQiOiJwYXJ0bmVyLWFnZW50LXVpIiwiZW1haWwiOiJ0ZXN0QGV4YW1wbGUuY29tIn0.test"

        # Mock HTTP error response
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "error": "invalid_token",
            "error_description": "Token is invalid or expired"
        }
        mock_response.text = "Token is invalid or expired"

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        # Mock audit service
        mock_audit.emit = AsyncMock()

        # Create client and attempt exchange
        client = TokenExchangeClient()

        with pytest.raises(TokenExchangeError) as exc_info:
            await client.exchange_for_agent(
                subject_token=test_token,
                target_agent="kubernetes-agent",
                actor_service="request-manager"
            )

        # Verify exception
        assert exc_info.value.status_code == 401

        # Verify audit event was emitted
        mock_audit.emit.assert_called_once()
        call_args = mock_audit.emit.call_args[1]

        assert call_args["event_type"] == "token.exchange"
        assert call_args["actor"] == "request-manager"
        assert call_args["action"] == "exchange_token"
        assert call_args["resource"] == "kubernetes-agent"
        assert call_args["outcome"] == "failure"
        assert "HTTP 401" in call_args["reason"]
        assert call_args["service"] == "request-manager"

        # Verify metadata contains error details
        metadata = call_args["metadata"]
        assert metadata["status_code"] == 401
        assert "error_detail" in metadata
        assert metadata["target_agent"] == "kubernetes-agent"

        await client.close()

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_timeout_emits_audit_event(self, mock_httpx, mock_audit):
        """Timeout during token exchange emits failure audit event."""
        import httpx

        # Mock JWT token
        test_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJhdWQiOiJwYXJ0bmVyLWFnZW50LXVpIn0.test"

        # Mock timeout exception
        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Request timeout"))
        mock_client.timeout = MagicMock(read=30.0)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        # Mock audit service
        mock_audit.emit = AsyncMock()

        # Create client and attempt exchange
        client = TokenExchangeClient()

        with pytest.raises(httpx.TimeoutException):
            await client.exchange_for_agent(
                subject_token=test_token,
                target_agent="kubernetes-agent",
                actor_service="request-manager"
            )

        # Verify audit event was emitted
        mock_audit.emit.assert_called_once()
        call_args = mock_audit.emit.call_args[1]

        assert call_args["event_type"] == "token.exchange"
        assert call_args["outcome"] == "failure"
        assert "timeout" in call_args["reason"].lower()

        # Verify metadata contains timeout info
        metadata = call_args["metadata"]
        assert metadata["timeout"] == 30.0

        await client.close()

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_audit_failure_does_not_crash_exchange(self, mock_httpx, mock_audit):
        """Failed audit emission doesn't crash the exchange operation."""
        # Mock JWT token
        test_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJhdWQiOiJwYXJ0bmVyLWFnZW50LXVpIn0.test"

        # Mock successful HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new_token_123",
            "token_type": "Bearer",
            "expires_in": 300,
        }

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        # Mock audit service to raise exception
        mock_audit.emit = AsyncMock(side_effect=Exception("Database connection lost"))

        # Create client and exchange token
        client = TokenExchangeClient()

        # Should complete successfully despite audit failure
        result = await client.exchange_for_agent(
            subject_token=test_token,
            target_agent="kubernetes-agent",
            actor_service="request-manager"
        )

        # Verify result is returned despite audit failure
        assert result["access_token"] == "new_token_123"

        # Verify audit was attempted
        mock_audit.emit.assert_called_once()

        await client.close()
