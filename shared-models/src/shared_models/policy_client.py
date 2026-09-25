"""
In-process policy evaluator for authorization decisions.

Evaluates authorization rules in-process using the permission
intersection model:

    Effective = User Departments ∩ Agent Capabilities

Policy file: policies/policy.yaml (APL format)
Data file:   policies/agent_capabilities.yaml
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

import structlog
import yaml

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Load agent capabilities from YAML at module level
# ---------------------------------------------------------------------------

_CAPABILITIES_PATH = Path(
    os.getenv(
        "POLICY_CAPABILITIES_PATH",
        "/etc/praxis/agent_capabilities.yaml",
    )
)

_DEFAULT_AGENT_CAPABILITIES: dict[str, list[str]] = {
    "routing-agent": ["admin", "kubernetes", "network", "software"],
    "kubernetes-support": ["kubernetes"],
    "network-support": ["network"],
    "software-support": ["software"],
}

_DEFAULT_VALID_DEPARTMENTS: list[str] = [
    "admin",
    "kubernetes",
    "network",
    "software",
]

_DEFAULT_SERVICE_NAMES: list[str] = [
    "request-manager",
    "agent-service",
    "kubernetes-agent",
    "aro-agent",
    "rag-api",
]

_DEFAULT_TRUST_DOMAIN: str = "partner.example.com"


def _load_capabilities() -> dict[str, Any]:
    """Load agent capabilities YAML, falling back to hardcoded defaults.

    Supports both flat format (agent_capabilities at top level) and
    Praxis attribute-file format (everything nested under a ``data:`` key).
    """
    try:
        if _CAPABILITIES_PATH.exists():
            with open(_CAPABILITIES_PATH) as f:
                raw = yaml.safe_load(f) or {}
            data = raw.get("data", raw)
            logger.debug(
                "Loaded agent capabilities from YAML",
                path=str(_CAPABILITIES_PATH),
                agents=list(data.get("agent_capabilities", {}).keys()),
            )
            return data
    except Exception as e:
        logger.warning(
            "Failed to load agent capabilities YAML, using defaults",
            path=str(_CAPABILITIES_PATH),
            error=str(e),
        )

    return {
        "agent_capabilities": _DEFAULT_AGENT_CAPABILITIES,
        "valid_departments": _DEFAULT_VALID_DEPARTMENTS,
        "service_names": _DEFAULT_SERVICE_NAMES,
        "trust_domain": _DEFAULT_TRUST_DOMAIN,
    }


# Module-level config, loaded once at import time.
_config: dict[str, Any] = _load_capabilities()

# Dynamic agent capabilities added at runtime (e.g. via DCR registration).
# Keys are agent names, values are lists of departments.
_dynamic_capabilities: dict[str, list[str]] = {}


def get_agent_capabilities() -> dict[str, list[str]]:
    """Return the merged static + dynamic agent capabilities."""
    caps: dict[str, list[str]] = dict(_config.get("agent_capabilities", {}))
    valid = set(_config.get("valid_departments", _DEFAULT_VALID_DEPARTMENTS))

    for name, depts in _dynamic_capabilities.items():
        if name not in caps:
            # Filter to valid departments only
            caps[name] = [d for d in depts if d in valid]

    return caps


def register_dynamic_agent(name: str, departments: list[str]) -> None:
    """Register a dynamically discovered agent's capabilities.

    Only departments present in the valid_departments allowlist are kept.
    """
    valid = set(_config.get("valid_departments", _DEFAULT_VALID_DEPARTMENTS))
    filtered = [d for d in departments if d in valid]
    _dynamic_capabilities[name] = filtered
    logger.info(
        "Registered dynamic agent capabilities",
        agent=name,
        departments=filtered,
    )


def reload_capabilities() -> None:
    """Reload the agent capabilities YAML from disk.

    Useful after ``make sync-agents`` regenerates the file.
    """
    global _config
    _config = _load_capabilities()
    logger.info("Reloaded agent capabilities")


# ---------------------------------------------------------------------------
# SPIFFE ID parsing
# ---------------------------------------------------------------------------

def _get_service_names() -> set[str]:
    return set(_config.get("service_names", _DEFAULT_SERVICE_NAMES))


def _get_trust_domain() -> str:
    return _config.get("trust_domain", _DEFAULT_TRUST_DOMAIN)


def parse_spiffe_name(spiffe_id: str) -> Optional[str]:
    """Extract the service/entity name from a SPIFFE ID.

    ``spiffe://partner.example.com/service-name`` -> ``service-name``
    """
    parts = spiffe_id.split("/")
    if len(parts) >= 4:
        return parts[-1]
    return None


def parse_spiffe_type(spiffe_id: str) -> Optional[str]:
    """Determine the entity type from a SPIFFE ID.

    Returns one of: ``"service"``, ``"user"``, ``"agent"``, or ``None``.
    """
    name = parse_spiffe_name(spiffe_id)
    if name and name in _get_service_names():
        return "service"

    if "/user/" in spiffe_id:
        return "user"

    if "/agent/" in spiffe_id and "agent-service" not in spiffe_id:
        return "agent"

    return None


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Delegation:
    """Delegation context: user delegates access to an agent.

    The act_claim field contains the RFC 8693 'act' (actor) claim from JWT tokens,
    providing cryptographic proof of the delegation chain. This replaces the
    trust-on-first-use model of HTTP headers with verifiable JWT claims.
    """

    user_spiffe_id: str
    agent_spiffe_id: str
    user_departments: list[str] = field(default_factory=list)
    act_claim: Optional[dict[str, Any]] = None
    delegation_chain: Optional[List[str]] = None
    original_user_email: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "user_spiffe_id": self.user_spiffe_id,
            "agent_spiffe_id": self.agent_spiffe_id,
            "user_departments": self.user_departments,
        }

        if self.act_claim:
            result["act_claim"] = self.act_claim

        if self.delegation_chain:
            result["delegation_chain"] = self.delegation_chain

        if self.original_user_email:
            result["original_user_email"] = self.original_user_email

        return result


@dataclass
class PolicyDecision:
    """Result of an in-process policy evaluation."""

    allow: bool
    reason: str
    effective_departments: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)



# ---------------------------------------------------------------------------
# Authorization evaluation (in-process, no HTTP)
# ---------------------------------------------------------------------------

async def check_agent_authorization(
    caller_spiffe_id: str,
    agent_name: str,
    delegation: Optional[Delegation] = None,
) -> PolicyDecision:
    """Evaluate whether an agent invocation is authorized.

    Six rules, evaluated in-process:

    1. Service-to-service without delegation -> allow
    2. Direct user access -> allow
    3. Delegated access with non-empty department intersection -> allow
    4. Delegated access with empty intersection -> deny
    5. Autonomous agent without delegation -> deny
    6. Unknown agent -> deny

    Args:
        caller_spiffe_id: SPIFFE ID of the calling service/user
        agent_name: Name of the target agent (e.g. "software-support")
        delegation: Delegation context (user -> agent), if applicable

    Returns:
        PolicyDecision with allow/deny, reason, and effective departments
    """
    try:
        caller_type = parse_spiffe_type(caller_spiffe_id)
        caps = get_agent_capabilities()

        # Rule 6: Unknown agent — agent_name not in capabilities map
        if delegation and agent_name not in caps:
            logger.info(
                "Policy denied: unknown agent",
                caller=caller_spiffe_id,
                agent=agent_name,
            )
            return PolicyDecision(
                allow=False,
                reason=f"Unknown agent: {agent_name}",
                details={"rule": 6, "agent_name": agent_name},
            )

        # Rule 1: Service-to-service without delegation — allowed
        if caller_type == "service" and not delegation:
            return PolicyDecision(
                allow=True,
                reason="Service-to-service call allowed",
                details={"rule": 1},
            )

        # Rule 2: Direct user access — allowed
        if caller_type == "user":
            return PolicyDecision(
                allow=True,
                reason="Direct user access allowed",
                details={"rule": 2},
            )

        # Rule 5: Autonomous agent without delegation — denied
        if caller_type == "agent" and not delegation:
            logger.info(
                "Policy denied: autonomous agent without delegation",
                caller=caller_spiffe_id,
                agent=agent_name,
            )
            return PolicyDecision(
                allow=False,
                reason="Autonomous agent access denied — delegation context required",
                details={"rule": 5},
            )

        # Rules 3 & 4: Delegated access — compute permission intersection
        if caller_type == "service" and delegation:
            user_depts = _resolve_user_departments(delegation)
            agent_caps = caps.get(agent_name, [])

            effective = sorted(set(user_depts) & set(agent_caps))

            if effective:
                # Rule 3: Non-empty intersection — allow
                return PolicyDecision(
                    allow=True,
                    reason=f"Delegated access granted — effective departments: {effective}",
                    effective_departments=effective,
                    details={"rule": 3, "user_departments": user_depts, "agent_capabilities": agent_caps},
                )
            else:
                # Rule 4: Empty intersection — deny
                logger.info(
                    "Policy denied: empty department intersection",
                    caller=caller_spiffe_id,
                    agent=agent_name,
                    user_departments=user_depts,
                    agent_capabilities=agent_caps,
                )
                return PolicyDecision(
                    allow=False,
                    reason=(
                        f"No overlapping departments between user {user_depts} "
                        f"and agent {agent_name} capabilities {agent_caps}"
                    ),
                    details={"rule": 4, "user_departments": user_depts, "agent_capabilities": agent_caps},
                )

        # Default deny — no matching rule
        logger.warning(
            "Policy denied: no matching rule",
            caller=caller_spiffe_id,
            caller_type=caller_type,
            agent=agent_name,
            has_delegation=delegation is not None,
        )
        return PolicyDecision(
            allow=False,
            reason="No matching policy rule",
        )

    except Exception as e:
        logger.error(
            "Policy evaluation failed",
            error=str(e),
            caller=caller_spiffe_id,
            agent=agent_name,
        )
        return PolicyDecision(
            allow=False,
            reason=f"Policy evaluation error: {e}",
        )


def _resolve_user_departments(delegation: Delegation) -> list[str]:
    """Resolve user departments from the delegation context.

    Prefers departments explicitly set on the delegation object (sourced
    from JWT claims or the database).  Falls back to an empty list since
    all users are managed in Keycloak.
    """
    if delegation.user_departments:
        return list(delegation.user_departments)

    return []


# ---------------------------------------------------------------------------
# Fallback department lookup
# ---------------------------------------------------------------------------

_user_departments_fallback: dict[str, list[str]] = {}


async def get_user_departments_fallback(user_email: str) -> list[str]:
    """Return departments for a user from the fallback map.

    The fallback map is intentionally empty since all users
    are managed in Keycloak.
    """
    return list(_user_departments_fallback.get(user_email, []))
