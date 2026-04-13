"""
OPA (Open Policy Agent) client for authorization decisions.

Queries an OPA server for policy decisions using the permission
intersection model: Effective = User Departments ∩ Agent Capabilities.
"""

import os
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
import structlog

logger = structlog.get_logger()

OPA_URL: str = os.getenv("OPA_URL", "http://localhost:8181")
OPA_POLICY_PATH: str = "v1/data/partner/authorization/decision"


@dataclass
class Delegation:
    """Delegation context: user delegates access to an agent."""

    user_spiffe_id: str
    agent_spiffe_id: str
    user_departments: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_spiffe_id": self.user_spiffe_id,
            "agent_spiffe_id": self.agent_spiffe_id,
            "user_departments": self.user_departments,
        }


@dataclass
class OPADecision:
    """Result of an OPA policy evaluation."""

    allow: bool
    reason: str
    effective_departments: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


async def check_agent_authorization(
    caller_spiffe_id: str,
    agent_name: str,
    delegation: Optional[Delegation] = None,
) -> OPADecision:
    """Query OPA to check if an agent invocation is authorized.

    Args:
        caller_spiffe_id: SPIFFE ID of the calling service/user
        agent_name: Name of the target agent (e.g. "software-support")
        delegation: Delegation context (user -> agent), if applicable

    Returns:
        OPADecision with allow/deny, reason, and effective departments
    """
    opa_input: dict[str, Any] = {
        "caller_spiffe_id": caller_spiffe_id,
        "agent_name": agent_name,
    }

    if delegation:
        opa_input["delegation"] = delegation.to_dict()

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{OPA_URL}/{OPA_POLICY_PATH}",
                json={"input": opa_input},
            )
            response.raise_for_status()
            result = response.json()

        decision = result.get("result", {})

        return OPADecision(
            allow=decision.get("allow", False),
            reason=decision.get("reason", "No policy decision"),
            effective_departments=decision.get("effective_departments", []),
            details=decision,
        )

    except httpx.ConnectError:
        logger.warning(
            "OPA service unreachable, denying by default",
            opa_url=OPA_URL,
            agent_name=agent_name,
        )
        return OPADecision(
            allow=False,
            reason="Policy engine unavailable — access denied by default",
        )
    except Exception as e:
        logger.error(
            "OPA query failed",
            error=str(e),
            caller=caller_spiffe_id,
            agent=agent_name,
        )
        return OPADecision(
            allow=False,
            reason=f"Policy evaluation error: {e}",
        )


async def get_user_departments_from_opa(user_email: str) -> list[str]:
    """Query OPA for a user's department list (fallback data).

    Used when user departments aren't available from JWT claims or DB.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{OPA_URL}/v1/data/partner/authorization/user_departments_fallback",
                json={"input": {"user_email": user_email}},
            )
            response.raise_for_status()
            result = response.json()

        fallback_map = result.get("result", {})
        return fallback_map.get(user_email, [])

    except Exception as e:
        logger.warning(
            "Failed to get user departments from OPA",
            user_email=user_email,
            error=str(e),
        )
        return []
