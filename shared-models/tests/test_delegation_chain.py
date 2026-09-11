"""Tests for shared_models.delegation_chain module.

Tests delegation chain parsing, building, validation, and extraction
for actor-actor (act) claims used in JWT tokens.
"""

import pytest

from shared_models.delegation_chain import (
    build_act_claim,
    extract_original_user,
    parse_act_claim,
    validate_delegation_chain,
)


@pytest.fixture
def simple_act_claim():
    """Single-level act claim (direct user, no delegation)."""
    return {"sub": "carlos@example.com"}


@pytest.fixture
def nested_act_claim():
    """Multi-hop delegation chain (3 levels)."""
    return {
        "sub": "routing-agent",
        "act": {
            "sub": "k8s-agent",
            "act": {
                "sub": "carlos@example.com"
            }
        }
    }


@pytest.fixture
def deep_act_claim():
    """Deep delegation chain (6 levels)."""
    return {
        "sub": "level1-agent",
        "act": {
            "sub": "level2-agent",
            "act": {
                "sub": "level3-agent",
                "act": {
                    "sub": "level4-agent",
                    "act": {
                        "sub": "level5-agent",
                        "act": {
                            "sub": "original-user@example.com"
                        }
                    }
                }
            }
        }
    }


@pytest.fixture
def two_hop_claim():
    """Two-hop delegation chain."""
    return {
        "sub": "agent1",
        "act": {
            "sub": "user@example.com"
        }
    }


class TestParseActClaim:
    """Tests for parse_act_claim() function."""

    def test_parse_act_claim_simple(self, simple_act_claim):
        """Parse single-level claim with no delegation."""
        result = parse_act_claim(simple_act_claim)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result == ["carlos@example.com"]

    def test_parse_act_claim_nested(self, nested_act_claim):
        """Parse multi-hop chain with 3 levels of delegation."""
        result = parse_act_claim(nested_act_claim)

        assert isinstance(result, list)
        assert len(result) == 3
        assert result == ["routing-agent", "k8s-agent", "carlos@example.com"]
        # Verify ordering: current actor first, original user last
        assert result[0] == "routing-agent"
        assert result[-1] == "carlos@example.com"

    def test_parse_act_claim_deep(self, deep_act_claim):
        """Parse deep delegation chain with 5+ levels."""
        result = parse_act_claim(deep_act_claim)

        assert isinstance(result, list)
        assert len(result) == 6
        assert result == [
            "level1-agent",
            "level2-agent",
            "level3-agent",
            "level4-agent",
            "level5-agent",
            "original-user@example.com"
        ]
        # Verify proper traversal from root to leaf
        assert result[0] == "level1-agent"
        assert result[-1] == "original-user@example.com"

    def test_parse_act_claim_missing_sub(self):
        """Raise ValueError when act claim missing 'sub' field."""
        invalid_claim = {"act": {"sub": "someone"}}

        with pytest.raises(ValueError, match="must contain 'sub' field"):
            parse_act_claim(invalid_claim)

    def test_parse_act_claim_invalid_type(self):
        """Raise ValueError when act claim is not a dictionary."""
        with pytest.raises(ValueError, match="must be a dictionary"):
            parse_act_claim("not-a-dict")

        with pytest.raises(ValueError, match="must be a dictionary"):
            parse_act_claim(["list", "of", "things"])

    def test_parse_act_claim_nested_missing_sub(self):
        """Raise ValueError when nested act claim missing 'sub' field."""
        invalid_claim = {
            "sub": "agent1",
            "act": {"other": "field"}
        }

        with pytest.raises(ValueError, match="nested act claim must contain 'sub' field"):
            parse_act_claim(invalid_claim)

    def test_parse_act_claim_invalid_nested_type(self):
        """Raise ValueError when nested act field is not a dictionary."""
        invalid_claim = {
            "sub": "agent1",
            "act": "not-a-dict"
        }

        with pytest.raises(ValueError, match="act field must be a dictionary"):
            parse_act_claim(invalid_claim)


