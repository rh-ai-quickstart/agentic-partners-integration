"""A2A server setup for partner specialist agents.

Dynamically discovers specialist agents from YAML configs and creates
an A2A Starlette sub-application for each one.  Adding a new agent
YAML with departments and an 'a2a' block is sufficient — no code
changes required.
"""

import logging
import os
from typing import Any

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from starlette.applications import Starlette

from .agent_cards import create_agent_card
from .executor import SpecialistAgentExecutor

logger = logging.getLogger(__name__)


def _build_a2a_app(agent_name: str, config: dict[str, Any], base_url: str) -> Starlette:
    """Build a standalone A2A Starlette app for one specialist agent.

    Args:
        agent_name: Agent identifier (e.g. "software-support")
        config: The agent's full YAML config dict (used for card generation)
        base_url: Base URL where this agent's A2A endpoint is served
    """
    agent_card = create_agent_card(agent_name, config, base_url)

    handler = DefaultRequestHandler(
        agent_executor=SpecialistAgentExecutor(agent_name),
        task_store=InMemoryTaskStore(),
    )

    a2a_app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=handler,
    )

    return a2a_app.build()


def get_a2a_app(agent_name: str, config: dict[str, Any]) -> Starlette:
    """Build an A2A sub-application for a specialist agent.

    The base URL is read from the environment variable
    ``<AGENT_NAME>_A2A_URL`` (uppercased, hyphens replaced with
    underscores), falling back to localhost.

    Args:
        agent_name: Agent identifier (e.g. "software-support")
        config: The agent's full YAML config dict
    """
    env_key = agent_name.upper().replace("-", "_") + "_A2A_URL"
    base_url = os.getenv(
        env_key,
        f"http://localhost:8080/a2a/{agent_name}/",
    )
    logger.info("Building A2A app for %s at %s", agent_name, base_url)
    return _build_a2a_app(agent_name, config, base_url)
