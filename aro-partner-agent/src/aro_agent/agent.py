"""ARO agent — independent black box using OpenAI SDK + MCP tool calling.

This agent uses the OpenAI SDK directly (no shared LLM framework).
When an MCP server is configured, the agent fetches tool definitions
from it and lets the LLM decide which tools to call.  The agent
handles the LLM <-> MCP round-trip loop until the LLM produces a
final text response.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import yaml
from openai import AsyncOpenAI

from .mcp_client import MCPClient

logger = logging.getLogger(__name__)

AGENT_NAME = "aro-support"
MAX_TOOL_ROUNDS = 10


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
    """Load the aro-support-agent YAML config."""
    config_path = _find_config_path()
    agent_file = config_path / "agents" / "aro-support-agent.yaml"
    if not agent_file.exists():
        raise FileNotFoundError(f"Agent config not found: {agent_file}")
    with open(agent_file) as f:
        return yaml.safe_load(f) or {}


class AROAgent:
    """ARO support specialist agent with MCP tool calling.

    Uses the OpenAI SDK directly for LLM calls.  When ``mcp_server_url``
    is configured, the LLM sees tool definitions from the MCP server
    and can call them during response generation.
    """

    def __init__(self, config: Optional[dict[str, Any]] = None):
        self.config = config or load_agent_config()
        self.agent_name = self.config.get("name", AGENT_NAME)
        self.system_message = self.config.get("system_message", "")

        sampling = self.config.get("sampling_params", {})
        strategy = sampling.get("strategy", {})
        self.temperature = strategy.get("temperature", 0.7)

        self.model = self.config.get(
            "llm_model", os.getenv("OPENAI_MODEL", "gpt-4")
        )
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "not-set"
        base_url = os.getenv("OPENAI_BASE_URL")
        self.openai_client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )

        mcp_configs = self.config.get("mcp_servers", [])
        self.mcp_server_url: str | None = os.getenv("MCP_SERVER_URL") or (
            mcp_configs[0]["url"] if mcp_configs else None
        )
        self.mcp_tool_filter: list[str] | None = (
            mcp_configs[0].get("tool_filter")
            if mcp_configs
            else None
        )
        self.mcp_transport: str = os.getenv("MCP_TRANSPORT") or (
            mcp_configs[0].get("transport", "http")
            if mcp_configs
            else "http"
        )

        logger.info(
            "Initialized AROAgent: model=%s mcp=%s",
            self.model,
            self.mcp_server_url or "disabled",
        )

    async def create_response(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
    ) -> tuple[str, list[dict]]:
        """Create a response, using MCP tools if available.

        Returns:
            Tuple of (response_text, tool_calls) where tool_calls is a list
            of dicts with keys: name, arguments, result.
        """
        if self.mcp_server_url:
            try:
                return await self._response_with_tools(messages, temperature)
            except Exception as e:
                logger.warning("MCP flow failed, falling back to simple: %s", e)
                return await self._response_simple(messages, temperature), []
        return await self._response_simple(messages, temperature), []

    async def _response_simple(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
    ) -> str:
        """Plain LLM response without tool calling."""
        llm_messages = self._build_messages(messages)
        temp = temperature if temperature is not None else self.temperature

        response = await self.openai_client.chat.completions.create(
            model=self.model,
            messages=llm_messages,
            temperature=temp,
        )
        return response.choices[0].message.content or ""

    async def _response_with_tools(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
    ) -> tuple[str, list[dict]]:
        """LLM response with MCP tool-calling loop.

        1. Connect to MCP server, fetch tool definitions
        2. Call LLM with messages + tools
        3. If LLM returns tool_calls -> execute via MCP -> feed back -> repeat
        4. If LLM returns text -> return it
        """
        temp = temperature if temperature is not None else self.temperature
        tool_call_log: list[dict] = []

        async with MCPClient(
            self.mcp_server_url,
            tool_filter=self.mcp_tool_filter,
            transport=self.mcp_transport,
        ) as mcp:
            mcp_tools = await mcp.list_tools()
            openai_tools = MCPClient.to_openai_tools(mcp_tools)

            if not openai_tools:
                logger.warning("MCP server returned no tools, using simple flow")
                return await self._response_simple(messages, temperature), []

            logger.info("MCP tools available: %d", len(openai_tools))
            llm_messages = self._build_messages(messages)

            for round_num in range(MAX_TOOL_ROUNDS):
                response = await self.openai_client.chat.completions.create(
                    model=self.model,
                    messages=llm_messages,
                    tools=openai_tools,
                    temperature=temp,
                )

                choice = response.choices[0]

                if not choice.message.tool_calls:
                    return choice.message.content or "", tool_call_log

                logger.info(
                    "Round %d: LLM requested %d tool call(s)",
                    round_num + 1,
                    len(choice.message.tool_calls),
                )

                assistant_msg = choice.message.model_dump(exclude_none=True)
                assistant_msg.setdefault("content", "")
                llm_messages.append(assistant_msg)

                for tool_call in choice.message.tool_calls:
                    args = json.loads(tool_call.function.arguments)
                    result = await mcp.call_tool(
                        tool_call.function.name,
                        args,
                    )
                    tool_call_log.append({
                        "tool": tool_call.function.name,
                        "arguments": args,
                        "result_preview": result.content[:200] if result.content else "",
                    })
                    llm_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result.content,
                        }
                    )

            logger.warning("Hit max tool rounds (%d), forcing final response", MAX_TOOL_ROUNDS)
            response = await self.openai_client.chat.completions.create(
                model=self.model,
                messages=llm_messages,
                temperature=temp,
            )
            return response.choices[0].message.content or "", tool_call_log

    def _build_messages(
        self, messages: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        """Prepend system message and normalize input messages."""
        llm_messages: list[dict[str, str]] = []
        if self.system_message:
            llm_messages.append({"role": "system", "content": self.system_message})
        for msg in messages:
            if isinstance(msg, dict):
                llm_messages.append(
                    {
                        "role": msg.get("role", "user"),
                        "content": msg.get("content", ""),
                    }
                )
            else:
                llm_messages.append({"role": "user", "content": str(msg)})
        return llm_messages

    async def create_response_with_retry(
        self,
        messages: list[dict[str, str]],
        max_retries: int = 3,
        temperature: float | None = None,
    ) -> tuple[str, bool, list[dict]]:
        """Create a response with retry logic.

        Returns:
            Tuple of (response_text, failed, tool_calls).
        """
        default_response = (
            "I apologize, but I'm having difficulty generating a response "
            "right now. Please try again."
        )
        response = default_response
        tool_calls: list[dict] = []

        for attempt in range(max_retries + 1):
            should_retry = False
            try:
                response, tool_calls = await self.create_response(messages, temperature)
                if response and response.strip():
                    break
                should_retry = True
            except Exception as e:
                logger.warning("Attempt %d/%d failed: %s", attempt + 1, max_retries + 1, e)
                should_retry = True

            if should_retry and attempt < max_retries:
                await asyncio.sleep(min(2**attempt, 16))
            elif should_retry:
                response = default_response
                tool_calls = []
                break

        return response, response == default_response, tool_calls