class TestBuildActClaim:
    """Tests for build_act_claim() function."""

    def test_build_act_claim_new(self):
        """Build first hop claim with no previous delegation."""
        result = build_act_claim("carlos@example.com")

        assert isinstance(result, dict)
        assert "sub" in result
        assert result["sub"] == "carlos@example.com"
        assert "act" not in result
        # Verify it's a leaf node (no nested act)
        assert len(result) == 1

    def test_build_act_claim_nested(self):
        """Add new level to existing delegation chain."""
        # Build user claim (leaf)
        user_claim = build_act_claim("carlos@example.com")
        assert user_claim == {"sub": "carlos@example.com"}

        # Add k8s-agent delegating for user
        agent_claim = build_act_claim("k8s-agent", user_claim)
        assert agent_claim == {
            "sub": "k8s-agent",
            "act": {"sub": "carlos@example.com"}
        }

        # Add routing-agent delegating for k8s-agent
        routing_claim = build_act_claim("routing-agent", agent_claim)
        assert routing_claim == {
            "sub": "routing-agent",
            "act": {
                "sub": "k8s-agent",
                "act": {"sub": "carlos@example.com"}
            }
        }

        # Verify the chain parses correctly
        parsed = parse_act_claim(routing_claim)
        assert parsed == ["routing-agent", "k8s-agent", "carlos@example.com"]

    def test_build_act_claim_multi_level(self):
        """Build multi-level chain step by step."""
        claim = build_act_claim("user@example.com")
        claim = build_act_claim("agent1", claim)
        claim = build_act_claim("agent2", claim)
        claim = build_act_claim("agent3", claim)

        parsed = parse_act_claim(claim)
        assert parsed == ["agent3", "agent2", "agent1", "user@example.com"]

    def test_build_act_claim_empty_actor(self):
        """Raise ValueError when current_actor is empty."""
        with pytest.raises(ValueError, match="must be a non-empty string"):
            build_act_claim("")

    def test_build_act_claim_none_actor(self):
        """Raise ValueError when current_actor is None."""
        with pytest.raises(ValueError, match="must be a non-empty string"):
            build_act_claim(None)

    def test_build_act_claim_invalid_actor_type(self):
        """Raise ValueError when current_actor is not a string."""
        with pytest.raises(ValueError, match="must be a non-empty string"):
            build_act_claim(123)

        with pytest.raises(ValueError, match="must be a non-empty string"):
            build_act_claim(["list"])

    def test_build_act_claim_invalid_previous_type(self):
        """Raise ValueError when previous_act is not a dict or None."""
        with pytest.raises(ValueError, match="must be a dictionary or None"):
            build_act_claim("agent", "not-a-dict")

        with pytest.raises(ValueError, match="must be a dictionary or None"):
            build_act_claim("agent", ["list"])


class TestValidateDelegationChain:
    """Tests for validate_delegation_chain() function."""

    def test_validate_chain_valid(self):
        """Validate normal delegation chain."""
        # Single actor
        assert validate_delegation_chain(["user@example.com"]) is True

        # Two actors
        assert validate_delegation_chain(["agent", "user"]) is True

        # Three actors (normal case)
        assert validate_delegation_chain(["routing-agent", "k8s-agent", "carlos@example.com"]) is True

        # Five actors
        assert validate_delegation_chain(["a1", "a2", "a3", "a4", "user"]) is True

        # Ten actors (max allowed)
        assert validate_delegation_chain([f"agent{i}" for i in range(9)] + ["user"]) is True

    def test_validate_chain_too_deep(self):
        """Reject chain with more than 10 hops."""
        # 11 hops - should fail
        chain_11 = [f"agent{i}" for i in range(11)]
        assert validate_delegation_chain(chain_11) is False

        # 15 hops - should fail
        chain_15 = [f"agent{i}" for i in range(15)]
        assert validate_delegation_chain(chain_15) is False

        # Exactly 10 should pass
        chain_10 = [f"agent{i}" for i in range(10)]
        assert validate_delegation_chain(chain_10) is True

    def test_validate_chain_circular(self):
        """Reject chain where agent appears 3 or more times."""
        # Agent appears 3 times - should fail
        circular_chain = ["agent1", "agent2", "agent1", "agent2", "agent1"]
        assert validate_delegation_chain(circular_chain) is False

        # Agent appears 4 times - should fail
        circular_chain_4 = ["repeater", "agent", "repeater", "agent", "repeater", "repeater"]
        assert validate_delegation_chain(circular_chain_4) is False

        # Agent appears 2 times - should pass (allowed re-delegation)
        allowed_redelegation = ["agent1", "agent2", "agent1", "user"]
        assert validate_delegation_chain(allowed_redelegation) is True

    def test_validate_chain_empty(self):
        """Reject empty chain."""
        assert validate_delegation_chain([]) is False

    def test_validate_chain_invalid_type(self):
        """Reject chain that is not a list."""
        assert validate_delegation_chain("not-a-list") is False
        assert validate_delegation_chain({"not": "list"}) is False
        assert validate_delegation_chain(None) is False

    def test_validate_chain_invalid_elements(self):
        """Reject chain with non-string or empty elements."""
        # Non-string element
        assert validate_delegation_chain(["agent", 123, "user"]) is False

        # Empty string element
        assert validate_delegation_chain(["agent", "", "user"]) is False

        # None element
        assert validate_delegation_chain(["agent", None, "user"]) is False

    def test_validate_chain_edge_cases(self):
        """Validate edge cases for delegation chains."""
        # Exactly 2 occurrences of same actor (max allowed)
        assert validate_delegation_chain(["agent", "user", "agent"]) is True

        # Multiple actors with 2 occurrences each
        assert validate_delegation_chain(["a1", "a2", "a1", "a2"]) is True


