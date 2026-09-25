"""
Integration tests for delegation and act_claim handling.

These tests verify that the complete delegation chain flows through
the system correctly, catching issues with:
- Delegation dataclass field compatibility
- Act claim propagation
- Policy engine integration with delegation context
- Token exchange with delegation
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from shared_models.policy_client import Delegation
from shared_models.delegation_chain import (
    build_act_claim,
    parse_act_claim,
    validate_delegation_chain,
    extract_original_user,
)


class TestDelegationDataclass:
    """Test Delegation dataclass compatibility."""

    def test_delegation_with_all_fields(self):
        """Verify Delegation can be created with all fields including act_claim."""
        # This test would have caught the "unexpected keyword argument" error
        delegation = Delegation(
            user_spiffe_id="spiffe://example.com/user/carlos",
            agent_spiffe_id="spiffe://example.com/agent/routing",
            user_departments=["kubernetes", "software"],
            act_claim={"sub": "request-manager"},
            delegation_chain=["routing-agent", "request-manager"],
            original_user_email="carlos@example.com",
        )

        assert delegation.user_spiffe_id == "spiffe://example.com/user/carlos"
        assert delegation.agent_spiffe_id == "spiffe://example.com/agent/routing"
        assert delegation.user_departments == ["kubernetes", "software"]
        assert delegation.act_claim == {"sub": "request-manager"}
        assert delegation.delegation_chain == ["routing-agent", "request-manager"]
        assert delegation.original_user_email == "carlos@example.com"

    def test_delegation_minimal_fields(self):
        """Verify Delegation works with only required fields."""
        delegation = Delegation(
            user_spiffe_id="spiffe://example.com/user/carlos",
            agent_spiffe_id="spiffe://example.com/agent/routing",
        )

        assert delegation.user_departments == []  # default
        assert delegation.act_claim is None
        assert delegation.delegation_chain is None
        assert delegation.original_user_email is None

    def test_delegation_to_dict_with_act_claim(self):
        """Verify to_dict() includes act_claim when present."""
        delegation = Delegation(
            user_spiffe_id="spiffe://example.com/user/carlos",
            agent_spiffe_id="spiffe://example.com/agent/routing",
            user_departments=["kubernetes"],
            act_claim={"sub": "routing-agent", "act": {"sub": "carlos@example.com"}},
        )

        result = delegation.to_dict()

        assert "act_claim" in result
        assert result["act_claim"]["sub"] == "routing-agent"
        assert result["act_claim"]["act"]["sub"] == "carlos@example.com"

    def test_delegation_to_dict_without_act_claim(self):
        """Verify to_dict() works when act_claim is None."""
        delegation = Delegation(
            user_spiffe_id="spiffe://example.com/user/carlos",
            agent_spiffe_id="spiffe://example.com/agent/routing",
            user_departments=["kubernetes"],
        )

        result = delegation.to_dict()

        assert "act_claim" not in result  # Should not include if None

    def test_delegation_to_dict_with_delegation_chain(self):
        """Verify to_dict() includes delegation_chain when present."""
        delegation = Delegation(
            user_spiffe_id="spiffe://example.com/user/carlos",
            agent_spiffe_id="spiffe://example.com/agent/routing",
            user_departments=["kubernetes"],
            delegation_chain=["routing-agent", "kubernetes-agent", "carlos@example.com"],
        )

        result = delegation.to_dict()

        assert "delegation_chain" in result
        assert result["delegation_chain"] == [
            "routing-agent",
            "kubernetes-agent",
            "carlos@example.com",
        ]


class TestDelegationChainIntegration:
    """Test delegation chain utilities integration."""

    def test_build_parse_roundtrip(self):
        """Build a chain, parse it, verify original structure."""
        # Build chain: carlos → rm → routing → k8s
        user_claim = build_act_claim("carlos@example.com")
        rm_claim = build_act_claim("request-manager", user_claim)
        routing_claim = build_act_claim("routing-agent", rm_claim)
        k8s_claim = build_act_claim("kubernetes-agent", routing_claim)

        # Parse back
        chain = parse_act_claim(k8s_claim)

        # Verify
        assert chain == [
            "kubernetes-agent",
            "routing-agent",
            "request-manager",
            "carlos@example.com",
        ]

        # Extract original user
        original = extract_original_user(k8s_claim)
        assert original == "carlos@example.com"

    def test_delegation_with_parsed_act_claim(self):
        """Verify Delegation can use parsed act claim."""
        # Build act claim
        act_claim = build_act_claim("routing-agent", {"sub": "carlos@example.com"})

        # Parse to get chain
        chain = parse_act_claim(act_claim)

        # Create Delegation with both
        delegation = Delegation(
            user_spiffe_id="spiffe://example.com/user/carlos",
            agent_spiffe_id="spiffe://example.com/agent/routing",
            user_departments=["kubernetes"],
            act_claim=act_claim,
            delegation_chain=chain,
        )

        # Verify
        assert delegation.act_claim == {"sub": "routing-agent", "act": {"sub": "carlos@example.com"}}
        assert delegation.delegation_chain == ["routing-agent", "carlos@example.com"]

    def test_validate_delegation_chain_from_delegation(self):
        """Verify delegation chain validation works with Delegation object."""
        # Create valid delegation
        delegation = Delegation(
            user_spiffe_id="spiffe://example.com/user/carlos",
            agent_spiffe_id="spiffe://example.com/agent/routing",
            user_departments=["kubernetes"],
            delegation_chain=["routing-agent", "k8s-agent", "carlos@example.com"],
        )

        # Validate
        is_valid = validate_delegation_chain(delegation.delegation_chain)
        assert is_valid is True

    def test_reject_circular_delegation(self):
        """Verify circular delegation is rejected."""
        # Create circular delegation
        circular_chain = ["routing-agent", "k8s-agent", "routing-agent", "k8s-agent", "routing-agent"]

        # Validate
        is_valid = validate_delegation_chain(circular_chain)
        assert is_valid is False

    def test_reject_too_deep_delegation(self):
        """Verify too-deep delegation chains are rejected."""
        # Create 11-hop chain (too deep)
        deep_chain = [f"agent-{i}" for i in range(11)]

        # Validate
        is_valid = validate_delegation_chain(deep_chain)
        assert is_valid is False


class TestPolicyEngineIntegration:
    """Test policy engine integration with delegation."""

    @pytest.mark.asyncio
    async def test_policy_engine_evaluates_act_claim(self):
        """Verify in-process policy evaluator handles act_claim in delegation."""
        from shared_models.policy_client import check_agent_authorization

        delegation = Delegation(
            user_spiffe_id="spiffe://example.com/user/carlos",
            agent_spiffe_id="spiffe://example.com/agent/routing",
            user_departments=["kubernetes", "software"],
            act_claim={"sub": "request-manager"},
        )

        decision = await check_agent_authorization(
            caller_spiffe_id="spiffe://partner.example.com/request-manager",
            agent_name="routing-agent",
            delegation=delegation,
        )

        assert decision.allow is True
        assert "kubernetes" in decision.effective_departments
        assert "software" in decision.effective_departments


class TestTokenExchangeWithDelegation:
    """Test token exchange integration with delegation chains."""

    @pytest.mark.asyncio
    async def test_token_exchange_builds_nested_act_claim(self):
        """Verify token exchange builds nested act claims correctly."""
        from request_manager.token_exchange import TokenExchangeClient

        client = TokenExchangeClient()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new_token_with_act_claim",
            "token_type": "Bearer",
            "expires_in": 300,
        }
        mock_response.raise_for_status = MagicMock()

        client.client = AsyncMock()
        client.client.post = AsyncMock(return_value=mock_response)

        result = await client.exchange_with_delegation(
            subject_token="user_token",
            target_agent="kubernetes-agent",
            actor_service="routing-agent",
            existing_act_claim={"sub": "request-manager"},
        )

        assert "access_token" in result


class TestEndToEndDelegationFlow:
    """End-to-end tests for delegation flow."""

    def test_complete_delegation_flow(self):
        """
        Test complete flow:
        1. Build delegation chain
        2. Create Delegation object
        3. Validate chain
        4. Convert to dict for policy engine
        5. Extract original user
        """
        # Step 1: Build delegation chain
        user_claim = build_act_claim("carlos@example.com")
        rm_claim = build_act_claim("request-manager", user_claim)
        routing_claim = build_act_claim("routing-agent", rm_claim)

        # Step 2: Parse chain
        chain = parse_act_claim(routing_claim)

        # Step 3: Create Delegation
        delegation = Delegation(
            user_spiffe_id="spiffe://example.com/user/carlos",
            agent_spiffe_id="spiffe://example.com/agent/routing",
            user_departments=["kubernetes", "software"],
            act_claim=routing_claim,
            delegation_chain=chain,
            original_user_email="carlos@example.com",
        )

        # Step 4: Validate chain
        is_valid = validate_delegation_chain(delegation.delegation_chain)
        assert is_valid is True

        # Step 5: Convert to dict for policy engine
        policy_input = delegation.to_dict()

        assert "act_claim" in policy_input
        assert "delegation_chain" in policy_input
        assert "original_user_email" in policy_input

        assert policy_input["delegation_chain"] == [
            "routing-agent",
            "request-manager",
            "carlos@example.com",
        ]
        assert policy_input["original_user_email"] == "carlos@example.com"

        # Step 6: Extract original user
        original = extract_original_user(delegation.act_claim)
        assert original == "carlos@example.com"

    def test_bidirectional_delegation_allowed(self):
        """Test that bidirectional delegation (A→B→A) is allowed."""
        # Build chain: carlos → routing → k8s → routing (bidirectional)
        chain = ["routing-agent", "k8s-agent", "routing-agent", "carlos@example.com"]

        # Validate (should be True - routing appears 2x which is OK)
        is_valid = validate_delegation_chain(chain)
        assert is_valid is True

    def test_circular_delegation_blocked(self):
        """Test that circular delegation (A→B→A→B→A) is blocked."""
        # Build chain: routing → k8s → routing → k8s → routing (circular)
        chain = ["routing-agent", "k8s-agent", "routing-agent", "k8s-agent", "routing-agent"]

        # Validate (should be False - routing appears 3x = circular)
        is_valid = validate_delegation_chain(chain)
        assert is_valid is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
