"""Tests for request_manager.communication_strategy."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from request_manager.communication_strategy import (
    DirectHTTPStrategy,
    UnifiedRequestProcessor,
    get_communication_strategy,
)

# ---------------------------------------------------------------------------
# get_communication_strategy
# ---------------------------------------------------------------------------


class TestGetCommunicationStrategy:
    """Tests for the factory function."""

    def test_returns_direct_http_strategy(self):
        """Factory always returns DirectHTTPStrategy."""
        strategy = get_communication_strategy()
        assert isinstance(strategy, DirectHTTPStrategy)


# ---------------------------------------------------------------------------
# DirectHTTPStrategy.__init__
# ---------------------------------------------------------------------------


class TestDirectHTTPStrategyInit:
    """Tests for DirectHTTPStrategy construction."""

    def test_reads_env_vars(self, monkeypatch):
        """Constructor uses AGENT_SERVICE_URL and AGENT_TIMEOUT env vars."""
        monkeypatch.setenv("AGENT_SERVICE_URL", "http://custom-agent:9090")
        monkeypatch.setenv("AGENT_TIMEOUT", "60")

        strategy = DirectHTTPStrategy()

        assert strategy.agent_client.agent_service_url == "http://custom-agent:9090"

    def test_defaults(self):
        """Without env vars, uses default values."""
        strategy = DirectHTTPStrategy()
        assert strategy.agent_client is not None


# ---------------------------------------------------------------------------
# DirectHTTPStrategy._ensure_registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEnsureRegistry:
    """Tests for _ensure_registry (agent endpoint discovery)."""

    async def test_fetches_registry_and_populates_endpoints(self):
        """On first call, fetches registry and sets agent_endpoints."""
        strategy = DirectHTTPStrategy()

        registry_data = {
            "agents": {
                "software-support": {
                    "endpoint": "http://agent-service:8080/api/v1/agents/software-support/invoke",
                    "departments": ["software"],
                    "description": "SW",
                },
                "db-support": {
                    "endpoint": "http://db-agent:9090/api/v1/agents/db-support/invoke",
                    "departments": ["database"],
                    "description": "DB",
                },
            }
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = registry_data
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "request_manager.communication_strategy.httpx.AsyncClient"
        ) as mock_httpx:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_resp)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value = mock_instance

            await strategy._ensure_registry()

        assert strategy.agent_client.agent_endpoints["db-support"] == (
            "http://db-agent:9090/api/v1/agents/db-support/invoke"
        )
        assert strategy._registry_fetched is True

    async def test_only_fetches_once(self):
        """Second call is a no-op (cached)."""
        strategy = DirectHTTPStrategy()
        strategy._registry_fetched = True

        with patch(
            "request_manager.communication_strategy.httpx.AsyncClient"
        ) as mock_httpx:
            await strategy._ensure_registry()
            mock_httpx.assert_not_called()

    async def test_handles_registry_failure_gracefully(self):
        """If registry fetch fails, logs warning but does not raise."""
        strategy = DirectHTTPStrategy()

        with patch(
            "request_manager.communication_strategy.httpx.AsyncClient"
        ) as mock_httpx:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(side_effect=Exception("Connection refused"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value = mock_instance

            # Should not raise
            await strategy._ensure_registry()

        assert strategy._registry_fetched is True
        assert strategy.agent_client.agent_endpoints == {}


# ---------------------------------------------------------------------------
# DirectHTTPStrategy.invoke_agent_with_routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestInvokeAgentWithRouting:
    """Tests for invoke_agent_with_routing."""

    async def _make_strategy_with_mock_client(self):
        """Create a DirectHTTPStrategy with a mocked agent_client."""
        strategy = DirectHTTPStrategy()
        strategy.agent_client = AsyncMock()
        strategy._registry_fetched = True  # Skip registry fetch in tests
        return strategy

    async def test_routes_through_routing_agent(self):
        """When routing-agent returns a routing_decision, follow the chain."""
        from dataclasses import dataclass, field

        @dataclass
        class FakeOPADecision:
            allow: bool = True
            reason: str = "ok"
            effective_departments: list = field(default_factory=lambda: ["engineering"])

        strategy = await self._make_strategy_with_mock_client()

        # First call: routing-agent returns a routing decision
        routing_response = {
            "content": "",
            "agent_id": "routing-agent",
            "routing_decision": "software-support",
            "metadata": {"handling_agent": "routing-agent"},
        }
        # Second call: specialist returns final response
        specialist_response = {
            "content": "Here is your answer.",
            "agent_id": "software-support",
            "routing_decision": None,
            "metadata": {"handling_agent": "software-support"},
        }
        strategy.agent_client.invoke_agent = AsyncMock(
            side_effect=[routing_response, specialist_response]
        )

        # Build normalized request
        normalized = MagicMock()
        normalized.request_id = "req-1"
        normalized.session_id = "sess-1"
        normalized.user_id = "user@example.com"
        normalized.content = "Help me with software"
        normalized.user_context = {
            "user_context": {
                "departments": ["engineering"],
                "spiffe_id": "spiffe://test/user/user",
                "email": "user@example.com",
            }
        }

        db = AsyncMock()

        # Mock _get_conversation_history and OPA
        with (
            patch.object(
                strategy,
                "_get_conversation_history",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "shared_models.opa_client.check_agent_authorization",
                new_callable=AsyncMock,
                return_value=FakeOPADecision(),
            ),
            patch(
                "request_manager.communication_strategy.make_spiffe_id",
                return_value="spiffe://test/service/request-manager",
            ),
        ):
            result = await strategy.invoke_agent_with_routing(normalized, db)

        assert result["content"] == "Here is your answer."
        assert result["agent_id"] == "software-support"

    async def test_opa_authorization_denial(self):
        """When OPA denies routing, return an access-denied message."""
        from dataclasses import dataclass, field

        @dataclass
        class FakeOPADecision:
            allow: bool = False
            reason: str = "department mismatch"
            effective_departments: list = field(default_factory=list)

        strategy = await self._make_strategy_with_mock_client()

        routing_response = {
            "content": "",
            "agent_id": "routing-agent",
            "routing_decision": "network-support",
            "metadata": {},
        }
        strategy.agent_client.invoke_agent = AsyncMock(return_value=routing_response)

        normalized = MagicMock()
        normalized.request_id = "req-2"
        normalized.session_id = "sess-2"
        normalized.user_id = "user@example.com"
        normalized.content = "network issue"
        normalized.user_context = {
            "user_context": {
                "departments": ["software"],
                "spiffe_id": "",
                "email": "user@example.com",
            }
        }

        db = AsyncMock()

        with (
            patch.object(
                strategy,
                "_get_conversation_history",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "shared_models.opa_client.check_agent_authorization",
                new_callable=AsyncMock,
                return_value=FakeOPADecision(),
            ),
            patch(
                "request_manager.communication_strategy.make_spiffe_id",
                return_value="spiffe://test/service/request-manager",
            ),
        ):
            result = await strategy.invoke_agent_with_routing(normalized, db)

        assert (
            "access" in result["content"].lower()
            or "does not have" in result["content"].lower()
        )
        assert result["metadata"]["blocked_agent"] == "network-support"

    async def test_direct_response_without_routing(self):
        """When agent returns no routing_decision, return the response directly."""
        strategy = await self._make_strategy_with_mock_client()

        direct_response = {
            "content": "Direct answer.",
            "agent_id": "routing-agent",
            "routing_decision": None,
            "metadata": {"handling_agent": "routing-agent"},
        }
        strategy.agent_client.invoke_agent = AsyncMock(return_value=direct_response)

        normalized = MagicMock()
        normalized.request_id = "req-3"
        normalized.session_id = "sess-3"
        normalized.user_id = "user@example.com"
        normalized.content = "Hello!"
        normalized.user_context = {
            "user_context": {"departments": [], "email": "user@example.com"}
        }

        db = AsyncMock()

        with patch.object(
            strategy,
            "_get_conversation_history",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await strategy.invoke_agent_with_routing(normalized, db)

        assert result["content"] == "Direct answer."
        assert result["agent_id"] == "routing-agent"


# ---------------------------------------------------------------------------
# DirectHTTPStrategy._get_conversation_history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGetConversationHistory:
    """Tests for _get_conversation_history."""

    async def test_retrieves_messages_from_session(self):
        """Should return messages from session's conversation_context."""
        strategy = DirectHTTPStrategy()

        mock_session = MagicMock()
        mock_session.conversation_context = {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ]
        }

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = mock_session

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        messages = await strategy._get_conversation_history("sess-1", db)

        assert len(messages) == 2
        assert messages[0]["role"] == "user"

    async def test_returns_empty_when_no_session(self):
        """When session is not found, return empty list."""
        strategy = DirectHTTPStrategy()

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        messages = await strategy._get_conversation_history("no-such-sess", db)

        assert messages == []

    async def test_returns_empty_when_no_messages(self):
        """When session has no messages key, return empty list."""
        strategy = DirectHTTPStrategy()

        mock_session = MagicMock()
        mock_session.conversation_context = {}

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = mock_session

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        messages = await strategy._get_conversation_history("sess-empty", db)

        assert messages == []

    async def test_truncates_to_40_entries(self):
        """When more than 40 messages exist, only last 40 are returned."""
        strategy = DirectHTTPStrategy()

        # Create 50 messages
        all_messages = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg-{i}"}
            for i in range(50)
        ]
        mock_session = MagicMock()
        mock_session.conversation_context = {"messages": all_messages}

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = mock_session

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        messages = await strategy._get_conversation_history("sess-big", db)

        assert len(messages) == 40

    async def test_handles_exception_gracefully(self):
        """On error, return empty list instead of raising."""
        strategy = DirectHTTPStrategy()

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=RuntimeError("DB error"))

        messages = await strategy._get_conversation_history("sess-err", db)

        assert messages == []