class TestExtractOriginalUser:
    """Tests for extract_original_user() function."""

    def test_extract_original_user(self, nested_act_claim):
        """Extract leaf user from nested delegation chain."""
        result = extract_original_user(nested_act_claim)

        assert isinstance(result, str)
        assert result == "carlos@example.com"

    def test_extract_original_user_simple(self, simple_act_claim):
        """Extract user from single-level claim (no delegation)."""
        result = extract_original_user(simple_act_claim)

        assert result == "carlos@example.com"

    def test_extract_original_user_deep(self, deep_act_claim):
        """Extract user from deep delegation chain."""
        result = extract_original_user(deep_act_claim)

        assert result == "original-user@example.com"

    def test_extract_original_user_two_hop(self, two_hop_claim):
        """Extract user from two-hop chain."""
        result = extract_original_user(two_hop_claim)

        assert result == "user@example.com"

    def test_extract_original_user_invalid_claim(self):
        """Raise ValueError for invalid act claim."""
        with pytest.raises(ValueError):
            extract_original_user({})

        with pytest.raises(ValueError):
            extract_original_user({"act": {"sub": "someone"}})

    def test_extract_original_user_built_claim(self):
        """Extract user from programmatically built claim."""
        claim = build_act_claim("user@example.com")
        claim = build_act_claim("agent1", claim)
        claim = build_act_claim("agent2", claim)

        result = extract_original_user(claim)
        assert result == "user@example.com"


class TestIntegration:
    """Integration tests combining multiple functions."""

    def test_build_parse_roundtrip(self):
        """Build and parse claim should roundtrip correctly."""
        # Build a complex chain
        claim = build_act_claim("user@example.com")
        claim = build_act_claim("service-a", claim)
        claim = build_act_claim("service-b", claim)
        claim = build_act_claim("gateway", claim)

        # Parse it back
        chain = parse_act_claim(claim)

        # Verify the chain
        assert chain == ["gateway", "service-b", "service-a", "user@example.com"]

        # Validate it
        assert validate_delegation_chain(chain) is True

        # Extract original user
        user = extract_original_user(claim)
        assert user == "user@example.com"

    def test_parse_validate_workflow(self, nested_act_claim):
        """Parse claim and validate the resulting chain."""
        chain = parse_act_claim(nested_act_claim)
        is_valid = validate_delegation_chain(chain)

        assert is_valid is True
        assert len(chain) == 3

    def test_full_workflow(self):
        """Test complete workflow from build to extract."""
        # 1. Build a delegation chain
        user_claim = build_act_claim("alice@example.com")
        agent1_claim = build_act_claim("payment-processor", user_claim)
        agent2_claim = build_act_claim("api-gateway", agent1_claim)

        # 2. Parse the chain
        chain = parse_act_claim(agent2_claim)
        assert chain == ["api-gateway", "payment-processor", "alice@example.com"]

        # 3. Validate the chain
        assert validate_delegation_chain(chain) is True

        # 4. Extract original user
        original = extract_original_user(agent2_claim)
        assert original == "alice@example.com"

    def test_invalid_chain_detection(self):
        """Build an invalid chain and verify validation catches it."""
        # Build a chain that's too deep
        claim = build_act_claim("user")
        for i in range(15):
            claim = build_act_claim(f"agent{i}", claim)

        chain = parse_act_claim(claim)
        assert len(chain) == 16
        assert validate_delegation_chain(chain) is False  # Too deep
