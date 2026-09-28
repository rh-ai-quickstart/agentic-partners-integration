"""Tests for shared_models.policy_client module."""

from unittest.mock import MagicMock, mock_open, patch

import pytest

from shared_models.policy_client import (
    Delegation,
    PolicyDecision,
    _get_service_names,
    _get_trust_domain,
    _load_capabilities,
    _resolve_user_departments,
    check_agent_authorization,
    get_agent_capabilities,
    get_user_departments_fallback,
    parse_spiffe_name,
    parse_spiffe_type,
    register_dynamic_agent,
    reload_capabilities,
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


# ── _load_capabilities ──────────────────────────────────────────────────────

class TestLoadCapabilities:
    def test_loads_from_yaml_file(self, tmp_path):
        """When YAML file exists, loads agent capabilities from it."""
        import shared_models.policy_client as mod

        yaml_content = """\
agent_capabilities:
  test-agent:
    - network
    - software
valid_departments:
  - network
  - software
service_names:
  - test-service
trust_domain: test.example.com
"""
        yaml_file = tmp_path / "caps.yaml"
        yaml_file.write_text(yaml_content)

        original_path = mod._CAPABILITIES_PATH
        try:
            mod._CAPABILITIES_PATH = yaml_file
            result = _load_capabilities()
            assert "test-agent" in result.get("agent_capabilities", {})
            assert result["trust_domain"] == "test.example.com"
        finally:
            mod._CAPABILITIES_PATH = original_path

    def test_loads_from_yaml_with_data_key(self, tmp_path):
        """Supports Praxis format with top-level 'data:' key."""
        import shared_models.policy_client as mod

        yaml_content = """\
data:
  agent_capabilities:
    wrapped-agent:
      - admin
  valid_departments:
    - admin
"""
        yaml_file = tmp_path / "caps.yaml"
        yaml_file.write_text(yaml_content)

        original_path = mod._CAPABILITIES_PATH
        try:
            mod._CAPABILITIES_PATH = yaml_file
            result = _load_capabilities()
            assert "wrapped-agent" in result.get("agent_capabilities", {})
        finally:
            mod._CAPABILITIES_PATH = original_path

    def test_falls_back_on_exception(self, tmp_path):
        """Falls back to defaults when YAML loading fails."""
        import shared_models.policy_client as mod

        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text("{{invalid yaml content")

        original_path = mod._CAPABILITIES_PATH
        try:
            mod._CAPABILITIES_PATH = yaml_file
            result = _load_capabilities()
            # Should contain default capabilities
            assert "routing-agent" in result.get("agent_capabilities", {})
        finally:
            mod._CAPABILITIES_PATH = original_path


# ── get_agent_capabilities with dynamic agents ─────────────────────────────

class TestGetAgentCapabilities:
    def test_includes_dynamic_agents(self):
        import shared_models.policy_client as mod
        original_dynamic = mod._dynamic_capabilities.copy()
        try:
            mod._dynamic_capabilities["dynamic-test"] = ["software", "network"]
            caps = get_agent_capabilities()
            assert "dynamic-test" in caps
            assert "software" in caps["dynamic-test"]
        finally:
            mod._dynamic_capabilities.clear()
            mod._dynamic_capabilities.update(original_dynamic)

    def test_dynamic_agent_filtered_by_valid_departments(self):
        import shared_models.policy_client as mod
        original_dynamic = mod._dynamic_capabilities.copy()
        try:
            # "invalid-dept" is not in valid departments
            mod._dynamic_capabilities["filtered-agent"] = ["software", "invalid-dept"]
            caps = get_agent_capabilities()
            assert "filtered-agent" in caps
            assert "software" in caps["filtered-agent"]
            assert "invalid-dept" not in caps["filtered-agent"]
        finally:
            mod._dynamic_capabilities.clear()
            mod._dynamic_capabilities.update(original_dynamic)

    def test_dynamic_agent_does_not_override_static(self):
        """Dynamic agents with same name as static agents are NOT merged."""
        import shared_models.policy_client as mod
        original_dynamic = mod._dynamic_capabilities.copy()
        try:
            mod._dynamic_capabilities["routing-agent"] = ["software"]
            caps = get_agent_capabilities()
            # Static "routing-agent" should have all four departments, not just ["software"]
            assert len(caps["routing-agent"]) > 1
        finally:
            mod._dynamic_capabilities.clear()
            mod._dynamic_capabilities.update(original_dynamic)


# ── register_dynamic_agent ──────────────────────────────────────────────────

class TestRegisterDynamicAgent:
    def test_registers_valid_departments_only(self):
        import shared_models.policy_client as mod
        original_dynamic = mod._dynamic_capabilities.copy()
        try:
            register_dynamic_agent("new-agent", ["software", "bogus-dept"])
            assert "new-agent" in mod._dynamic_capabilities
            assert mod._dynamic_capabilities["new-agent"] == ["software"]
        finally:
            mod._dynamic_capabilities.clear()
            mod._dynamic_capabilities.update(original_dynamic)


# ── reload_capabilities ─────────────────────────────────────────────────────

class TestReloadCapabilities:
    def test_reloads_config(self):
        import shared_models.policy_client as mod
        original_config = mod._config.copy()
        try:
            reload_capabilities()
            # After reload, config should still have defaults (no file on disk)
            assert "agent_capabilities" in mod._config
        finally:
            mod._config = original_config


# ── SPIFFE ID parsing ───────────────────────────────────────────────────────

class TestParseSpiffeName:
    def test_long_spiffe_id(self):
        name = parse_spiffe_name("spiffe://domain.com/service/my-svc")
        assert name == "my-svc"

    def test_short_spiffe_id_returns_none(self):
        """SPIFFE IDs with fewer than 4 parts return None."""
        # "spiffe://domain.com" splits to ["spiffe:", "", "domain.com"] -> 3 parts
        name = parse_spiffe_name("spiffe://domain.com")
        assert name is None


class TestParseSpiffeType:
    def test_service_type(self):
        t = parse_spiffe_type("spiffe://partner.example.com/service/request-manager")
        assert t == "service"

    def test_user_type(self):
        t = parse_spiffe_type("spiffe://partner.example.com/user/alice")
        assert t == "user"

    def test_agent_type(self):
        # Use an agent name that is NOT in the default service_names list
        t = parse_spiffe_type("spiffe://partner.example.com/agent/my-custom-agent")
        assert t == "agent"

    def test_none_for_unknown(self):
        t = parse_spiffe_type("spiffe://partner.example.com/unknown/thing")
        assert t is None

    def test_agent_service_not_treated_as_agent(self):
        """agent-service in the path should return 'service', not 'agent'."""
        t = parse_spiffe_type("spiffe://partner.example.com/service/agent-service")
        assert t == "service"


# ── _get_trust_domain / _get_service_names ──────────────────────────────────

class TestConfigAccessors:
    def test_get_trust_domain(self):
        domain = _get_trust_domain()
        assert isinstance(domain, str)
        assert len(domain) > 0

    def test_get_service_names(self):
        names = _get_service_names()
        assert isinstance(names, set)
        assert len(names) > 0


# ── Delegation.to_dict with optional fields ─────────────────────────────────

class TestDelegationToDict:
    def test_with_act_claim(self):
        d = Delegation(
            user_spiffe_id="spiffe://example.com/user/alice",
            agent_spiffe_id="spiffe://example.com/agent/support",
            act_claim={"sub": "agent-1"},
        )
        result = d.to_dict()
        assert result["act_claim"] == {"sub": "agent-1"}

    def test_with_delegation_chain(self):
        d = Delegation(
            user_spiffe_id="spiffe://example.com/user/alice",
            agent_spiffe_id="spiffe://example.com/agent/support",
            delegation_chain=["agent-1", "agent-2"],
        )
        result = d.to_dict()
        assert result["delegation_chain"] == ["agent-1", "agent-2"]

    def test_with_original_user_email(self):
        d = Delegation(
            user_spiffe_id="spiffe://example.com/user/alice",
            agent_spiffe_id="spiffe://example.com/agent/support",
            original_user_email="alice@example.com",
        )
        result = d.to_dict()
        assert result["original_user_email"] == "alice@example.com"

    def test_with_all_optional_fields(self):
        d = Delegation(
            user_spiffe_id="spiffe://example.com/user/alice",
            agent_spiffe_id="spiffe://example.com/agent/support",
            user_departments=["software"],
            act_claim={"sub": "agent-1"},
            delegation_chain=["agent-1"],
            original_user_email="alice@example.com",
        )
        result = d.to_dict()
        assert "act_claim" in result
        assert "delegation_chain" in result
        assert "original_user_email" in result


# ── check_agent_authorization — additional rules ────────────────────────────

@pytest.mark.asyncio
class TestCheckAgentAuthorizationAdditional:
    async def test_default_deny_no_matching_rule(self):
        """Default deny when caller type does not match any rule."""
        # A SPIFFE ID that isn't recognized as service, user, or agent
        # and has no delegation
        decision = await check_agent_authorization(
            "spiffe://partner.example.com/unknown/thing",
            "software-support",
        )
        assert decision.allow is False
        assert "No matching policy rule" in decision.reason

    async def test_exception_handling(self):
        """Policy evaluation errors are caught and returned as deny."""
        with patch(
            "shared_models.policy_client.parse_spiffe_type",
            side_effect=Exception("boom"),
        ):
            decision = await check_agent_authorization(
                "spiffe://partner.example.com/service/test",
                "software-support",
            )
        assert decision.allow is False
        assert "Policy evaluation error" in decision.reason

    async def test_direct_user_access_allowed(self):
        """Rule 2: Direct user access is allowed."""
        decision = await check_agent_authorization(
            "spiffe://partner.example.com/user/alice",
            "software-support",
        )
        assert decision.allow is True
        assert "Direct user access" in decision.reason


# ── _resolve_user_departments ───────────────────────────────────────────────

class TestResolveUserDepartments:
    def test_returns_departments_from_delegation(self):
        d = Delegation(
            user_spiffe_id="spiffe://example.com/user/alice",
            agent_spiffe_id="spiffe://example.com/agent/support",
            user_departments=["software", "network"],
        )
        result = _resolve_user_departments(d)
        assert result == ["software", "network"]

    def test_returns_empty_list_when_no_departments(self):
        d = Delegation(
            user_spiffe_id="spiffe://example.com/user/alice",
            agent_spiffe_id="spiffe://example.com/agent/support",
        )
        result = _resolve_user_departments(d)
        assert result == []
