"""Agent configuration and LLM integration.

Loads agent definitions from YAML config files and provides
LLM-backed response generation with retry logic.
"""

import asyncio
import os
from typing import Any, Dict, Optional

import yaml
from agent_service.llm import LLMClientFactory, LLMMessage
from shared_models import configure_logging

from .config_utils import load_config_from_path, resolve_agent_service_path

logger = configure_logging("agent-service")


class Agent:
    """
    Agent that loads configuration from agent YAML files and provides LLM integration.
    """

    def __init__(
        self,
        agent_name: str,
        config: dict[str, Any],
        global_config: dict[str, Any] | None = None,
        system_message: str | None = None,
    ):
        """Initialize agent with provided configuration."""
        self.agent_name = agent_name
        self.config = config
        self.global_config = global_config or {}

        # Initialize LLM client using factory (supports OpenAI, Gemini, Ollama)
        llm_backend = self.config.get("llm_backend") or self.global_config.get(
            "llm_backend"
        )
        llm_model = self.config.get("llm_model") or self.global_config.get("llm_model")

        self.llm_client = LLMClientFactory.create_client(
            backend=llm_backend, model=llm_model
        )

        # Model name is now managed by the LLM client
        self.model: str = self.llm_client.get_model_name()

        self.default_response_config = self._get_response_config()
        self.system_message = system_message or self._get_default_system_message()

        logger.info(
            "Initialized Agent",
            agent_name=agent_name,
            model="deferred" if self.model is None else self.model,
        )

    def _get_response_config(self) -> dict[str, Any]:
        """Get response configuration from agent config with defaults."""
        base_config = {
            "stream": False,
            "temperature": 0.7,
        }

        if self.config and "sampling_params" in self.config:
            sampling_params = self.config["sampling_params"]
            if "strategy" in sampling_params:
                strategy = sampling_params["strategy"]
                if "temperature" in strategy:
                    base_config["temperature"] = strategy["temperature"]

        return base_config

    def _get_default_system_message(self) -> str:
        """Get default system message for the agent."""
        if self.config and self.config.get("system_message"):
            message = self.config["system_message"]
            return str(message) if message is not None else ""

        return ""

    async def create_response_with_retry(
        self,
        messages: list[Any],
        max_retries: int = 3,
        temperature: float | None = None,
        token_context: str | None = None,
    ) -> tuple[str, bool]:
        """Create a response with retry logic for empty responses and errors."""
        default_response = "I apologize, but I'm having difficulty generating a response right now. Please try again."
        response = default_response
        last_error = None

        for attempt in range(max_retries + 1):  # +1 for initial attempt plus retries
            should_retry = False
            retry_reason = None

            try:
                response = await self.create_response(
                    messages,
                    temperature=temperature,
                    token_context=token_context,
                )

                # Check if response is empty or contains error
                if response and response.strip():
                    # Check if it's an error message that we should retry
                    if response.startswith("Error: Unable to get response"):
                        last_error = response
                        should_retry = True
                        retry_reason = "error response"
                    else:
                        # Valid response, break out of retry loop
                        break
                else:
                    # Empty response detected
                    should_retry = True
                    retry_reason = "empty response"

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "Exception on retry attempt",
                    attempt=attempt + 1,
                    max_retries=max_retries + 1,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                should_retry = True
                retry_reason = "exception"

            # Consolidated retry logic
            if should_retry:
                if attempt < max_retries:
                    retry_delay = min(
                        2**attempt, 16
                    )  # Exponential backoff: 1s, 2s, 4s, 8s, 16s max
                    logger.info(
                        "Retrying after failure",
                        reason=retry_reason,
                        attempt=attempt + 1,
                        max_attempts=max_retries + 1,
                        retry_delay=retry_delay,
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    logger.warning(
                        "All retry attempts failed",
                        max_attempts=max_retries + 1,
                        last_error=last_error or "Empty response",
                    )
                    response = default_response
                    break

        response_failed = response == default_response
        return response, response_failed

    async def create_response(
        self,
        messages: list[Any],
        temperature: float | None = None,
        token_context: str | None = None,
    ) -> str:
        """Create a response using LLM client (OpenAI, Gemini, or Ollama).

        Args:
            messages: List of user/assistant messages
            temperature: Optional temperature override
            token_context: Optional context for token counting

        Returns:
            Generated response text
        """
        try:
            # Build message list: system message(s) + conversation
            llm_messages = []

            # Add main system message
            if self.system_message:
                llm_messages.append(
                    LLMMessage(role="system", content=self.system_message)
                )

            # Add the conversation messages
            for msg in messages:
                if isinstance(msg, dict):
                    llm_messages.append(
                        LLMMessage(
                            role=msg.get("role", "user"), content=msg.get("content", "")
                        )
                    )
                else:
                    # Handle other message formats
                    llm_messages.append(LLMMessage(role="user", content=str(msg)))

            # Get temperature from config or parameter
            temp = (
                temperature
                if temperature is not None
                else self.default_response_config.get("temperature", 0.7)
            )

            logger.debug(
                "Calling LLM",
                agent_name=self.agent_name,
                model=self.model,
                message_count=len(llm_messages),
                temperature=temp,
            )

            # Call LLM via our abstraction layer
            response = await self.llm_client.create_completion(
                messages=llm_messages,
                temperature=temp,
            )

            response_text = response.content

            # Token counting (optional)
            try:
                from .token_counter import TokenCounter

                context = token_context or "chat_agent"
                counter = TokenCounter()
                counter.add_tokens(
                    input_tokens=response.usage.get("prompt_tokens", 0),
                    output_tokens=response.usage.get("completion_tokens", 0),
                    context=context,
                )
            except (ImportError, Exception) as e:
                logger.debug(
                    "Token counting not available",
                    error=str(e),
                    error_type=type(e).__name__,
                )

            # Check for empty response
            if not response_text or not response_text.strip():
                logger.warning(
                    "Empty response from LLM",
                    agent_name=self.agent_name,
                    model=self.model,
                )
                return ""  # Return empty to trigger retry logic

            logger.debug(
                "LLM response received",
                agent_name=self.agent_name,
                response_length=len(response_text),
                total_tokens=response.total_tokens,
            )

            return response_text

        except Exception as e:
            logger.error(
                "Error calling LLM",
                agent_name=self.agent_name,
                model=self.model,
                error=str(e),
                error_type=type(e).__name__,
            )
            return f"Error: Unable to get response from LLM: {e}"


class AgentManager:
    """Manages multiple agent instances for the application.

    Loads agent definitions from YAML config files in config/agents/.
    Each YAML file is the single source of truth for that agent's:
    - LLM configuration (backend, model, system message)
    - Departments (authorization scope)
    - Description (used by routing-agent for delegation decisions)
    - A2A card metadata (skills, tags, examples)
    """

    def __init__(self) -> None:
        self.agents_dict: dict[str, Agent] = {}
        self._agent_configs: dict[str, dict[str, Any]] = {}

        # Load the configuration using centralized path resolution
        try:
            config_path = resolve_agent_service_path("config")
            logger.info("AgentManager found config", config_path=str(config_path))
        except FileNotFoundError as e:
            logger.error(
                "AgentManager config not found",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise

        agent_configs = load_config_from_path(config_path)

        # Load global configuration (config.yaml)
        global_config_path = config_path / "config.yaml"
        global_config: Dict[str, Any] = {}
        if global_config_path.exists():
            with open(global_config_path, "r") as f:
                global_config = yaml.safe_load(f) or {}

        # Create agents for each entry in the configuration
        agents_list = agent_configs.get("agents", [])
        for agent_config in agents_list:
            agent_name = agent_config.get("name")
            if agent_name:
                self.agents_dict[agent_name] = Agent(
                    agent_name, agent_config, global_config
                )
                self._agent_configs[agent_name] = agent_config

    def get_agent(self, agent_id: str) -> Agent:
        """Get an agent by ID, returning default if not found."""
        if agent_id in self.agents_dict:
            return self.agents_dict[agent_id]

        # If agent_id not found, return first available agent
        if self.agents_dict:
            return next(iter(self.agents_dict.values()))

        # If no agents loaded, raise an error
        raise ValueError(
            f"No agent found with ID '{agent_id}' and no agents are loaded"
        )

    def get_agent_config(self, agent_name: str) -> dict[str, Any]:
        """Get the raw YAML config for an agent."""
        return self._agent_configs.get(agent_name, {})

    def get_specialist_agents(self) -> dict[str, dict[str, Any]]:
        """Get configs for all specialist agents (excludes routing-agent).

        Returns:
            Dict mapping agent name to its YAML config, for agents that
            have a 'departments' field (i.e. specialist agents).
        """
        return {
            name: config
            for name, config in self._agent_configs.items()
            if name != "routing-agent" and config.get("departments")
        }

    def get_agent_dept_map(self) -> dict[str, list[str]]:
        """Get department mapping for all specialist agents.

        Returns:
            Dict mapping agent name to list of departments, e.g.
            {"software-support": ["software"], "network-support": ["network"]}
        """
        return {
            name: config.get("departments", [])
            for name, config in self.get_specialist_agents().items()
        }

    def get_agent_descriptions(self) -> dict[str, str]:
        """Get routing descriptions for all specialist agents.

        Returns:
            Dict mapping agent name to description string, e.g.
            {"software-support": "Handles software issues, bugs, ..."}
        """
        return {
            name: config.get("description", "")
            for name, config in self.get_specialist_agents().items()
        }

    def get_agent_endpoints(self) -> dict[str, str]:
        """Get invoke endpoint URLs for all specialist agents.

        Returns:
            Dict mapping agent name to its full invoke URL.
            Agents with an explicit ``endpoint`` field in their YAML config
            use that URL; others default to the local agent-service URL
            constructed from the ``AGENT_SERVICE_URL`` env var (or
            ``http://localhost:8080``).
        """
        default_base = os.getenv("AGENT_SERVICE_URL", "http://localhost:8080").rstrip(
            "/"
        )

        endpoints: dict[str, str] = {}
        for name, config in self.get_specialist_agents().items():
            explicit = config.get("endpoint")
            if explicit:
                endpoints[name] = explicit.rstrip("/")
            else:
                endpoints[name] = f"{default_base}/api/v1/agents/{name}/invoke"
        return endpoints

    def get_agent_capabilities_for_opa(self) -> dict[str, list[str]]:
        """Get agent capabilities dict suitable for OPA policy.

        Returns the same structure as agent_permissions.rego's
        agent_capabilities map, including routing-agent with the
        union of all specialist departments plus 'admin'.
        """
        specialists = self.get_agent_dept_map()

        # routing-agent can route to any department
        all_departments = set()
        for depts in specialists.values():
            all_departments.update(depts)
        all_departments.add("admin")

        capabilities: dict[str, list[str]] = {
            "routing-agent": sorted(all_departments),
        }
        capabilities.update(specialists)
        return capabilities
