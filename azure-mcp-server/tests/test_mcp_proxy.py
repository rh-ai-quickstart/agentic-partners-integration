"""Tests for mcp_proxy HTTP handlers."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, TestClient, TestServer

# We need to add the parent directory to the path for imports
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_proxy import McpProxy, create_app, handle_health, handle_mcp_post


@pytest.fixture
def mock_proxy():
    """Create a McpProxy with mocked internals."""
    proxy = McpProxy()
    proxy._session = MagicMock()
    # Fake discovered tools
    tool1 = MagicMock()
    tool1.name = "storage"
    tool1.description = "Azure Storage operations"
    tool1.inputSchema = {"type": "object", "properties": {"command": {"type": "string"}}}

    tool2 = MagicMock()
    tool2.name = "keyvault"
    tool2.description = "Azure Key Vault operations"
    tool2.inputSchema = None

    proxy._tools = [tool1, tool2]
    return proxy


@pytest.fixture
def app(mock_proxy):
    """Create test aiohttp app with mocked proxy."""
    app = web.Application()
    app["proxy"] = mock_proxy
    app.router.add_post("/", handle_mcp_post)
    app.router.add_get("/health", handle_health)
    return app


class TestHealthEndpoint:
    async def test_health_returns_status(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "azure-mcp-proxy"
        assert data["tools_count"] == 2
        assert "storage" in data["tools"]
        assert "keyvault" in data["tools"]


class TestMcpInitialize:
    async def test_initialize_returns_capabilities(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.post("/", json={
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 1,
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1.0"},
            },
        })
        assert resp.status == 200
        data = await resp.json()
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 1
        assert data["result"]["protocolVersion"] == "2024-11-05"
        assert data["result"]["serverInfo"]["name"] == "azure-mcp-proxy"


class TestMcpNotificationsInitialized:
    async def test_notifications_initialized_returns_202(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.post("/", json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        assert resp.status == 202


class TestMcpToolsList:
    async def test_tools_list_returns_all_tools(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.post("/", json={
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 2,
            "params": {},
        })
        assert resp.status == 200
        data = await resp.json()
        tools = data["result"]["tools"]
        assert len(tools) == 2
        assert tools[0]["name"] == "storage"
        assert tools[0]["description"] == "Azure Storage operations"
        assert tools[1]["name"] == "keyvault"
        assert tools[1]["inputSchema"] == {"type": "object", "properties": {}}


class TestMcpToolsCall:
    async def test_tools_call_success(self, aiohttp_client, app, mock_proxy):
        text_block = MagicMock()
        text_block.text = "storage account created"
        mock_result = MagicMock()
        mock_result.content = [text_block]
        mock_result.isError = False

        mock_proxy._session = AsyncMock()
        mock_proxy._session.call_tool = AsyncMock(return_value=mock_result)

        client = await aiohttp_client(app)
        resp = await client.post("/", json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 3,
            "params": {
                "name": "storage",
                "arguments": {"command": "list_accounts"},
            },
        })
        assert resp.status == 200
        data = await resp.json()
        assert data["result"]["content"][0]["text"] == "storage account created"
        assert data["result"]["isError"] is False

    async def test_tools_call_error(self, aiohttp_client, app, mock_proxy):
        mock_proxy._session = AsyncMock()
        mock_proxy._session.call_tool = AsyncMock(
            side_effect=RuntimeError("connection lost")
        )

        client = await aiohttp_client(app)
        resp = await client.post("/", json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 4,
            "params": {"name": "bad_tool", "arguments": {}},
        })
        assert resp.status == 200
        data = await resp.json()
        assert data["result"]["isError"] is True
        assert "connection lost" in data["result"]["content"][0]["text"]


class TestMcpPing:
    async def test_ping_returns_empty(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.post("/", json={
            "jsonrpc": "2.0",
            "method": "ping",
            "id": 5,
        })
        assert resp.status == 200
        data = await resp.json()
        assert data["result"] == {}


class TestMcpUnknownMethod:
    async def test_unknown_method_returns_error(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.post("/", json={
            "jsonrpc": "2.0",
            "method": "unknown/method",
            "id": 6,
        })
        assert resp.status == 200
        data = await resp.json()
        assert data["error"]["code"] == -32601
        assert "unknown/method" in data["error"]["message"]


class TestMcpInvalidJson:
    async def test_invalid_json_returns_parse_error(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.post(
            "/",
            data=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400
        data = await resp.json()
        assert data["error"]["code"] == -32700


class TestMcpProxyToolsAsDicts:
    def test_tools_as_dicts(self, mock_proxy):
        result = mock_proxy.tools_as_dicts()
        assert len(result) == 2
        assert result[0]["name"] == "storage"
        assert result[0]["description"] == "Azure Storage operations"
        assert result[1]["inputSchema"] == {"type": "object", "properties": {}}


class TestBuildMcpArgs:
    def test_default_args(self):
        from mcp_proxy import _build_mcp_args

        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("MCP_NAMESPACES", None)
            os.environ.pop("MCP_MODE", None)
            os.environ.pop("MCP_READ_ONLY", None)
            args = _build_mcp_args()

        assert "server" in args
        assert "start" in args
        assert "--outgoing-auth-strategy" in args
        assert "UseHostingEnvironmentIdentity" in args

    def test_with_namespaces(self):
        from mcp_proxy import _build_mcp_args

        with patch.dict("os.environ", {"MCP_NAMESPACES": "storage,keyvault"}):
            args = _build_mcp_args()

        assert "--namespace" in args
        idx = args.index("--namespace")
        assert args[idx + 1] == "storage"

    def test_with_read_only(self):
        from mcp_proxy import _build_mcp_args

        with patch.dict("os.environ", {"MCP_READ_ONLY": "true"}):
            args = _build_mcp_args()

        assert "--read-only" in args
