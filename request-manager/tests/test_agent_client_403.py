"""Tests for request_manager.agent_client_enhanced — 403 and error handling."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from request_manager.agent_client_enhanced import EnhancedAgentClient


class TestAgentClient403Handling:
    """Tests for graceful 403 denial handling."""

    def _make_client(self) -> EnhancedAgentClient:
        client = EnhancedAgentClient(agent_service_url="http://agent:8080", timeout=10.0)
        client.client = AsyncMock(spec=httpx.AsyncClient)
        return client

    @patch("request_manager.agent_client_enhanced.CredentialService")
    async def test_403_returns_access_denied_message(self, mock_cred):
        """403 from agent-service returns a user-friendly denial message."""
        mock_cred.get_auth_header.return_value = None

        client = self._make_client()

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.json.return_value = {"detail": "User lacks software department"}
        mock_response.headers = {"content-type": "application/json"}

        error = httpx.HTTPStatusError(
            "403 Forbidden",
            request=MagicMock(),
            response=mock_response,
        )
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = error
        client.client.post = AsyncMock(return_value=mock_resp)

        result = await client.invoke_agent(
            agent_name="software-support",
            session_id="s1",
            user_id="user@example.com",
            message="help",
        )

        assert "Access denied" in result["content"]
        assert "Software Support" in result["content"]
        assert result["agent_id"] == "routing-agent"
        assert result["metadata"]["blocked_agent"] == "software-support"

    @patch("request_manager.agent_client_enhanced.CredentialService")
    async def test_403_non_json_response(self, mock_cred):
        """403 with non-JSON body still returns graceful message."""
        mock_cred.get_auth_header.return_value = None

        client = self._make_client()

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {"content-type": "text/plain"}

        error = httpx.HTTPStatusError(
            "403 Forbidden",
            request=MagicMock(),
            response=mock_response,
        )
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = error
        client.client.post = AsyncMock(return_value=mock_resp)

        result = await client.invoke_agent(
            agent_name="network-support",
            session_id="s1",
            user_id="user@example.com",
            message="vpn issue",
        )

        assert "Access denied" in result["content"]

    @patch("request_manager.agent_client_enhanced.CredentialService")
    async def test_non_403_http_error_raises(self, mock_cred):
        """Non-403 HTTP errors (e.g. 500) should propagate."""
        mock_cred.get_auth_header.return_value = None

        client = self._make_client()

        mock_response = MagicMock()
        mock_response.status_code = 500

        error = httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=MagicMock(),
            response=mock_response,
        )
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = error
        client.client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(httpx.HTTPStatusError):
            await client.invoke_agent(
                agent_name="test-agent",
                session_id="s1",
                user_id="user@example.com",
                message="help",
            )

    @patch("request_manager.agent_client_enhanced.CredentialService")
    async def test_generic_http_error_raises(self, mock_cred):
        """Generic httpx.HTTPError (connection error, timeout) propagates."""
        mock_cred.get_auth_header.return_value = None

        client = self._make_client()

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.ConnectError("Connection refused")
        client.client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(httpx.ConnectError):
            await client.invoke_agent(
                agent_name="test-agent",
                session_id="s1",
                user_id="user@example.com",
                message="help",
            )
