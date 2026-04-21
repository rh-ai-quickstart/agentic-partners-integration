"""Tests for aro_agent.mcp_client."""

from unittest.mock import AsyncMock, MagicMock, patch

from aro_agent.mcp_client import MCPClient, MCPToolResult


class TestMCPToolResult:
    def test_defaults(self):
        r = MCPToolResult(content="hello")
        assert r.content == "hello"
        assert r.is_error is False

    def test_error_flag(self):
        r = MCPToolResult(content="boom", is_error=True)
        assert r.is_error is True


class TestMCPClientToOpenAITools:
    def test_converts_single_tool(self):
        tool = MagicMock()
        tool.name = "search_index"
        tool.description = "Search the index"
        tool.inputSchema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        }

        result = MCPClient.to_openai_tools([tool])

        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "search_index"
        assert result[0]["function"]["description"] == "Search the index"
        assert result[0]["function"]["parameters"] == tool.inputSchema

    def test_missing_schema_uses_empty(self):
        tool = MagicMock()
        tool.name = "no_schema"
        tool.description = None
        tool.inputSchema = None

        result = MCPClient.to_openai_tools([tool])

        assert result[0]["function"]["description"] == ""
        assert result[0]["function"]["parameters"] == {
            "type": "object",
            "properties": {},
        }

    def test_empty_list(self):
        assert MCPClient.to_openai_tools([]) == []


class TestMCPClientTransport:
    def test_default_transport_is_http(self):
        client = MCPClient("http://localhost:8080/mcp")
        assert client.transport == "http"

    def test_sse_transport(self):
        client = MCPClient("http://localhost:8080/sse", transport="sse")
        assert client.transport == "sse"


class TestMCPClientListTools:
    async def test_list_tools_no_filter(self):
        client = MCPClient("http://localhost:8080/mcp")

        mock_tool = MagicMock()
        mock_tool.name = "search_docs"

        mock_result = MagicMock()
        mock_result.tools = [mock_tool]

        client._session = AsyncMock()
        client._session.list_tools = AsyncMock(return_value=mock_result)

        tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "search_docs"

    async def test_list_tools_with_filter(self):
        client = MCPClient(
            "http://localhost:8080/mcp", tool_filter=["search", "storage"]
        )

        tools_data = []
        for name in [
            "search_azure_ai_index",
            "get_azure_storage_details",
            "create_azure_sql_database",
            "get_azure_container_details",
        ]:
            t = MagicMock()
            t.name = name
            tools_data.append(t)

        mock_result = MagicMock()
        mock_result.tools = tools_data

        client._session = AsyncMock()
        client._session.list_tools = AsyncMock(return_value=mock_result)

        tools = await client.list_tools()
        assert len(tools) == 2
        assert {t.name for t in tools} == {
            "search_azure_ai_index",
            "get_azure_storage_details",
        }


class TestMCPClientCallTool:
    async def test_call_tool_success(self):
        client = MCPClient("http://localhost:8080/mcp")

        text_block = MagicMock()
        text_block.text = "result data"
        mock_result = MagicMock()
        mock_result.content = [text_block]
        mock_result.isError = False

        client._session = AsyncMock()
        client._session.call_tool = AsyncMock(return_value=mock_result)

        result = await client.call_tool("search", {"query": "pods"})

        assert result.content == "result data"
        assert result.is_error is False
        client._session.call_tool.assert_awaited_once_with(
            "search", arguments={"query": "pods"}
        )

    async def test_call_tool_error(self):
        client = MCPClient("http://localhost:8080/mcp")

        text_block = MagicMock()
        text_block.text = "tool failed"
        mock_result = MagicMock()
        mock_result.content = [text_block]
        mock_result.isError = True

        client._session = AsyncMock()
        client._session.call_tool = AsyncMock(return_value=mock_result)

        result = await client.call_tool("bad_tool", {})

        assert result.is_error is True
        assert result.content == "tool failed"

    async def test_call_tool_no_text_blocks(self):
        client = MCPClient("http://localhost:8080/mcp")

        image_block = MagicMock(spec=[])
        mock_result = MagicMock()
        mock_result.content = [image_block]
        mock_result.isError = None

        client._session = AsyncMock()
        client._session.call_tool = AsyncMock(return_value=mock_result)

        result = await client.call_tool("image_tool", {})
        assert result.content == ""
        assert result.is_error is False


class TestMCPClientCallToolMultipleBlocks:
    async def test_call_tool_multiple_text_blocks(self):
        client = MCPClient("http://localhost:8080/mcp")

        block1 = MagicMock()
        block1.text = "first result"
        block2 = MagicMock()
        block2.text = "second result"
        mock_result = MagicMock()
        mock_result.content = [block1, block2]
        mock_result.isError = False

        client._session = AsyncMock()
        client._session.call_tool = AsyncMock(return_value=mock_result)

        result = await client.call_tool("multi_tool", {})
        assert result.content == "first result\nsecond result"
        assert result.is_error is False


class TestMCPClientFilterEdgeCases:
    async def test_filter_no_matches(self):
        client = MCPClient(
            "http://localhost:8080/mcp", tool_filter=["nonexistent"]
        )

        mock_tool = MagicMock()
        mock_tool.name = "search_index"

        mock_result = MagicMock()
        mock_result.tools = [mock_tool]

        client._session = AsyncMock()
        client._session.list_tools = AsyncMock(return_value=mock_result)

        tools = await client.list_tools()
        assert len(tools) == 0

    async def test_filter_multiple_keywords_match(self):
        client = MCPClient(
            "http://localhost:8080/mcp",
            tool_filter=["search", "storage", "cosmos"],
        )

        tools_data = []
        for name in [
            "search_azure_ai_index",
            "get_azure_storage_details",
            "get_azure_cosmos_details",
            "create_azure_vm",
            "delete_azure_sql_database",
        ]:
            t = MagicMock()
            t.name = name
            tools_data.append(t)

        mock_result = MagicMock()
        mock_result.tools = tools_data

        client._session = AsyncMock()
        client._session.list_tools = AsyncMock(return_value=mock_result)

        tools = await client.list_tools()
        assert len(tools) == 3
        assert {t.name for t in tools} == {
            "search_azure_ai_index",
            "get_azure_storage_details",
            "get_azure_cosmos_details",
        }


class TestMCPClientContextManager:
    @patch("aro_agent.mcp_client.MCPClient.__aexit__", new_callable=AsyncMock)
    @patch("aro_agent.mcp_client.MCPClient.__aenter__", new_callable=AsyncMock)
    async def test_context_manager_protocol(self, mock_enter, mock_exit):
        client = MCPClient("http://localhost:8080/mcp")
        mock_enter.return_value = client

        async with client as c:
            assert c is client

        mock_enter.assert_awaited_once()
        mock_exit.assert_awaited_once()
