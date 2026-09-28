"""MCP client for connecting to MCP servers and executing tool calls.

Uses the official MCP Python SDK to communicate with MCP servers.
Supports two transports:
- ``http`` (StreamableHTTP) — used by the Azure MCP server and most
  production deployments.  This is the default.
- ``sse`` (Server-Sent Events) — legacy transport for older MCP servers.

When the MCP server requires Azure AD JWT Bearer authentication
(the Red Hat MCP catalog image in HTTP mode), the client acquires
tokens via the OAuth 2.0 client_credentials grant and passes them
as ``Authorization: Bearer <token>`` headers on every request.
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class AzureADTokenProvider:
    """Acquires and caches Azure AD tokens via client_credentials grant."""

    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        self._token_url = (
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        )
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = f"api://{client_id}/.default"
        self._token: str | None = None
        self._expires_at: float = 0

    async def get_token(self) -> str:
        if self._token and time.monotonic() < self._expires_at:
            return self._token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self._token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": self._scope,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        self._token = data["access_token"]
        # Refresh 5 minutes before actual expiry
        self._expires_at = time.monotonic() + data.get("expires_in", 3600) - 300
        logger.info("Acquired Azure AD token (expires_in=%s)", data.get("expires_in"))
        return self._token


@dataclass
class MCPToolResult:
    """Result from an MCP tool call."""

    content: str
    is_error: bool = False


class MCPClient:
    """Manages a session-scoped connection to an MCP server.

    Usage as an async context manager (one session per request)::

        async with MCPClient("http://mcp-server:8080/mcp") as mcp:
            tools = await mcp.list_tools()
            result = await mcp.call_tool("search", {"query": "pods"})

    For Azure AD-authenticated MCP servers, pass auth credentials::

        async with MCPClient(
            url="http://mcp-server:8080/mcp",
            azure_tenant_id="...",
            azure_client_id="...",
            azure_client_secret="...",
        ) as mcp:
            ...
    """

    def __init__(
        self,
        url: str,
        tool_filter: list[str] | None = None,
        transport: str = "http",
        azure_tenant_id: str | None = None,
        azure_client_id: str | None = None,
        azure_client_secret: str | None = None,
    ):
        self.url = url
        self.tool_filter = tool_filter
        self.transport = transport
        self._session = None
        self._transport_cm = None
        self._session_cm = None

        self._token_provider: AzureADTokenProvider | None = None
        if azure_tenant_id and azure_client_id and azure_client_secret:
            self._token_provider = AzureADTokenProvider(
                azure_tenant_id, azure_client_id, azure_client_secret
            )

    async def _get_auth_headers(self) -> dict[str, str] | None:
        if not self._token_provider:
            return None
        token = await self._token_provider.get_token()
        return {"Authorization": f"Bearer {token}"}

    async def __aenter__(self):
        from mcp import ClientSession

        headers = await self._get_auth_headers()

        if self.transport == "sse":
            from mcp.client.sse import sse_client

            self._transport_cm = sse_client(url=self.url, headers=headers)
            read_stream, write_stream = await self._transport_cm.__aenter__()
        else:
            from mcp.client.streamable_http import streamablehttp_client

            self._transport_cm = streamablehttp_client(url=self.url, headers=headers)
            read_stream, write_stream, _ = await self._transport_cm.__aenter__()

        self._session_cm = ClientSession(read_stream, write_stream)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()

        logger.info(
            "Connected to MCP server: %s (transport=%s, auth=%s)",
            self.url,
            self.transport,
            "azure-ad" if self._token_provider else "none",
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            if self._session_cm:
                await self._session_cm.__aexit__(exc_type, exc_val, exc_tb)
        except Exception:
            pass
        try:
            if self._transport_cm:
                await self._transport_cm.__aexit__(exc_type, exc_val, exc_tb)
        except (RuntimeError, BaseExceptionGroup):
            # streamablehttp_client raises RuntimeError on cleanup when the
            # anyio cancel scope exits in a different task than it entered.
            pass
        self._session = None
        logger.info("Disconnected from MCP server: %s", self.url)

    async def list_tools(self) -> list[Any]:
        """List available tools from the MCP server.

        Returns MCP Tool objects. Apply tool_filter if configured.
        """
        result = await self._session.list_tools()
        tools = result.tools

        if self.tool_filter:
            tools = [
                t
                for t in tools
                if any(keyword in t.name for keyword in self.tool_filter)
            ]
            logger.info(
                "Filtered tools: %d/%d (filter=%s)",
                len(tools),
                len(result.tools),
                self.tool_filter,
            )

        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        """Call a tool on the MCP server and return the result."""
        logger.info("Calling MCP tool: %s(%s)", name, json.dumps(arguments)[:200])

        result = await self._session.call_tool(name, arguments=arguments)

        texts = []
        for block in result.content:
            if hasattr(block, "text"):
                texts.append(block.text)

        content = "\n".join(texts) if texts else ""
        is_error = bool(result.isError) if result.isError is not None else False

        if is_error:
            logger.warning("MCP tool %s returned error: %s", name, content[:200])
        else:
            logger.info("MCP tool %s returned %d chars", name, len(content))

        return MCPToolResult(content=content, is_error=is_error)

    @staticmethod
    def to_openai_tools(
        mcp_tools: list[Any], max_description_len: int = 200
    ) -> list[dict[str, Any]]:
        """Convert MCP tool definitions to OpenAI function calling format.

        Truncates descriptions to avoid exceeding model context limits
        when many tools are registered (Gemini returns empty responses
        with >~60K of tool definitions).
        """
        openai_tools = []
        for tool in mcp_tools:
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": (tool.description or "")[:max_description_len],
                        "parameters": tool.inputSchema
                        if tool.inputSchema
                        else {"type": "object", "properties": {}},
                    },
                }
            )
        return openai_tools
