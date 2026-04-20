"""Kubernetes agent — loads config from YAML and provides LLM integration."""

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

import yaml

from .llm import LLMClientFactory, LLMMessage

logger = logging.getLogger(__name__)

AGENT_NAME = "kubernetes-support"


def _find_config_path() -> Path:
    """Resolve config path across dev and container environments."""
    candidates = [
        Path(__file__).parent.parent.parent / "config",
        Path("/app/config"),
        Path("config"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    tried = [str(c) for c in candidates]
    raise FileNotFoundError(f"Config directory not found in: {tried}")


def load_agent_config() -> dict[str, Any]:
    """Load the kubernetes-support-agent YAML config."""
    config_path = _find_config_path()
    agent_file = config_path / "agents" / "kubernetes-support-agent.yaml"
    if not agent_file.exists():
        raise FileNotFoundError(f"Agent config not found: {agent_file}")
    with open(agent_file) as f:
        return yaml.safe_load(f) or {}


class KubernetesAgent:
    """Kubernetes support specialist agent with LLM integration."""

    def __init__(self, config: Optional[dict[str, Any]] = None):
        self.config = config or load_agent_config()
        self.agent_name = self.config.get("name", AGENT_NAME)

        llm_backend = self.config.get("llm_backend")
        llm_model = self.config.get("llm_model")
        self.llm_client = LLMClientFactory.create_client(
            backend=llm_backend, model=llm_model
        )
        self.model: str = self.llm_client.get_model_name()
        self.system_message = self.config.get("system_message", "")

        sampling = self.config.get("sampling_params", {})
        strategy = sampling.get("strategy", {})
        self.temperature = strategy.get("temperature", 0.7)

        logger.info("Initialized KubernetesAgent: model=%s", self.model)

    async def create_response(
        self,
        messages: list[Any],
        temperature: float | None = None,
    ) -> str:
        """Create a response using the configured LLM."""
        llm_messages = []

        if self.system_message:
            llm_messages.append(LLMMessage(role="system", content=self.system_message))

        for msg in messages:
            if isinstance(msg, dict):
                llm_messages.append(
                    LLMMessage(
                        role=msg.get("role", "user"),
                        content=msg.get("content", ""),
                    )
                )
            else:
                llm_messages.append(LLMMessage(role="user", content=str(msg)))

        temp = temperature if temperature is not None else self.temperature

        try:
            response = await self.llm_client.create_completion(
                messages=llm_messages, temperature=temp
            )
            if not response.content or not response.content.strip():
                return ""
            return response.content
        except Exception as e:
            logger.error("LLM call failed: %s", e)
            return f"Error: Unable to get response from LLM: {e}"

    async def create_response_with_retry(
        self,
        messages: list[Any],
        max_retries: int = 3,
        temperature: float | None = None,
    ) -> tuple[str, bool]:
        """Create a response with retry logic for empty responses and errors."""
        default_response = (
            "I apologize, but I'm having difficulty generating a response "
            "right now. Please try again."
        )
        response = default_response

        for attempt in range(max_retries + 1):
            should_retry = False

            try:
                response = await self.create_response(messages, temperature=temperature)

                if response and response.strip():
                    if response.startswith("Error: Unable to get response"):
                        should_retry = True
                    else:
                        break
                else:
                    should_retry = True

            except Exception as e:
                logger.warning(
                    "Retry attempt %d/%d failed: %s",
                    attempt + 1,
                    max_retries + 1,
                    e,
                )
                should_retry = True

            if should_retry and attempt < max_retries:
                retry_delay = min(2**attempt, 16)
                logger.info(
                    "Retrying in %ds (attempt %d/%d)",
                    retry_delay,
                    attempt + 1,
                    max_retries + 1,
                )
                await asyncio.sleep(retry_delay)
            elif should_retry:
                response = default_response
                break

        return response, response == default_response