# ---------------------------------------------------------------------------
# create_or_get_session_shared
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCreateOrGetSessionShared:
    """Tests for create_or_get_session_shared."""

    async def test_creates_new_session(self):
        """When no existing session, creates a new one."""
        from request_manager.communication_strategy import create_or_get_session_shared

        # No existing sessions found
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []

        # Session re-fetch after expiry update
        refetch_result = MagicMock()
        refetch_session = MagicMock()
        refetch_session.session_id = "new-sess-id"
        refetch_result.scalar_one_or_none.return_value = refetch_session

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[result_mock, AsyncMock(), refetch_result])

        mock_session_response = MagicMock()
        mock_session_response.session_id = "new-sess-id"

        request = MagicMock()
        request.user_id = "user@example.com"
        request.integration_type = "WEB"
        request.metadata = {}
        request.channel_id = None
        request.thread_id = None

        with (
            patch(
                "shared_models.resolve_canonical_user_id",
                new_callable=AsyncMock,
                return_value="canonical-uid",
            ),
            patch(
                "shared_models.BaseSessionManager",
            ) as mock_bsm,
            patch(
                "shared_models.SessionResponse",
            ) as mock_sr,
        ):
            mock_bsm_instance = AsyncMock()
            mock_bsm_instance.create_session = AsyncMock(
                return_value=mock_session_response
            )
            mock_bsm.return_value = mock_bsm_instance
            mock_sr.model_validate.return_value = MagicMock(session_id="new-sess-id")

            result = await create_or_get_session_shared(request, db)

        assert result is not None

    async def test_reuses_existing_session(self):
        """When an existing active session is found, reuse it."""
        from request_manager.communication_strategy import create_or_get_session_shared

        existing_session = MagicMock()
        existing_session.session_id = "existing-sess"
        existing_session.last_request_at = None
        existing_session.expires_at = None
        existing_session.integration_type = "WEB"
        existing_session.current_agent_id = "routing-agent"

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [existing_session]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        request = MagicMock()
        request.user_id = "user@example.com"
        request.integration_type = "WEB"
        request.metadata = {}

        with (
            patch(
                "shared_models.resolve_canonical_user_id",
                new_callable=AsyncMock,
                return_value="canonical-uid",
            ),
            patch(
                "shared_models.SessionResponse",
            ) as mock_sr,
        ):
            mock_sr.model_validate.return_value = MagicMock(session_id="existing-sess")
            result = await create_or_get_session_shared(request, db)

        assert result.session_id == "existing-sess"


