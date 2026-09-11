"""Tests for the 3-tier agent registry discovery (Change 1 + Change 4).

Covers:
- Bug fix: _registry_fetched no longer permanently disabled on failure
- TTL-based module-level cache
- Tier-3 YAML registry fallback
- Tier-2 /.well-known/agent-card.json enrichment (when enabled)
- Graceful cascade (Tier 2 failures do not break Tier 3 results)
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

# Reset module-level cache between tests
import request_manager.communication_strategy as cs_module


@pytest.fixture(autouse=True)
def clear_registry_cache():
    """Wipe the module-level registry cache before each test."""
    cs_module._registry_cache.clear()
    yield
    cs_module._registry_cache.clear()


def make_strategy():
    from request_manager.communication_strategy import DirectHTTPStrategy
    return DirectHTTPStrategy()


# ── Tier-3: YAML registry ─────────────────────────────────────────────────────

class TestYamlRegistryTier3:
    @pytest.mark.asyncio
    async def test_remote_endpoints_loaded_from_yaml(self):
        """Remote agent endpoints from YAML registry are populated."""
        registry_response = {
            "agents": {
                "kubernetes-support": {
                    "endpoint": "http://k8s-agent:8080/api/v1/agents/kubernetes-support/invoke",
                    "departments": ["kubernetes"],
                },
                "software-support": {
                    # No endpoint → local agent, should be excluded
                    "departments": ["software"],
                },
            }
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = registry_response

        strategy = make_strategy()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await strategy._ensure_registry()

        assert "kubernetes-support" in strategy.agent_client.agent_endpoints
        assert "software-support" not in strategy.agent_client.agent_endpoints

    @pytest.mark.asyncio
    async def test_failure_does_not_permanently_disable_discovery(self):
        """BUG FIX: registry failure no longer sets _registry_fetched=True."""
        strategy = make_strategy()

        call_count = 0

        async def failing_then_succeeding(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("connection refused")
            # Second call succeeds
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {
                "agents": {
                    "kubernetes-support": {
                        "endpoint": "http://k8s:8080/api/v1/agents/kubernetes-support/invoke"
                    }
                }
            }
            return mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = failing_then_succeeding
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            # First call — fails, but should NOT permanently disable discovery
            await strategy._ensure_registry()
            assert "kubernetes-support" not in strategy.agent_client.agent_endpoints

            # Cache miss (failure not cached) — second call should succeed
            await strategy._ensure_registry()

        # After second (successful) call, endpoint should be present
        assert "kubernetes-support" in strategy.agent_client.agent_endpoints

    @pytest.mark.asyncio
    async def test_successful_result_is_cached(self):
        """Successful registry fetch is cached and reused within TTL."""
        registry_response = {
            "agents": {
                "network-support": {
                    "endpoint": "http://net:8080/api/v1/agents/network-support/invoke"
                }
            }
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = registry_response

        strategy = make_strategy()
        fetch_count = 0

        async def count_fetches(*args, **kwargs):
            nonlocal fetch_count
            fetch_count += 1
            return mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = count_fetches
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await strategy._ensure_registry()
            await strategy._ensure_registry()  # should use cache
            await strategy._ensure_registry()  # should use cache

        assert fetch_count == 1  # only one real HTTP call

    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl(self):
        """Cache entry is invalidated after REGISTRY_TTL_SECONDS."""
        registry_response = {
            "agents": {
                "software-support": {
                    "endpoint": "http://sw:8080/api/v1/agents/software-support/invoke"
                }
            }
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = registry_response

        strategy = make_strategy()
        fetch_count = 0

        async def count_fetches(*args, **kwargs):
            nonlocal fetch_count
            fetch_count += 1
            return mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = count_fetches
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            # First call — populates cache
            await strategy._ensure_registry()

            # Manually expire the cache entry
            agent_svc_url = strategy._agent_service_url
            endpoints, _ = cs_module._registry_cache[agent_svc_url]
            stale_time = datetime.now(timezone.utc) - timedelta(seconds=cs_module._REGISTRY_TTL_SECONDS + 1)
            cs_module._registry_cache[agent_svc_url] = (endpoints, stale_time)

            # Second call — cache stale, should re-fetch
            await strategy._ensure_registry()

        assert fetch_count == 2


# ── Tier-2: /.well-known enrichment ──────────────────────────────────────────

class TestWellKnownTier2:
    @pytest.mark.asyncio
    async def test_card_url_enriches_invoke_endpoint(self, monkeypatch):
        """Tier 2 replaces the invoke URL with the one from the agent card."""
        monkeypatch.setenv("AGENT_CARD_DISCOVERY", "true")

        # Tier-3 returns a base endpoint
        yaml_registry = {
            "agents": {
                "kubernetes-support": {
                    "endpoint": "http://k8s-old:9000/api/v1/agents/kubernetes-support/invoke"
                }
            }
        }
        yaml_resp = MagicMock()
        yaml_resp.status_code = 200
        yaml_resp.raise_for_status = MagicMock()
        yaml_resp.json.return_value = yaml_registry

        # Tier-2 card returns an authoritative URL
        card_resp = MagicMock()
        card_resp.status_code = 200
        card_resp.json.return_value = {
            "url": "http://k8s-new:8080/a2a/kubernetes-support/",
            "name": "Kubernetes Support Agent",
        }

        strategy = make_strategy()
        call_urls = []

        async def mock_get(url, **kwargs):
            call_urls.append(url)
            if "well-known" in url:
                return card_resp
            return yaml_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await strategy._ensure_registry()

        assert "kubernetes-support" in strategy.agent_client.agent_endpoints
        # The card URL should override the YAML URL
        assert strategy.agent_client.agent_endpoints["kubernetes-support"] == \
            "http://k8s-new:8080/a2a/kubernetes-support/"

        monkeypatch.delenv("AGENT_CARD_DISCOVERY")

    @pytest.mark.asyncio
    async def test_card_failure_falls_back_to_yaml_url(self, monkeypatch):
        """Card fetch failure leaves Tier-3 YAML endpoint intact."""
        monkeypatch.setenv("AGENT_CARD_DISCOVERY", "true")

        yaml_registry = {
            "agents": {
                "software-support": {
                    "endpoint": "http://sw:8080/api/v1/agents/software-support/invoke"
                }
            }
        }
        yaml_resp = MagicMock()
        yaml_resp.status_code = 200
        yaml_resp.raise_for_status = MagicMock()
        yaml_resp.json.return_value = yaml_registry

        strategy = make_strategy()

        async def mock_get(url, **kwargs):
            if "well-known" in url:
                raise httpx.ConnectError("agent card not reachable")
            return yaml_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await strategy._ensure_registry()

        # Tier-3 YAML result survives even when Tier-2 card is unreachable
        assert strategy.agent_client.agent_endpoints.get("software-support") == \
            "http://sw:8080/api/v1/agents/software-support/invoke"

        monkeypatch.delenv("AGENT_CARD_DISCOVERY")

    @pytest.mark.asyncio
    async def test_card_discovery_disabled_by_default(self):
        """Tier-2 card discovery is OFF unless AGENT_CARD_DISCOVERY=true."""
        yaml_registry = {
            "agents": {
                "network-support": {
                    "endpoint": "http://net:8080/api/v1/agents/network-support/invoke"
                }
            }
        }
        yaml_resp = MagicMock()
        yaml_resp.status_code = 200
        yaml_resp.raise_for_status = MagicMock()
        yaml_resp.json.return_value = yaml_registry

        card_fetch_called = False

        async def mock_get(url, **kwargs):
            nonlocal card_fetch_called
            if "well-known" in url:
                card_fetch_called = True
            return yaml_resp

        strategy = make_strategy()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await strategy._ensure_registry()

        assert not card_fetch_called
