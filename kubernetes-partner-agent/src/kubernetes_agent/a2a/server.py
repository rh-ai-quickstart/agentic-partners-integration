"""A2A server setup for the Kubernetes partner agent."""

import logging
import os
from typing import Any

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from starlette.applications import Starlette
from starlette.routing import Route

from .agent_cards import create_agent_card
from .executor import KubernetesAgentExecutor

logger = logging.getLogger(__name__)


def get_a2a_app(agent_name: str, config: dict[str, Any]) -> Starlette:
    """Build an A2A Starlette sub-application for the Kubernetes agent."""
    env_key = agent_name.upper().replace("-", "_") + "_A2A_URL"
    base_url = os.getenv(
        env_key,
        f"http://localhost:8080/a2a/{agent_name}/",
    )

    agent_card = create_agent_card(agent_name, config, base_url)

    handler = DefaultRequestHandler(
        agent_executor=KubernetesAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )

    routes: list[Route] = []
    routes.extend(create_agent_card_routes(agent_card))
    routes.extend(create_jsonrpc_routes(handler, rpc_url="/"))

    logger.info("Built A2A app for %s at %s", agent_name, base_url)
    return Starlette(routes=routes)
