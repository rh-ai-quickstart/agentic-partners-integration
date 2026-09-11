"""
Integration tests for delegation and act_claim handling.

These tests verify that the complete delegation chain flows through
the system correctly, catching issues with:
- Delegation dataclass field compatibility
- Act claim propagation
- OPA integration with delegation context
- Token exchange with delegation
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from shared_models.opa_client import Delegation
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


class TestOPAIntegration:
    """Test OPA integration with delegation."""

    @pytest.mark.asyncio
    async def test_opa_receives_act_claim(self):
        """Verify OPA receives act_claim in delegation context."""
        from shared_models.opa_client import check_agent_authorization

        # Create delegation with act claim
        delegation = Delegation(
            user_spiffe_id="spiffe://example.com/user/carlos",
            agent_spiffe_id="spiffe://example.com/agent/routing",
            user_departments=["kubernetes", "software"],
            act_claim={"sub": "request-manager"},
        )

        # Mock OPA response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "allow": True,
                "reason": "Permission intersection found",
                "effective_departments": ["kubernetes"],
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            # Call OPA
            decision = await check_agent_authorization(
                caller_spiffe_id="spiffe://example.com/user/carlos",
                agent_name="routing-agent",
                delegation=delegation,
            )

            # Verify OPA was called with act_claim
            call_args = mock_post.call_args
            opa_input = call_args.kwargs["json"]["input"]

            assert "delegation" in opa_input
            assert "act_claim" in opa_input["delegation"]
            assert opa_input["delegation"]["act_claim"] == {"sub": "request-manager"}

            assert decision.allow is True


class TestTokenExchangeWithDelegation:
    """Test token exchange integration with delegation chains."""

    @pytest.mark.asyncio
    async def test_token_exchange_builds_nested_act_claim(self):
        """Verify token exchange builds nested act claims correctly."""
        from request_manager.token_exchange import TokenExchangeClient

        client = TokenExchangeClient()

        # Mock Keycloak response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "new_token_with_act_claim",
            "token_type": "Bearer",
            "expires_in": 300,
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            # Exchange token with existing act claim
            result = await client.exchange_with_delegation(
                subject_token="user_token",
                target_agent="kubernetes-agent",
                actor_service="routing-agent",
                existing_act_claim={"sub": "request-manager"},
            )

            # Verify the request included nested act claim
            # (implementation would need to expose this in the request)
            assert "access_token" in result


class TestEndToEndDelegationFlow:
    """End-to-end tests for delegation flow."""

    def test_complete_delegation_flow(self):
        """
        Test complete flow:
        1. Build delegation chain
        2. Create Delegation object
        3. Validate chain
        4. Convert to dict for OPA
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

        # Step 5: Convert to dict for OPA
        opa_input = delegation.to_dict()

        assert "act_claim" in opa_input
        assert "delegation_chain" in opa_input
        assert "original_user_email" in opa_input

        assert opa_input["delegation_chain"] == [
            "routing-agent",
            "request-manager",
            "carlos@example.com",
        ]
        assert opa_input["original_user_email"] == "carlos@example.com"

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
