"""Tests for shared_models.policy_client module."""

import pytest

from shared_models.policy_client import (
    Delegation,
    PolicyDecision,
    check_agent_authorization,
    get_user_departments_fallback,
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


class TestPolicyDecision:
    """Tests for PolicyDecision dataclass."""

    def test_fields(self):
        decision = PolicyDecision(
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
        decision = PolicyDecision(allow=False, reason="denied")
        assert decision.effective_departments == []
        assert decision.details == {}


@pytest.mark.asyncio
class TestCheckAgentAuthorization:
    """Tests for check_agent_authorization() — in-process policy evaluation."""

    async def test_service_to_service_without_delegation_allowed(self):
        """Rule 1: Service-to-service call without delegation is allowed."""
        decision = await check_agent_authorization(
            "spiffe://partner.example.com/service/request-manager",
            "software-support",
        )
        assert decision.allow is True
        assert "Service-to-service" in decision.reason

    async def test_delegated_access_with_matching_departments_allowed(self):
        """Rule 3: Delegated access with non-empty department intersection is allowed."""
        delegation = Delegation(
            user_spiffe_id="spiffe://partner.example.com/user/alice",
            agent_spiffe_id="spiffe://partner.example.com/agent/support",
            user_departments=["software", "network"],
        )
        decision = await check_agent_authorization(
            "spiffe://partner.example.com/service/request-manager",
            "software-support",
            delegation=delegation,
        )
        assert decision.allow is True
        assert "software" in decision.effective_departments

    async def test_delegated_access_no_matching_departments_denied(self):
        """Rule 4: Delegated access with empty department intersection is denied."""
        delegation = Delegation(
            user_spiffe_id="spiffe://partner.example.com/user/alice",
            agent_spiffe_id="spiffe://partner.example.com/agent/support",
            user_departments=["network"],
        )
        decision = await check_agent_authorization(
            "spiffe://partner.example.com/service/request-manager",
            "software-support",
            delegation=delegation,
        )
        assert decision.allow is False
        assert "No overlapping" in decision.reason

    async def test_unknown_agent_denied(self):
        """Rule 6: Unknown agent is denied."""
        delegation = Delegation(
            user_spiffe_id="spiffe://partner.example.com/user/alice",
            agent_spiffe_id="spiffe://partner.example.com/agent/support",
            user_departments=["software"],
        )
        decision = await check_agent_authorization(
            "spiffe://partner.example.com/service/request-manager",
            "nonexistent-agent",
            delegation=delegation,
        )
        assert decision.allow is False
        assert "Unknown agent" in decision.reason

    async def test_autonomous_agent_without_delegation_denied(self):
        """Rule 5: Autonomous agent without delegation is denied."""
        decision = await check_agent_authorization(
            "spiffe://partner.example.com/agent/rogue-agent",
            "software-support",
        )
        assert decision.allow is False
        assert "delegation" in decision.reason.lower()

    async def test_effective_departments_computed_correctly(self):
        """Effective departments are the intersection of user depts and agent capabilities."""
        delegation = Delegation(
            user_spiffe_id="spiffe://partner.example.com/user/alice",
            agent_spiffe_id="spiffe://partner.example.com/agent/routing",
            user_departments=["kubernetes", "software", "network"],
        )
        # routing-agent has capabilities: admin, kubernetes, network, software
        decision = await check_agent_authorization(
            "spiffe://partner.example.com/service/request-manager",
            "routing-agent",
            delegation=delegation,
        )
        assert decision.allow is True
        assert sorted(decision.effective_departments) == ["kubernetes", "network", "software"]


@pytest.mark.asyncio
class TestGetUserDepartmentsFallback:
    """Tests for get_user_departments_fallback()."""

    async def test_returns_empty_for_any_user(self):
        """Fallback map is empty — all users managed in Keycloak."""
        departments = await get_user_departments_fallback("alice@example.com")
        assert departments == []

    async def test_returns_empty_for_unknown_user(self):
        departments = await get_user_departments_fallback("unknown@example.com")
        assert departments == []
