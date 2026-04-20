"""Tests for shared_models.opa_client module."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from shared_models.opa_client import (
    Delegation,
    OPADecision,
    check_agent_authorization,
    get_user_departments_from_opa,
)


class TestDelegation:
    """Tests for Delegation dataclass."""

    def test_to_dict(self):
        d = Delegation(
            user_spiffe_id="spiffe://example.com/user/alice",
            agent_spiffe_id="spiffe://example.com/agent/support",
            user_departments=["software", "hr"],
        )
        result = d.to_dict()
        assert result["user_spiffe_id"] == "spiffe://example.com/user/alice"
        assert result["agent_spiffe_id"] == "spiffe://example.com/agent/support"
        assert result["user_departments"] == ["software", "hr"]

    def test_to_dict_empty_departments(self):
        d = Delegation(
            user_spiffe_id="spiffe://example.com/user/bob",
            agent_spiffe_id="spiffe://example.com/agent/support",
        )
        result = d.to_dict()
        assert result["user_departments"] == []


class TestOPADecision:
    """Tests for OPADecision dataclass."""

    def test_fields(self):
        decision = OPADecision(
            allow=True,
            reason="authorized",
            effective_departments=["software"],
            details={"extra": "data"},
        )
        assert decision.allow is True
        assert decision.reason == "authorized"
        assert decision.effective_departments == ["software"]
        assert decision.details == {"extra": "data"}

    def test_defaults(self):
        decision = OPADecision(allow=False, reason="denied")
        assert decision.effective_departments == []
        assert decision.details == {}


class TestCheckAgentAuthorization:
    """Tests for check_agent_authorization()."""

    @patch("shared_models.opa_client.httpx.AsyncClient")
    async def test_allow_decision(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "allow": True,
                "reason": "authorized by policy",
                "effective_departments": ["software"],
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        decision = await check_agent_authorization(
            "spiffe://example.com/service/rm",
            "software-support",
        )

        assert decision.allow is True
        assert decision.reason == "authorized by policy"
        assert "software" in decision.effective_departments

    @patch("shared_models.opa_client.httpx.AsyncClient")
    async def test_deny_decision(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "allow": False,
                "reason": "no matching departments",
                "effective_departments": [],
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        decision = await check_agent_authorization(
            "spiffe://example.com/service/rm",
            "hr-agent",
        )

        assert decision.allow is False
        assert decision.reason == "no matching departments"

    @patch("shared_models.opa_client.httpx.AsyncClient")
    async def test_with_delegation(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "allow": True,
                "reason": "delegated access",
                "effective_departments": ["software"],
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        delegation = Delegation(
            user_spiffe_id="spiffe://example.com/user/alice",
            agent_spiffe_id="spiffe://example.com/agent/support",
            user_departments=["software"],
        )

        decision = await check_agent_authorization(
            "spiffe://example.com/service/rm",
            "software-support",
            delegation=delegation,
        )

        assert decision.allow is True
        # Verify delegation was passed in the request
        call_args = mock_client.post.call_args
        sent_json = call_args.kwargs.get("json") or call_args[1].get("json")
        assert "delegation" in sent_json["input"]

    @patch("shared_models.opa_client.httpx.AsyncClient")
    async def test_connect_error_denies_by_default(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        decision = await check_agent_authorization(
            "spiffe://example.com/service/rm",
            "software-support",
        )

        assert decision.allow is False
        assert "unavailable" in decision.reason.lower()

    @patch("shared_models.opa_client.httpx.AsyncClient")
    async def test_general_exception_denies(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=RuntimeError("unexpected"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        decision = await check_agent_authorization(
            "spiffe://example.com/service/rm",
            "software-support",
        )

        assert decision.allow is False
        assert "error" in decision.reason.lower()


class TestGetUserDepartmentsFromOpa:
    """Tests for get_user_departments_from_opa()."""

    @patch("shared_models.opa_client.httpx.AsyncClient")
    async def test_returns_departments(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "alice@example.com": ["software", "engineering"],
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        departments = await get_user_departments_from_opa("alice@example.com")
        assert departments == ["software", "engineering"]

    @patch("shared_models.opa_client.httpx.AsyncClient")
    async def test_returns_empty_for_unknown_user(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": {}}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        departments = await get_user_departments_from_opa("unknown@example.com")
        assert departments == []

    @patch("shared_models.opa_client.httpx.AsyncClient")
    async def test_returns_empty_on_error(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=RuntimeError("network error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        departments = await get_user_departments_from_opa("alice@example.com")
        assert departments == []