# ---------------------------------------------------------------------------
# UnifiedRequestProcessor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestUnifiedRequestProcessor:
    """Tests for UnifiedRequestProcessor.process_request_sync."""

    async def test_process_request_sync_full_flow(self):
        """process_request_sync orchestrates session, normalize, invoke, log."""
        # Setup strategy mock
        strategy = AsyncMock(spec=DirectHTTPStrategy)

        # Mock session creation
        mock_session = MagicMock()
        mock_session.session_id = "sess-100"
        mock_session.current_agent_id = "routing-agent"
        strategy.create_or_get_session = AsyncMock(return_value=mock_session)

        # Mock agent invocation
        strategy.invoke_agent_with_routing = AsyncMock(
            return_value={
                "content": "Agent reply",
                "agent_id": "routing-agent",
                "metadata": {},
                "session_id": "sess-100",
            }
        )

        processor = UnifiedRequestProcessor(strategy)

        request = MagicMock()
        request.user_id = "user@example.com"
        request.metadata = {}

        db = MagicMock()
        db.commit = AsyncMock()

        # Mock the user lookup (for email replacement)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        # Mock normalizer
        mock_normalized = MagicMock()
        mock_normalized.request_id = "req-100"
        mock_normalized.session_id = "sess-100"
        mock_normalized.user_id = "user@example.com"
        mock_normalized.content = "test message"
        mock_normalized.request_type = "message"
        mock_normalized.integration_type = "WEB"
        mock_normalized.integration_context = {}
        mock_normalized.target_agent_id = None

        with (
            patch(
                "request_manager.communication_strategy.RequestNormalizer"
            ) as mock_normalizer_cls,
            patch(
                "request_manager.communication_strategy.get_pod_name",
                return_value=None,
            ),
            patch(
                "shared_models.models.RequestLog",
            ),
        ):
            mock_normalizer_cls.return_value.normalize_request.return_value = (
                mock_normalized
            )

            result = await processor.process_request_sync(request, db)

        assert result["content"] == "Agent reply"
        assert "processing_time_ms" in result

    async def test_process_request_sync_no_session_raises(self):
        """When session creation fails, should raise HTTPException."""
        from fastapi import HTTPException

        strategy = AsyncMock(spec=DirectHTTPStrategy)
        strategy.create_or_get_session = AsyncMock(return_value=None)

        processor = UnifiedRequestProcessor(strategy)

        request = MagicMock()
        request.user_id = "user@example.com"

        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await processor.process_request_sync(request, db)
        assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# Helper functions: _should_filter_sessions_by_integration_type,
#                   _get_session_timeout_hours, get_pod_name
# ---------------------------------------------------------------------------


class TestShouldFilterSessionsByIntegrationType:
    """Tests for _should_filter_sessions_by_integration_type."""

    def test_default_returns_false(self, monkeypatch):
        """Default (env var not set) should return False."""
        from request_manager.communication_strategy import (
            _should_filter_sessions_by_integration_type,
        )

        monkeypatch.delenv("SESSION_PER_INTEGRATION_TYPE", raising=False)
        assert _should_filter_sessions_by_integration_type() is False

    def test_returns_true_when_set(self, monkeypatch):
        """When env var is 'true', returns True."""
        from request_manager.communication_strategy import (
            _should_filter_sessions_by_integration_type,
        )

        monkeypatch.setenv("SESSION_PER_INTEGRATION_TYPE", "true")
        assert _should_filter_sessions_by_integration_type() is True

    def test_returns_true_case_insensitive(self, monkeypatch):
        """Case-insensitive comparison for 'TRUE'."""
        from request_manager.communication_strategy import (
            _should_filter_sessions_by_integration_type,
        )

        monkeypatch.setenv("SESSION_PER_INTEGRATION_TYPE", "TRUE")
        assert _should_filter_sessions_by_integration_type() is True

    def test_returns_false_for_other_values(self, monkeypatch):
        """Any value other than 'true' returns False."""
        from request_manager.communication_strategy import (
            _should_filter_sessions_by_integration_type,
        )

        monkeypatch.setenv("SESSION_PER_INTEGRATION_TYPE", "yes")
        assert _should_filter_sessions_by_integration_type() is False


