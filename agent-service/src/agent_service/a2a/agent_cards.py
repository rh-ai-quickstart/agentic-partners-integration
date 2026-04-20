"""A2A Agent Card generation from YAML config.

Generates AgentCard instances dynamically from the 'a2a' section
of each agent's YAML config file.  No per-agent functions needed —
adding a new agent YAML with an 'a2a' block is sufficient.
"""

from typing import Any

from a2a.types import AgentCapabilities, AgentCard, AgentSkill


def create_agent_card(
    agent_name: str, config: dict[str, Any], base_url: str
) -> AgentCard:
    """Create an A2A AgentCard from an agent's YAML config.

    Reads the 'a2a' section of the config for card_name, card_description,
    and skills.  Falls back to sensible defaults when fields are missing.

    Args:
        agent_name: Agent identifier (e.g. "software-support")
        config: The agent's full YAML config dict
        base_url: Base URL where this agent's A2A endpoint is served

    Returns:
        A fully populated AgentCard
    """
    a2a_config = config.get("a2a", {})

    # Card-level metadata
    card_name = a2a_config.get(
        "card_name",
        agent_name.replace("-", " ").title() + " Agent",
    )
    card_description = a2a_config.get(
        "card_description",
        config.get("description", f"{card_name} specialist agent"),
    )

    # Build skills from config
    skills = []
    for skill_cfg in a2a_config.get("skills", []):
        skills.append(
            AgentSkill(
                id=skill_cfg["id"],
                name=skill_cfg["name"],
                description=skill_cfg.get("description", ""),
                tags=skill_cfg.get("tags", []),
                examples=skill_cfg.get("examples", []),
            )
        )

    return AgentCard(
        name=card_name,
        description=card_description.strip(),
        url=base_url,
        protocol_version="0.3.0",
        version="0.1.0",
        preferred_transport="JSONRPC",
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
            state_transition_history=True,
        ),
        skills=skills,
    )
