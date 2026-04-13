"""A2A server setup for partner specialist agents.

Creates two A2A Starlette sub-applications (one per specialist agent)
that can be mounted on the main FastAPI app.
"""

import logging
import os

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from starlette.applications import Starlette

from .agent_cards import create_network_support_card, create_software_support_card
from .executor import SpecialistAgentExecutor

logger = logging.getLogger(__name__)


def _build_a2a_app(agent_name: str, base_url: str) -> Starlette:
    """Build a standalone A2A Starlette app for one specialist agent."""
    card_factory = {
        "software-support": create_software_support_card,
        "network-support": create_network_support_card,
    }

    if agent_name not in card_factory:
        raise ValueError(f"Unknown agent: {agent_name}")

    agent_card = card_factory[agent_name](base_url)

    handler = DefaultRequestHandler(
        agent_executor=SpecialistAgentExecutor(agent_name),
        task_store=InMemoryTaskStore(),
    )

    a2a_app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=handler,
    )

    return a2a_app.build()


def get_software_support_a2a_app() -> Starlette:
    """Build the Software Support A2A sub-application."""
    base_url = os.getenv(
        "SOFTWARE_SUPPORT_A2A_URL",
        "http://localhost:8080/a2a/software-support/",
    )
    logger.info("Building A2A app for software-support at %s", base_url)
    return _build_a2a_app("software-support", base_url)


def get_network_support_a2a_app() -> Starlette:
    """Build the Network Support A2A sub-application."""
    base_url = os.getenv(
        "NETWORK_SUPPORT_A2A_URL",
        "http://localhost:8080/a2a/network-support/",
    )
    logger.info("Building A2A app for network-support at %s", base_url)
    return _build_a2a_app("network-support", base_url)