class TestGetSessionTimeoutHours:
    """Tests for _get_session_timeout_hours."""

    def test_default_value(self, monkeypatch):
        """Default timeout is 336 hours (2 weeks)."""
        from request_manager.communication_strategy import _get_session_timeout_hours

        monkeypatch.delenv("SESSION_TIMEOUT_HOURS", raising=False)
        assert _get_session_timeout_hours() == 336

    def test_custom_value(self, monkeypatch):
        """Custom timeout from env var."""
        from request_manager.communication_strategy import _get_session_timeout_hours

        monkeypatch.setenv("SESSION_TIMEOUT_HOURS", "24")
        assert _get_session_timeout_hours() == 24


class TestGetPodName:
    """Tests for get_pod_name."""

    def test_returns_hostname(self, monkeypatch):
        """Returns HOSTNAME env var when set."""
        from request_manager.communication_strategy import get_pod_name

        monkeypatch.setenv("HOSTNAME", "pod-abc-123")
        monkeypatch.delenv("POD_NAME", raising=False)
        assert get_pod_name() == "pod-abc-123"

    def test_returns_pod_name_as_fallback(self, monkeypatch):
        """Returns POD_NAME when HOSTNAME is not set."""
        from request_manager.communication_strategy import get_pod_name

        monkeypatch.delenv("HOSTNAME", raising=False)
        monkeypatch.setenv("POD_NAME", "pod-xyz-789")
        assert get_pod_name() == "pod-xyz-789"

    def test_returns_none_when_neither_set(self, monkeypatch):
        """Returns None when neither HOSTNAME nor POD_NAME is set."""
        from request_manager.communication_strategy import get_pod_name

        monkeypatch.delenv("HOSTNAME", raising=False)
        monkeypatch.delenv("POD_NAME", raising=False)
        assert get_pod_name() is None


# ---------------------------------------------------------------------------
# DirectHTTPStrategy.send_request and wait_for_response (stub methods)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDirectHTTPStrategyStubs:
    """Tests for the stub methods send_request and wait_for_response."""

    async def test_send_request_returns_true(self):
        """send_request always returns True (stub for interface compatibility)."""
        strategy = DirectHTTPStrategy()
        normalized = MagicMock()
        normalized.request_id = "req-stub"
        result = await strategy.send_request(normalized)
        assert result is True

    async def test_wait_for_response_returns_empty_dict(self):
        """wait_for_response always returns empty dict (unused in sync flow)."""
        strategy = DirectHTTPStrategy()
        result = await strategy.wait_for_response("req-stub", timeout=30)
        assert result == {}

    async def test_wait_for_response_with_db(self):
        """wait_for_response accepts optional db parameter."""
        strategy = DirectHTTPStrategy()
        db = AsyncMock()
        result = await strategy.wait_for_response("req-stub", timeout=30, db=db)
        assert result == {}


