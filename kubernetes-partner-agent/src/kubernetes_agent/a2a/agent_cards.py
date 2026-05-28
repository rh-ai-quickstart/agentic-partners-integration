"""A2A Agent Card generation from YAML config."""

from typing import Any

from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill


def create_agent_card(
    agent_name: str, config: dict[str, Any], base_url: str
) -> AgentCard:
    """Create an A2A AgentCard from the agent's YAML config."""
    a2a_config = config.get("a2a", {})

    card_name = a2a_config.get(
        "card_name",
        agent_name.replace("-", " ").title() + " Agent",
    )
    card_description = a2a_config.get(
        "card_description",
        config.get("description", f"{card_name} specialist agent"),
    )

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
        version="0.1.0",
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
        ),
        supported_interfaces=[
            AgentInterface(
                url=base_url,
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
        ],
        skills=skills,
    )
