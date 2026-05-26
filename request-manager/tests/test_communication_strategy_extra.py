"""Additional tests for request_manager.communication_strategy — registry and edge cases."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from request_manager.communication_strategy import (
    DirectHTTPStrategy,
    UnifiedRequestProcessor,
)


@pytest.mark.asyncio
class TestDirectHTTPStrategyEnsureRegistry:
    """Tests for the _ensure_registry method."""

    @patch("request_manager.communication_strategy.httpx.AsyncClient")
    async def test_registry_all_local_agents(self, mock_httpx):
        """When registry has no remote endpoints, logs 'all agents local'."""
        strategy = DirectHTTPStrategy()
        strategy._registry_fetched = False

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "agents": {
                "software-support": {"endpoint": None},
                "network-support": {},
            }
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_httpx.return_value = mock_client

        await strategy._ensure_registry()
        assert strategy._registry_fetched is True

    @patch("request_manager.communication_strategy.httpx.AsyncClient")
    async def test_registry_with_remote_agents(self, mock_httpx):
        """When registry has remote endpoints, populates agent_endpoints."""
        strategy = DirectHTTPStrategy()
        strategy._registry_fetched = False

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "agents": {
                "kubernetes-support": {
                    "endpoint": "http://k8s-agent:8080/api/v1/agents/kubernetes-support/invoke",
                },
                "software-support": {"endpoint": None},
            }
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_httpx.return_value = mock_client

        await strategy._ensure_registry()
        assert "kubernetes-support" in strategy.agent_client.agent_endpoints

    @patch("request_manager.communication_strategy.httpx.AsyncClient")
    async def test_registry_fetch_failure(self, mock_httpx):
        """When registry is unreachable, falls back gracefully."""
        import httpx as real_httpx

        strategy = DirectHTTPStrategy()
        strategy._registry_fetched = False

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=real_httpx.ConnectError("Connection refused")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_httpx.return_value = mock_client

        await strategy._ensure_registry()
        assert strategy._registry_fetched is True

    async def test_registry_cached_after_first_call(self):
        """Second call to _ensure_registry does nothing (cached)."""
        strategy = DirectHTTPStrategy()
        strategy._registry_fetched = True

        with patch("request_manager.communication_strategy.httpx.AsyncClient") as mock_httpx:
            mock_client = AsyncMock()
            mock_httpx.return_value = mock_client

            await strategy._ensure_registry()

            mock_client.get.assert_not_called()