# ---------------------------------------------------------------------------
# create_or_get_session_shared - additional coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCreateOrGetSessionSharedExtended:
    """Additional tests for create_or_get_session_shared to cover missing lines."""

    async def test_provided_session_id_found_and_reused(self):
        """When session_id is provided in metadata and found, reuse it (lines 78-104)."""
        from datetime import datetime, timedelta, timezone

        from request_manager.communication_strategy import create_or_get_session_shared

        # Create a mock session that has not expired
        provided_session = MagicMock()
        provided_session.session_id = "provided-sess-id"
        provided_session.expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        provided_session.last_request_at = None

        # First execute: lookup by provided session_id
        provided_result = MagicMock()
        provided_result.scalar_one_or_none.return_value = provided_session

        db = AsyncMock()
        db.execute = AsyncMock(return_value=provided_result)
        db.commit = AsyncMock()

        request = MagicMock()
        request.user_id = "user@example.com"
        request.integration_type = "WEB"
        request.metadata = {"session_id": "provided-sess-id"}

        with (
            patch(
                "shared_models.resolve_canonical_user_id",
                new_callable=AsyncMock,
                return_value="canonical-uid",
            ),
            patch(
                "shared_models.SessionResponse",
            ) as mock_sr,
        ):
            mock_sr.model_validate.return_value = MagicMock(
                session_id="provided-sess-id"
            )
            result = await create_or_get_session_shared(request, db)

        assert result.session_id == "provided-sess-id"
        # Verify the session timestamp was updated
        assert provided_session.last_request_at is not None

    async def test_provided_session_id_expired_creates_new(self):
        """When provided session_id is expired, create a new session (lines 105-110)."""
        from datetime import datetime, timedelta, timezone

        from request_manager.communication_strategy import create_or_get_session_shared

        # Expired provided session
        expired_session = MagicMock()
        expired_session.session_id = "expired-sess"
        expired_session.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)

        provided_result = MagicMock()
        provided_result.scalar_one_or_none.return_value = expired_session

        # No existing sessions found in the general lookup
        general_result = MagicMock()
        general_result.scalars.return_value.all.return_value = []

        # Session re-fetch after creation
        refetch_result = MagicMock()
        refetch_session = MagicMock()
        refetch_session.session_id = "new-sess-after-expire"
        refetch_result.scalar_one_or_none.return_value = refetch_session

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[provided_result, general_result, AsyncMock(), refetch_result]
        )

        request = MagicMock()
        request.user_id = "user@example.com"
        request.integration_type = "WEB"
        request.metadata = {"session_id": "expired-sess"}
        request.channel_id = None
        request.thread_id = None

        mock_session_response = MagicMock()
        mock_session_response.session_id = "new-sess-after-expire"

        with (
            patch(
                "shared_models.resolve_canonical_user_id",
                new_callable=AsyncMock,
                return_value="canonical-uid",
            ),
            patch(
                "shared_models.BaseSessionManager",
            ) as mock_bsm,
            patch(
                "shared_models.SessionResponse",
            ) as mock_sr,
        ):
            mock_bsm_instance = AsyncMock()
            mock_bsm_instance.create_session = AsyncMock(
                return_value=mock_session_response
            )
            mock_bsm.return_value = mock_bsm_instance
            mock_sr.model_validate.return_value = MagicMock(
                session_id="new-sess-after-expire"
            )

            result = await create_or_get_session_shared(request, db)

        assert result is not None

    async def test_provided_session_id_not_found(self):
        """When provided session_id is not found, create new session (lines 111-116)."""
        from request_manager.communication_strategy import create_or_get_session_shared

        # Provided session not found
        provided_result = MagicMock()
        provided_result.scalar_one_or_none.return_value = None

        # No existing sessions
        general_result = MagicMock()
        general_result.scalars.return_value.all.return_value = []

        # Session re-fetch
        refetch_result = MagicMock()
        refetch_session = MagicMock()
        refetch_session.session_id = "new-sess-fallback"
        refetch_result.scalar_one_or_none.return_value = refetch_session

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[provided_result, general_result, AsyncMock(), refetch_result]
        )

        request = MagicMock()
        request.user_id = "user@example.com"
        request.integration_type = "WEB"
        request.metadata = {"session_id": "nonexistent-sess"}
        request.channel_id = None
        request.thread_id = None

        mock_session_response = MagicMock()
        mock_session_response.session_id = "new-sess-fallback"

        with (
            patch(
                "shared_models.resolve_canonical_user_id",
                new_callable=AsyncMock,
                return_value="canonical-uid",
            ),
            patch(
                "shared_models.BaseSessionManager",
            ) as mock_bsm,
            patch(
                "shared_models.SessionResponse",
            ) as mock_sr,
        ):
            mock_bsm_instance = AsyncMock()
            mock_bsm_instance.create_session = AsyncMock(
                return_value=mock_session_response
            )
            mock_bsm.return_value = mock_bsm_instance
            mock_sr.model_validate.return_value = MagicMock(
                session_id="new-sess-fallback"
            )

            result = await create_or_get_session_shared(request, db)

        assert result is not None

    async def test_multiple_sessions_cleanup(self):
        """When multiple active sessions exist, clean up old ones (lines 171-206)."""
        from request_manager.communication_strategy import create_or_get_session_shared

        # Two active sessions found
        session1 = MagicMock()
        session1.session_id = "sess-1"
        session1.last_request_at = None
        session1.expires_at = None
        session1.integration_type = MagicMock()
        session1.integration_type.value = "WEB"
        session1.current_agent_id = "routing-agent"

        session2 = MagicMock()
        session2.session_id = "sess-2"
        session2.last_request_at = None
        session2.expires_at = None
        session2.integration_type = MagicMock()
        session2.integration_type.value = "WEB"
        session2.current_agent_id = "routing-agent"

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [session1, session2]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        request = MagicMock()
        request.user_id = "user@example.com"
        request.integration_type = MagicMock()
        request.integration_type.value = "WEB"
        request.metadata = {}

        with (
            patch(
                "shared_models.resolve_canonical_user_id",
                new_callable=AsyncMock,
                return_value="canonical-uid",
            ),
            patch(
                "shared_models.SessionResponse",
            ) as mock_sr,
            patch(
                "request_manager.database_utils.cleanup_old_sessions",
                new_callable=AsyncMock,
                return_value=1,
            ) as mock_cleanup,
            patch(
                "shared_models.get_enum_value",
                return_value="WEB",
            ),
        ):
            mock_sr.model_validate.return_value = MagicMock(session_id="sess-1")
            result = await create_or_get_session_shared(request, db)

        assert result.session_id == "sess-1"
        mock_cleanup.assert_awaited_once()

    async def test_filter_by_integration_type(self):
        """When SESSION_PER_INTEGRATION_TYPE is true, filter sessions (line 135)."""
        from request_manager.communication_strategy import create_or_get_session_shared

        existing_session = MagicMock()
        existing_session.session_id = "filtered-sess"
        existing_session.last_request_at = None
        existing_session.expires_at = None
        existing_session.integration_type = MagicMock()
        existing_session.integration_type.value = "WEB"
        existing_session.current_agent_id = "routing-agent"

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [existing_session]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        request = MagicMock()
        request.user_id = "user@example.com"
        request.integration_type = "WEB"
        request.metadata = {}

        with (
            patch(
                "shared_models.resolve_canonical_user_id",
                new_callable=AsyncMock,
                return_value="canonical-uid",
            ),
            patch(
                "shared_models.SessionResponse",
            ) as mock_sr,
            patch(
                "request_manager.communication_strategy._should_filter_sessions_by_integration_type",
                return_value=True,
            ),
        ):
            mock_sr.model_validate.return_value = MagicMock(session_id="filtered-sess")
            result = await create_or_get_session_shared(request, db)

        assert result.session_id == "filtered-sess"

    async def test_no_integration_type_defaults_to_web(self):
        """When integration_type is None, defaults to IntegrationType.WEB (lines 236-238)."""
        from request_manager.communication_strategy import create_or_get_session_shared

        # No existing sessions
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []

        # Session re-fetch
        refetch_result = MagicMock()
        refetch_session = MagicMock()
        refetch_session.session_id = "web-default-sess"
        refetch_result.scalar_one_or_none.return_value = refetch_session

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[result_mock, AsyncMock(), refetch_result])

        request = MagicMock()
        request.user_id = "user@example.com"
        request.integration_type = None  # No integration type
        request.metadata = {}
        request.channel_id = None
        request.thread_id = None

        mock_session_response = MagicMock()
        mock_session_response.session_id = "web-default-sess"

        with (
            patch(
                "shared_models.resolve_canonical_user_id",
                new_callable=AsyncMock,
                return_value="canonical-uid",
            ),
            patch(
                "shared_models.BaseSessionManager",
            ) as mock_bsm,
            patch(
                "shared_models.SessionResponse",
            ) as mock_sr,
        ):
            mock_bsm_instance = AsyncMock()
            mock_bsm_instance.create_session = AsyncMock(
                return_value=mock_session_response
            )
            mock_bsm.return_value = mock_bsm_instance
            mock_sr.model_validate.return_value = MagicMock(
                session_id="web-default-sess"
            )

            result = await create_or_get_session_shared(request, db)

        assert result is not None

    async def test_session_creation_failure_fallback(self):
        """When session creation fails, fallback to get_active_session (lines 281-299)."""
        from request_manager.communication_strategy import create_or_get_session_shared

        # No existing sessions
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        request = MagicMock()
        request.user_id = "user@example.com"
        request.integration_type = "WEB"
        request.metadata = {}
        request.channel_id = None
        request.thread_id = None

        # Session creation fails, but get_active_session finds one
        fallback_session = MagicMock()
        fallback_session.session_id = "fallback-sess"

        with (
            patch(
                "shared_models.resolve_canonical_user_id",
                new_callable=AsyncMock,
                return_value="canonical-uid",
            ),
            patch(
                "shared_models.BaseSessionManager",
            ) as mock_bsm,
            patch(
                "shared_models.SessionResponse",
            ) as mock_sr,
        ):
            mock_bsm_instance = AsyncMock()
            mock_bsm_instance.create_session = AsyncMock(
                side_effect=Exception("DB constraint violation")
            )
            mock_bsm_instance.get_active_session = AsyncMock(
                return_value=fallback_session
            )
            mock_bsm.return_value = mock_bsm_instance
            mock_sr.model_validate.return_value = MagicMock(session_id="fallback-sess")

            result = await create_or_get_session_shared(request, db)

        assert result.session_id == "fallback-sess"

    async def test_session_creation_failure_no_fallback_raises(self):
        """When session creation fails and no fallback, re-raise (line 299)."""
        from request_manager.communication_strategy import create_or_get_session_shared

        # No existing sessions
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        request = MagicMock()
        request.user_id = "user@example.com"
        request.integration_type = "WEB"
        request.metadata = {}
        request.channel_id = None
        request.thread_id = None

        with (
            patch(
                "shared_models.resolve_canonical_user_id",
                new_callable=AsyncMock,
                return_value="canonical-uid",
            ),
            patch(
                "shared_models.BaseSessionManager",
            ) as mock_bsm,
        ):
            mock_bsm_instance = AsyncMock()
            mock_bsm_instance.create_session = AsyncMock(
                side_effect=Exception("DB down")
            )
            mock_bsm_instance.get_active_session = AsyncMock(return_value=None)
            mock_bsm.return_value = mock_bsm_instance

            with pytest.raises(Exception, match="DB down"):
                await create_or_get_session_shared(request, db)


