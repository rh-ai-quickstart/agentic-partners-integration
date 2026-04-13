"""Tests for request_manager.agent_client_enhanced."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from request_manager.agent_client_enhanced import EnhancedAgentClient


@pytest.mark.asyncio
class TestEnhancedAgentClient:
    """Tests for EnhancedAgentClient."""

    def _make_client(self, url: str = "http://agent:8080") -> EnhancedAgentClient:
        """Create a client with a mocked httpx.AsyncClient."""
        client = EnhancedAgentClient(agent_service_url=url, timeout=10.0)
        client.client = AsyncMock(spec=httpx.AsyncClient)
        return client

    # -- invoke_agent -------------------------------------------------------

    async def test_invoke_agent_posts_to_correct_url(self):
        """POST is sent to /api/v1/agents/<name>/invoke."""
        client = self._make_client("http://myagent:9090")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"content": "hello", "agent_id": "routing-agent"}
        mock_resp.raise_for_status = MagicMock()
        client.client.post = AsyncMock(return_value=mock_resp)

        await client.invoke_agent(
            agent_name="routing-agent",
            session_id="s1",
            user_id="u1",
            message="hi",
        )

        call_args = client.client.post.call_args
        assert call_args[0][0] == "http://myagent:9090/api/v1/agents/routing-agent/invoke"

    @patch("request_manager.agent_client_enhanced.CredentialService")
    async def test_invoke_agent_includes_auth_header(self, mock_cred):
        """When CredentialService has a token, include Authorization header."""
        mock_cred.get_auth_header.return_value = "Bearer tok123"

        client = self._make_client()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": "ok"}
        mock_resp.raise_for_status = MagicMock()
        client.client.post = AsyncMock(return_value=mock_resp)

        await client.invoke_agent(
            agent_name="test-agent",
            session_id="s1",
            user_id="u1",
            message="msg",
        )

        call_kwargs = client.client.post.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert headers.get("Authorization") == "Bearer tok123"

    @patch("request_manager.agent_client_enhanced.CredentialService")
    async def test_invoke_agent_no_auth_when_no_token(self, mock_cred):
        """When CredentialService has no token, Authorization header is absent."""
        mock_cred.get_auth_header.return_value = None

        client = self._make_client()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": "ok"}
        mock_resp.raise_for_status = MagicMock()
        client.client.post = AsyncMock(return_value=mock_resp)

        await client.invoke_agent(
            agent_name="test-agent",
            session_id="s1",
            user_id="u1",
            message="msg",
        )

        call_kwargs = client.client.post.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert "Authorization" not in headers

    @patch(
        "request_manager.agent_client_enhanced.STRUCTURED_CONTEXT_ENABLED",
        True,
    )
    @patch("request_manager.agent_client_enhanced.CredentialService")
    async def test_invoke_agent_adds_conversation_history_when_enabled(self, mock_cred):
        """When STRUCTURED_CONTEXT_ENABLED=True and history provided, add to transfer_context."""
        mock_cred.get_auth_header.return_value = None

        client = self._make_client()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": "ok"}
        mock_resp.raise_for_status = MagicMock()
        client.client.post = AsyncMock(return_value=mock_resp)

        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

        await client.invoke_agent(
            agent_name="test-agent",
            session_id="s1",
            user_id="u1",
            message="follow-up",
            conversation_history=history,
            previous_agent="routing-agent",
        )

        call_kwargs = client.client.post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json", {})
        tc = payload["transfer_context"]
        assert tc["conversation_history"] == history
        assert tc["previous_agent"] == "routing-agent"
        assert tc["current_agent"] == "test-agent"
        assert tc["enable_context_extraction"] is True

    @patch(
        "request_manager.agent_client_enhanced.STRUCTURED_CONTEXT_ENABLED",
        False,
    )
    @patch("request_manager.agent_client_enhanced.CredentialService")
    async def test_invoke_agent_skips_history_when_disabled(self, mock_cred):
        """When STRUCTURED_CONTEXT_ENABLED=False, conversation_history is NOT added."""
        mock_cred.get_auth_header.return_value = None

        client = self._make_client()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": "ok"}
        mock_resp.raise_for_status = MagicMock()
        client.client.post = AsyncMock(return_value=mock_resp)

        await client.invoke_agent(
            agent_name="test-agent",
            session_id="s1",
            user_id="u1",
            message="msg",
            conversation_history=[{"role": "user", "content": "old"}],
        )

        call_kwargs = client.client.post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json", {})
        tc = payload["transfer_context"]
        assert "conversation_history" not in tc

    @patch("request_manager.agent_client_enhanced.CredentialService")
    async def test_invoke_agent_raises_on_http_error(self, mock_cred):
        """HTTP errors from the agent service should propagate."""
        mock_cred.get_auth_header.return_value = None

        client = self._make_client()

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Internal", request=MagicMock(), response=MagicMock()
        )
        client.client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(httpx.HTTPStatusError):
            await client.invoke_agent(
                agent_name="bad-agent",
                session_id="s1",
                user_id="u1",
                message="oops",
            )

    # -- close / context manager --------------------------------------------

    async def test_close_closes_httpx_client(self):
        """close() should call aclose on the underlying httpx client."""
        client = self._make_client()
        client.client.aclose = AsyncMock()

        await client.close()

        client.client.aclose.assert_awaited_once()

    async def test_async_context_manager(self):
        """Client can be used as an async context manager."""
        client = self._make_client()
        client.client.aclose = AsyncMock()

        async with client as c:
            assert c is client

        client.client.aclose.assert_awaited_once()

    # -- URL trailing slash -------------------------------------------------

    def test_strips_trailing_slash_from_url(self):
        """Trailing slashes on agent_service_url should be stripped."""
        client = EnhancedAgentClient(agent_service_url="http://agent:8080/")
        assert client.agent_service_url == "http://agent:8080"

    # -- invoke_agent payload -----------------------------------------------

    @patch("request_manager.agent_client_enhanced.CredentialService")
    async def test_invoke_agent_payload_structure(self, mock_cred):
        """The POST payload should include session_id, user_id, message, transfer_context."""
        mock_cred.get_auth_header.return_value = None

        client = self._make_client()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": "result"}
        mock_resp.raise_for_status = MagicMock()
        client.client.post = AsyncMock(return_value=mock_resp)

        await client.invoke_agent(
            agent_name="agent-x",
            session_id="sess-42",
            user_id="bob@example.com",
            message="What is the status?",
            transfer_context={"key": "val"},
        )

        call_kwargs = client.client.post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json", {})
        assert payload["session_id"] == "sess-42"
        assert payload["user_id"] == "bob@example.com"
        assert payload["message"] == "What is the status?"
        assert payload["transfer_context"]["key"] == "val"
