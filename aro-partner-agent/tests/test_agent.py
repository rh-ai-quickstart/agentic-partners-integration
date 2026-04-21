"""Tests for aro_agent.agent."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_openai_response(content: str, tool_calls=None):
    """Build a mock OpenAI ChatCompletion response."""
    choice = MagicMock()
    choice.message.content = content
    choice.message.tool_calls = tool_calls
    choice.message.model_dump.return_value = {
        "role": "assistant",
        "content": content,
        "tool_calls": None,
    }
    response = MagicMock()
    response.choices = [choice]
    return response


def _mock_tool_call(call_id: str, name: str, arguments: str):
    """Build a mock OpenAI tool call object."""
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = arguments
    return tc


class TestAROAgentInit:
    @patch.dict("os.environ", {}, clear=False)
    @patch("aro_agent.agent.AsyncOpenAI")
    def test_init_with_mcp(self, mock_openai_cls, mock_agent_config):
        from aro_agent.agent import AROAgent

        os.environ.pop("MCP_SERVER_URL", None)
        os.environ.pop("MCP_TRANSPORT", None)
        agent = AROAgent(config=mock_agent_config)

        assert agent.agent_name == "aro-support"
        assert agent.model == "gemini-2.5-flash"
        assert agent.temperature == 0.7
        assert agent.mcp_server_url == "http://azure-mcp-server:8080/mcp"
        assert agent.mcp_tool_filter is None
        assert agent.mcp_transport == "http"
        mock_openai_cls.assert_called_once()

    @patch("aro_agent.agent.AsyncOpenAI")
    def test_init_without_mcp(self, mock_openai_cls, mock_agent_config_no_mcp):
        from aro_agent.agent import AROAgent

        agent = AROAgent(config=mock_agent_config_no_mcp)

        assert agent.mcp_server_url is None
        assert agent.mcp_tool_filter is None
        assert agent.mcp_transport == "http"

    @patch.dict("os.environ", {"MCP_SERVER_URL": "http://env-mcp:8080/mcp"})
    @patch("aro_agent.agent.AsyncOpenAI")
    def test_init_mcp_from_env(self, mock_openai_cls, mock_agent_config_no_mcp):
        from aro_agent.agent import AROAgent

        agent = AROAgent(config=mock_agent_config_no_mcp)

        assert agent.mcp_server_url == "http://env-mcp:8080/mcp"

    @patch.dict("os.environ", {"MCP_SERVER_URL": "http://override:8080/"})
    @patch("aro_agent.agent.AsyncOpenAI")
    def test_init_env_overrides_config_mcp_url(self, mock_openai_cls, mock_agent_config):
        from aro_agent.agent import AROAgent

        agent = AROAgent(config=mock_agent_config)

        assert agent.mcp_server_url == "http://override:8080/"

    @patch.dict(
        "os.environ",
        {"GOOGLE_API_KEY": "gemini-key-123", "OPENAI_API_KEY": ""},
        clear=False,
    )
    @patch("aro_agent.agent.AsyncOpenAI")
    def test_init_google_api_key_fallback(self, mock_openai_cls, mock_agent_config_no_mcp):
        from aro_agent.agent import AROAgent

        AROAgent(config=mock_agent_config_no_mcp)

        mock_openai_cls.assert_called_once_with(
            api_key="gemini-key-123", base_url=None,
        )

    @patch.dict(
        "os.environ",
        {"OPENAI_BASE_URL": "https://generativelanguage.googleapis.com/v1beta/openai/"},
        clear=False,
    )
    @patch("aro_agent.agent.AsyncOpenAI")
    def test_init_base_url_from_env(self, mock_openai_cls, mock_agent_config_no_mcp):
        from aro_agent.agent import AROAgent

        AROAgent(config=mock_agent_config_no_mcp)

        call_kwargs = mock_openai_cls.call_args
        assert call_kwargs.kwargs["base_url"] == "https://generativelanguage.googleapis.com/v1beta/openai/"

    @patch.dict("os.environ", {"MCP_TRANSPORT": "sse"}, clear=False)
    @patch("aro_agent.agent.AsyncOpenAI")
    def test_init_mcp_transport_from_env(self, mock_openai_cls, mock_agent_config_no_mcp):
        from aro_agent.agent import AROAgent

        agent = AROAgent(config=mock_agent_config_no_mcp)

        assert agent.mcp_transport == "sse"


class TestAROAgentConfigLoading:
    def test_load_agent_config_file_not_found(self):
        from aro_agent.agent import _find_config_path

        with patch("pathlib.Path.exists", return_value=False):
            with pytest.raises(FileNotFoundError, match="Config directory not found"):
                _find_config_path()


class TestAROAgentBuildMessages:
    @patch("aro_agent.agent.AsyncOpenAI")
    def test_build_messages_with_non_dict(self, mock_openai_cls, mock_agent_config_no_mcp):
        from aro_agent.agent import AROAgent

        agent = AROAgent(config=mock_agent_config_no_mcp)
        result = agent._build_messages(["plain string message"])

        assert result[-1] == {"role": "user", "content": "plain string message"}

    @patch("aro_agent.agent.AsyncOpenAI")
    def test_build_messages_prepends_system(self, mock_openai_cls, mock_agent_config_no_mcp):
        from aro_agent.agent import AROAgent

        agent = AROAgent(config=mock_agent_config_no_mcp)
        result = agent._build_messages([{"role": "user", "content": "Hi"}])

        assert result[0]["role"] == "system"
        assert result[1] == {"role": "user", "content": "Hi"}


class TestAROAgentSimpleResponse:
    @patch("aro_agent.agent.AsyncOpenAI")
    async def test_simple_response(self, mock_openai_cls, mock_agent_config_no_mcp):
        from aro_agent.agent import AROAgent

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_openai_response("ARO advice here")
        )
        mock_openai_cls.return_value = mock_client

        agent = AROAgent(config=mock_agent_config_no_mcp)
        result, tool_calls = await agent.create_response(
            [{"role": "user", "content": "Help with ARO"}]
        )

        assert result == "ARO advice here"
        assert tool_calls == []
        mock_client.chat.completions.create.assert_awaited_once()

    @patch("aro_agent.agent.AsyncOpenAI")
    async def test_empty_response(self, mock_openai_cls, mock_agent_config_no_mcp):
        from aro_agent.agent import AROAgent

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_openai_response("")
        )
        mock_openai_cls.return_value = mock_client

        agent = AROAgent(config=mock_agent_config_no_mcp)
        result, tool_calls = await agent.create_response(
            [{"role": "user", "content": "Hi"}]
        )
        assert result == ""
        assert tool_calls == []


class TestAROAgentMCPResponse:
    @patch("aro_agent.agent.MCPClient")
    @patch("aro_agent.agent.AsyncOpenAI")
    async def test_response_with_tools_no_tool_calls(
        self, mock_openai_cls, mock_mcp_cls, mock_agent_config
    ):
        """LLM sees tools but decides not to call any."""
        from aro_agent.agent import AROAgent

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_openai_response("Direct answer")
        )
        mock_openai_cls.return_value = mock_client

        mock_tool = MagicMock()
        mock_tool.name = "search_index"
        mock_tool.description = "Search"
        mock_tool.inputSchema = {"type": "object", "properties": {}}

        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.list_tools = AsyncMock(return_value=[mock_tool])
        mock_mcp_cls.return_value = mock_mcp_instance
        mock_mcp_instance.__aenter__ = AsyncMock(return_value=mock_mcp_instance)
        mock_mcp_instance.__aexit__ = AsyncMock(return_value=None)

        agent = AROAgent(config=mock_agent_config)
        result, tool_calls = await agent.create_response(
            [{"role": "user", "content": "What is ARO?"}]
        )

        assert result == "Direct answer"
        assert tool_calls == []

    @patch("aro_agent.agent.MCPClient")
    @patch("aro_agent.agent.AsyncOpenAI")
    async def test_response_with_tool_call_round(
        self, mock_openai_cls, mock_mcp_cls, mock_agent_config
    ):
        """LLM calls a tool, gets result, then produces final answer."""
        from aro_agent.agent import AROAgent
        from aro_agent.mcp_client import MCPToolResult

        tool_call = _mock_tool_call("tc1", "search_index", '{"query": "oom"}')

        response_with_tools = _mock_openai_response(None, tool_calls=[tool_call])
        final_response = _mock_openai_response("OOMKilled fix: increase limits")

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[response_with_tools, final_response]
        )
        mock_openai_cls.return_value = mock_client

        mock_tool = MagicMock()
        mock_tool.name = "search_index"
        mock_tool.description = "Search"
        mock_tool.inputSchema = {"type": "object", "properties": {}}

        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.list_tools = AsyncMock(return_value=[mock_tool])
        mock_mcp_instance.call_tool = AsyncMock(
            return_value=MCPToolResult(content="Found: set memory limits")
        )
        mock_mcp_cls.return_value = mock_mcp_instance
        mock_mcp_instance.__aenter__ = AsyncMock(return_value=mock_mcp_instance)
        mock_mcp_instance.__aexit__ = AsyncMock(return_value=None)

        agent = AROAgent(config=mock_agent_config)
        result, tool_calls = await agent.create_response(
            [{"role": "user", "content": "Pods OOMKilled"}]
        )

        assert result == "OOMKilled fix: increase limits"
        assert len(tool_calls) == 1
        assert tool_calls[0]["tool"] == "search_index"
        assert tool_calls[0]["arguments"] == {"query": "oom"}
        assert "set memory limits" in tool_calls[0]["result_preview"]
        mock_mcp_instance.call_tool.assert_awaited_once_with(
            "search_index", {"query": "oom"}
        )

    @patch("aro_agent.agent.MCPClient")
    @patch("aro_agent.agent.AsyncOpenAI")
    async def test_mcp_fallback_on_no_tools(
        self, mock_openai_cls, mock_mcp_cls, mock_agent_config
    ):
        """Falls back to simple response when MCP server returns no tools."""
        from aro_agent.agent import AROAgent

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_openai_response("Fallback answer")
        )
        mock_openai_cls.return_value = mock_client

        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.list_tools = AsyncMock(return_value=[])
        mock_mcp_cls.return_value = mock_mcp_instance
        mock_mcp_instance.__aenter__ = AsyncMock(return_value=mock_mcp_instance)
        mock_mcp_instance.__aexit__ = AsyncMock(return_value=None)

        agent = AROAgent(config=mock_agent_config)
        result, tool_calls = await agent.create_response(
            [{"role": "user", "content": "Help"}]
        )

        assert result == "Fallback answer"
        assert tool_calls == []

    @patch("aro_agent.agent.MCPClient")
    @patch("aro_agent.agent.AsyncOpenAI")
    async def test_mcp_connection_failure_falls_back(
        self, mock_openai_cls, mock_mcp_cls, mock_agent_config
    ):
        """Falls back to simple response when MCP connection fails."""
        from aro_agent.agent import AROAgent

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_openai_response("Simple fallback")
        )
        mock_openai_cls.return_value = mock_client

        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.__aenter__ = AsyncMock(
            side_effect=ConnectionError("MCP server down")
        )
        mock_mcp_cls.return_value = mock_mcp_instance

        agent = AROAgent(config=mock_agent_config)
        result, tool_calls = await agent.create_response(
            [{"role": "user", "content": "Help"}]
        )

        assert result == "Simple fallback"
        assert tool_calls == []


class TestAROAgentMultipleToolCalls:
    @patch("aro_agent.agent.MCPClient")
    @patch("aro_agent.agent.AsyncOpenAI")
    async def test_multiple_tool_calls_in_one_round(
        self, mock_openai_cls, mock_mcp_cls, mock_agent_config
    ):
        """LLM requests two tool calls in a single round."""
        from aro_agent.agent import AROAgent
        from aro_agent.mcp_client import MCPToolResult

        tc1 = _mock_tool_call("tc1", "search_index", '{"query": "oom"}')
        tc2 = _mock_tool_call("tc2", "get_storage", '{"account": "prod"}')

        response_with_tools = _mock_openai_response(None, tool_calls=[tc1, tc2])
        final_response = _mock_openai_response("Combined answer")

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[response_with_tools, final_response]
        )
        mock_openai_cls.return_value = mock_client

        mock_tool = MagicMock()
        mock_tool.name = "search_index"
        mock_tool.description = "Search"
        mock_tool.inputSchema = {"type": "object", "properties": {}}

        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.list_tools = AsyncMock(return_value=[mock_tool])
        mock_mcp_instance.call_tool = AsyncMock(
            return_value=MCPToolResult(content="result")
        )
        mock_mcp_cls.return_value = mock_mcp_instance
        mock_mcp_instance.__aenter__ = AsyncMock(return_value=mock_mcp_instance)
        mock_mcp_instance.__aexit__ = AsyncMock(return_value=None)

        agent = AROAgent(config=mock_agent_config)
        result, tool_calls = await agent.create_response(
            [{"role": "user", "content": "Check both"}]
        )

        assert result == "Combined answer"
        assert len(tool_calls) == 2
        assert mock_mcp_instance.call_tool.await_count == 2

    @patch("aro_agent.agent.MCPClient")
    @patch("aro_agent.agent.AsyncOpenAI")
    async def test_max_tool_rounds_exhaustion(
        self, mock_openai_cls, mock_mcp_cls, mock_agent_config
    ):
        """LLM keeps calling tools until MAX_TOOL_ROUNDS is hit."""
        from aro_agent.agent import AROAgent, MAX_TOOL_ROUNDS
        from aro_agent.mcp_client import MCPToolResult

        tc = _mock_tool_call("tc1", "search_index", '{"query": "loop"}')
        tool_response = _mock_openai_response(None, tool_calls=[tc])
        final_response = _mock_openai_response("Forced final answer")

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[tool_response] * MAX_TOOL_ROUNDS + [final_response]
        )
        mock_openai_cls.return_value = mock_client

        mock_tool = MagicMock()
        mock_tool.name = "search_index"
        mock_tool.description = "Search"
        mock_tool.inputSchema = {"type": "object", "properties": {}}

        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.list_tools = AsyncMock(return_value=[mock_tool])
        mock_mcp_instance.call_tool = AsyncMock(
            return_value=MCPToolResult(content="looping")
        )
        mock_mcp_cls.return_value = mock_mcp_instance
        mock_mcp_instance.__aenter__ = AsyncMock(return_value=mock_mcp_instance)
        mock_mcp_instance.__aexit__ = AsyncMock(return_value=None)

        agent = AROAgent(config=mock_agent_config)
        result, tool_calls = await agent.create_response(
            [{"role": "user", "content": "Infinite loop test"}]
        )

        assert result == "Forced final answer"
        assert len(tool_calls) == MAX_TOOL_ROUNDS
        assert mock_mcp_instance.call_tool.await_count == MAX_TOOL_ROUNDS


class TestAROAgentRetry:
    @patch("aro_agent.agent.asyncio.sleep", new_callable=AsyncMock)
    @patch("aro_agent.agent.AsyncOpenAI")
    async def test_retry_succeeds_first_try(
        self, mock_openai_cls, mock_sleep, mock_agent_config_no_mcp
    ):
        from aro_agent.agent import AROAgent

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_openai_response("Success")
        )
        mock_openai_cls.return_value = mock_client

        agent = AROAgent(config=mock_agent_config_no_mcp)
        response, failed, tool_calls = await agent.create_response_with_retry(
            [{"role": "user", "content": "Hi"}]
        )

        assert response == "Success"
        assert failed is False
        assert tool_calls == []
        mock_sleep.assert_not_awaited()

    @patch("aro_agent.agent.asyncio.sleep", new_callable=AsyncMock)
    @patch("aro_agent.agent.AsyncOpenAI")
    async def test_retry_all_fail(
        self, mock_openai_cls, mock_sleep, mock_agent_config_no_mcp
    ):
        from aro_agent.agent import AROAgent

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("LLM down")
        )
        mock_openai_cls.return_value = mock_client

        agent = AROAgent(config=mock_agent_config_no_mcp)
        response, failed, tool_calls = await agent.create_response_with_retry(
            [{"role": "user", "content": "Hi"}], max_retries=1
        )

        assert failed is True
        assert "apologize" in response.lower()
        assert tool_calls == []