# ---------------------------------------------------------------------------
# DirectHTTPStrategy.invoke_agent_with_routing - max hops exceeded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestInvokeAgentWithRoutingExtended:
    """Additional tests for invoke_agent_with_routing."""

    async def test_max_routing_hops_exceeded(self):
        """When max routing hops are exceeded, raise an exception (lines 548-553)."""
        from dataclasses import dataclass, field

        @dataclass
        class FakeOPADecision:
            allow: bool = True
            reason: str = "ok"
            effective_departments: list = field(default_factory=lambda: ["engineering"])

        strategy = DirectHTTPStrategy()
        strategy.agent_client = AsyncMock()
        strategy._registry_fetched = True

        # Every call returns a routing decision, creating an infinite loop
        routing_response = {
            "content": "",
            "agent_id": "routing-agent",
            "routing_decision": "some-agent",
            "metadata": {"handling_agent": "routing-agent"},
        }
        strategy.agent_client.invoke_agent = AsyncMock(return_value=routing_response)

        normalized = MagicMock()
        normalized.request_id = "req-loop"
        normalized.session_id = "sess-loop"
        normalized.user_id = "user@example.com"
        normalized.content = "loop test"
        normalized.user_context = {
            "user_context": {
                "departments": ["engineering"],
                "spiffe_id": "spiffe://test/user/user",
                "email": "user@example.com",
            }
        }

        db = AsyncMock()

        with (
            patch.object(
                strategy,
                "_get_conversation_history",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "shared_models.opa_client.check_agent_authorization",
                new_callable=AsyncMock,
                return_value=FakeOPADecision(),
            ),
            patch(
                "request_manager.communication_strategy.make_spiffe_id",
                return_value="spiffe://test/service/request-manager",
            ),
        ):
            with pytest.raises(Exception, match="Max routing hops"):
                await strategy.invoke_agent_with_routing(normalized, db)

    async def test_invoke_with_target_agent(self):
        """When target_agent is provided, skip routing-agent (line 400)."""
        strategy = DirectHTTPStrategy()
        strategy.agent_client = AsyncMock()
        strategy._registry_fetched = True

        direct_response = {
            "content": "Direct from specialist.",
            "agent_id": "software-support",
            "routing_decision": None,
            "metadata": {"handling_agent": "software-support"},
        }
        strategy.agent_client.invoke_agent = AsyncMock(return_value=direct_response)

        normalized = MagicMock()
        normalized.request_id = "req-target"
        normalized.session_id = "sess-target"
        normalized.user_id = "user@example.com"
        normalized.content = "Direct question"
        normalized.user_context = {
            "user_context": {
                "departments": ["engineering"],
                "spiffe_id": "",
                "email": "user@example.com",
            }
        }

        db = AsyncMock()

        with patch.object(
            strategy,
            "_get_conversation_history",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await strategy.invoke_agent_with_routing(
                normalized, db, target_agent="software-support"
            )

        assert result["content"] == "Direct from specialist."
        # Verify it called the target agent directly, not routing-agent
        call_args = strategy.agent_client.invoke_agent.call_args
        assert call_args.kwargs["agent_name"] == "software-support"

    async def test_multi_hop_routing(self):
        """Test multi-hop routing: routing-agent -> specialist1 -> specialist2 (routing chain)."""
        from dataclasses import dataclass, field

        @dataclass
        class FakeOPADecision:
            allow: bool = True
            reason: str = "ok"
            effective_departments: list = field(default_factory=lambda: ["engineering"])

        strategy = DirectHTTPStrategy()
        strategy.agent_client = AsyncMock()
        strategy._registry_fetched = True

        # First call: routing-agent routes to specialist1
        hop1 = {
            "content": "",
            "agent_id": "routing-agent",
            "routing_decision": "specialist1",
            "metadata": {"from": "routing-agent"},
        }
        # Second call: specialist1 routes to specialist2
        hop2 = {
            "content": "",
            "agent_id": "specialist1",
            "routing_decision": "specialist2",
            "metadata": {"from": "specialist1"},
        }
        # Third call: specialist2 returns final response
        hop3 = {
            "content": "Final multi-hop answer.",
            "agent_id": "specialist2",
            "routing_decision": None,
            "metadata": {"handling_agent": "specialist2"},
        }
        strategy.agent_client.invoke_agent = AsyncMock(side_effect=[hop1, hop2, hop3])

        normalized = MagicMock()
        normalized.request_id = "req-multi"
        normalized.session_id = "sess-multi"
        normalized.user_id = "user@example.com"
        normalized.content = "Multi-hop question"
        normalized.user_context = {
            "user_context": {
                "departments": ["engineering"],
                "spiffe_id": "spiffe://test/user/user",
                "email": "user@example.com",
            }
        }

        db = AsyncMock()

        with (
            patch.object(
                strategy,
                "_get_conversation_history",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "shared_models.opa_client.check_agent_authorization",
                new_callable=AsyncMock,
                return_value=FakeOPADecision(),
            ),
            patch(
                "request_manager.communication_strategy.make_spiffe_id",
                return_value="spiffe://test/service/request-manager",
            ),
        ):
            result = await strategy.invoke_agent_with_routing(normalized, db)

        assert result["content"] == "Final multi-hop answer."
        assert result["agent_id"] == "specialist2"
        assert strategy.agent_client.invoke_agent.call_count == 3

    async def test_scope_reduction_passes_effective_departments(self):
        """After OPA check, specialist receives effective_departments (intersection), not full departments."""
        from dataclasses import dataclass, field

        @dataclass
        class FakeOPADecision:
            allow: bool = True
            reason: str = "ok"
            effective_departments: list = field(default_factory=lambda: ["software"])

        strategy = DirectHTTPStrategy()
        strategy.agent_client = AsyncMock()
        strategy._registry_fetched = True

        # Routing agent routes to software-support
        routing_response = {
            "content": "",
            "agent_id": "routing-agent",
            "routing_decision": "software-support",
            "metadata": {},
        }
        specialist_response = {
            "content": "Fixed it.",
            "agent_id": "software-support",
            "routing_decision": None,
            "metadata": {},
        }
        strategy.agent_client.invoke_agent = AsyncMock(
            side_effect=[routing_response, specialist_response]
        )

        # User has engineering + software, but OPA intersection gives only software
        normalized = MagicMock()
        normalized.request_id = "req-scope"
        normalized.session_id = "sess-scope"
        normalized.user_id = "user@example.com"
        normalized.content = "software issue"
        normalized.user_context = {
            "user_context": {
                "departments": ["engineering", "software"],
                "spiffe_id": "spiffe://test/user/user",
                "email": "user@example.com",
            }
        }

        db = AsyncMock()

        with (
            patch.object(
                strategy,
                "_get_conversation_history",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "shared_models.opa_client.check_agent_authorization",
                new_callable=AsyncMock,
                return_value=FakeOPADecision(),
            ),
            patch(
                "request_manager.communication_strategy.make_spiffe_id",
                return_value="spiffe://test/service/request-manager",
            ),
        ):
            result = await strategy.invoke_agent_with_routing(normalized, db)

        assert result["content"] == "Fixed it."

        # The second call (specialist) should receive effective_departments ["software"],
        # NOT the full user departments ["engineering", "software"]
        specialist_call = strategy.agent_client.invoke_agent.call_args_list[1]
        specialist_tc = specialist_call.kwargs.get("transfer_context", {})
        assert specialist_tc["departments"] == ["software"]

    async def test_delegation_headers_sent_for_specialist_not_routing(self):
        """Delegation headers are sent to specialist agents but NOT to routing-agent."""
        from dataclasses import dataclass, field

        @dataclass
        class FakeOPADecision:
            allow: bool = True
            reason: str = "ok"
            effective_departments: list = field(default_factory=lambda: ["software"])

        strategy = DirectHTTPStrategy()
        strategy.agent_client = AsyncMock()
        strategy._registry_fetched = True

        routing_response = {
            "content": "",
            "agent_id": "routing-agent",
            "routing_decision": "software-support",
            "metadata": {},
        }
        specialist_response = {
            "content": "Answer.",
            "agent_id": "software-support",
            "routing_decision": None,
            "metadata": {},
        }
        strategy.agent_client.invoke_agent = AsyncMock(
            side_effect=[routing_response, specialist_response]
        )

        normalized = MagicMock()
        normalized.request_id = "req-deleg"
        normalized.session_id = "sess-deleg"
        normalized.user_id = "user@example.com"
        normalized.content = "help"
        normalized.user_context = {
            "user_context": {
                "departments": ["software"],
                "spiffe_id": "spiffe://test/user/user",
                "email": "user@example.com",
            }
        }

        db = AsyncMock()

        with (
            patch.object(
                strategy,
                "_get_conversation_history",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "shared_models.opa_client.check_agent_authorization",
                new_callable=AsyncMock,
                return_value=FakeOPADecision(),
            ),
            patch(
                "request_manager.communication_strategy.make_spiffe_id",
                return_value="spiffe://test/service/request-manager",
            ),
        ):
            await strategy.invoke_agent_with_routing(normalized, db)

        # First call (routing-agent): NO delegation
        routing_call = strategy.agent_client.invoke_agent.call_args_list[0]
        assert routing_call.kwargs.get("delegation_user_spiffe_id") is None

        # Second call (specialist): WITH delegation
        specialist_call = strategy.agent_client.invoke_agent.call_args_list[1]
        assert specialist_call.kwargs.get("delegation_user_spiffe_id") is not None

    async def test_delegation_sent_when_target_agent_provided(self):
        """When target_agent bypasses routing, delegation is included from the start."""
        strategy = DirectHTTPStrategy()
        strategy.agent_client = AsyncMock()
        strategy._registry_fetched = True

        direct_response = {
            "content": "Direct answer.",
            "agent_id": "software-support",
            "routing_decision": None,
            "metadata": {},
        }
        strategy.agent_client.invoke_agent = AsyncMock(return_value=direct_response)

        normalized = MagicMock()
        normalized.request_id = "req-direct-deleg"
        normalized.session_id = "sess-direct-deleg"
        normalized.user_id = "user@example.com"
        normalized.content = "help"
        normalized.user_context = {
            "user_context": {
                "departments": ["software"],
                "spiffe_id": "spiffe://test/user/user",
                "email": "user@example.com",
            }
        }

        db = AsyncMock()

        with (
            patch.object(
                strategy,
                "_get_conversation_history",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "request_manager.communication_strategy.make_spiffe_id",
                return_value="spiffe://test/user/user",
            ),
        ):
            await strategy.invoke_agent_with_routing(
                normalized, db, target_agent="software-support"
            )

        call = strategy.agent_client.invoke_agent.call_args
        assert call.kwargs.get("delegation_user_spiffe_id") is not None


# ---------------------------------------------------------------------------
# CommunicationStrategy base class
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCommunicationStrategyBase:
    """Tests for the CommunicationStrategy base class."""

    async def test_create_or_get_session_delegates_to_shared(self):
        """CommunicationStrategy.create_or_get_session delegates to create_or_get_session_shared (line 313)."""
        strategy = DirectHTTPStrategy()

        request = MagicMock()
        db = AsyncMock()

        with patch(
            "request_manager.communication_strategy.create_or_get_session_shared",
            new_callable=AsyncMock,
            return_value=MagicMock(session_id="delegated-sess"),
        ) as mock_shared:
            result = await strategy.create_or_get_session(request, db)

        mock_shared.assert_awaited_once_with(request, db)
        assert result.session_id == "delegated-sess"


# ---------------------------------------------------------------------------
# UnifiedRequestProcessor._complete_request_log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCompleteRequestLog:
    """Tests for _complete_request_log method (lines 831-834)."""

    async def test_complete_request_log_handles_db_error(self):
        """When DB update fails, log warning but don't raise."""
        strategy = AsyncMock(spec=DirectHTTPStrategy)
        processor = UnifiedRequestProcessor(strategy)

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=Exception("DB write error"))

        # Should not raise - just logs warning
        await processor._complete_request_log(
            request_id="req-err",
            agent_id="test-agent",
            response_content="some response",
            response_metadata={"key": "val"},
            processing_time_ms=100,
            db=db,
        )

    async def test_complete_request_log_success(self):
        """Successful completion updates the request log."""
        strategy = AsyncMock(spec=DirectHTTPStrategy)
        processor = UnifiedRequestProcessor(strategy)

        db = AsyncMock()
        db.execute = AsyncMock()
        db.commit = AsyncMock()

        await processor._complete_request_log(
            request_id="req-ok",
            agent_id="test-agent",
            response_content="response",
            response_metadata={},
            processing_time_ms=50,
            db=db,
        )

        db.execute.assert_awaited_once()
        db.commit.assert_awaited_once()
