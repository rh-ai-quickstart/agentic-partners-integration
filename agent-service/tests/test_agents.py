"""Tests for agent_service.agents."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_service.llm.base import LLMResponse


class TestAgent:
    """Tests for the Agent class."""

    @patch("agent_service.agents.LLMClientFactory")
    def test_init_creates_llm_client(self, mock_factory, mock_agent_config, mock_global_config):
        from agent_service.agents import Agent

        mock_client = MagicMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_factory.create_client.return_value = mock_client

        agent = Agent("test-agent", mock_agent_config, mock_global_config)

        mock_factory.create_client.assert_called_once_with(
            backend="openai", model="gpt-4"
        )
        assert agent.agent_name == "test-agent"
        assert agent.model == "gpt-4"

    @patch("agent_service.agents.LLMClientFactory")
    def test_get_default_system_message(self, mock_factory, mock_agent_config):
        from agent_service.agents import Agent

        mock_client = MagicMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_factory.create_client.return_value = mock_client

        agent = Agent("test-agent", mock_agent_config)
        assert agent.system_message == "You are a helpful test agent."

    @patch("agent_service.agents.LLMClientFactory")
    def test_get_default_system_message_empty(self, mock_factory):
        from agent_service.agents import Agent

        mock_client = MagicMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_factory.create_client.return_value = mock_client

        config = {"name": "minimal-agent", "llm_backend": "openai", "llm_model": "gpt-4"}
        agent = Agent("minimal-agent", config)
        assert agent.system_message == ""

    @patch("agent_service.agents.LLMClientFactory")
    async def test_create_response(self, mock_factory, mock_agent_config):
        from agent_service.agents import Agent

        mock_client = AsyncMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_client.create_completion.return_value = LLMResponse(
            content="LLM response text",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            model="gpt-4",
        )
        mock_factory.create_client.return_value = mock_client

        agent = Agent("test-agent", mock_agent_config)
        result = await agent.create_response(
            [{"role": "user", "content": "Hello"}]
        )

        assert result == "LLM response text"
        mock_client.create_completion.assert_awaited_once()

    @patch("agent_service.agents.LLMClientFactory")
    async def test_create_response_empty_returns_empty(self, mock_factory, mock_agent_config):
        from agent_service.agents import Agent

        mock_client = AsyncMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_client.create_completion.return_value = LLMResponse(
            content="",
            usage={"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
        )
        mock_factory.create_client.return_value = mock_client

        agent = Agent("test-agent", mock_agent_config)
        result = await agent.create_response([{"role": "user", "content": "Hi"}])
        assert result == ""

    @patch("agent_service.agents.asyncio.sleep", new_callable=AsyncMock)
    @patch("agent_service.agents.LLMClientFactory")
    async def test_create_response_with_retry_succeeds_first_try(
        self, mock_factory, mock_sleep, mock_agent_config
    ):
        from agent_service.agents import Agent

        mock_client = AsyncMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_client.create_completion.return_value = LLMResponse(
            content="Success",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        mock_factory.create_client.return_value = mock_client

        agent = Agent("test-agent", mock_agent_config)
        response, failed = await agent.create_response_with_retry(
            [{"role": "user", "content": "Hi"}]
        )

        assert response == "Success"
        assert failed is False
        mock_sleep.assert_not_awaited()

    @patch("agent_service.agents.asyncio.sleep", new_callable=AsyncMock)
    @patch("agent_service.agents.LLMClientFactory")
    async def test_create_response_with_retry_retries_on_failure(
        self, mock_factory, mock_sleep, mock_agent_config
    ):
        from agent_service.agents import Agent

        mock_client = AsyncMock()
        mock_client.get_model_name.return_value = "gpt-4"

        # First call fails, second succeeds
        mock_client.create_completion.side_effect = [
            RuntimeError("LLM error"),
            LLMResponse(
                content="Success on retry",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
        ]
        mock_factory.create_client.return_value = mock_client

        agent = Agent("test-agent", mock_agent_config)
        response, failed = await agent.create_response_with_retry(
            [{"role": "user", "content": "Hi"}],
            max_retries=3,
        )

        assert response == "Success on retry"
        assert failed is False
        mock_sleep.assert_awaited_once()

    @patch("agent_service.agents.asyncio.sleep", new_callable=AsyncMock)
    @patch("agent_service.agents.LLMClientFactory")
    async def test_create_response_with_retry_all_fail(
        self, mock_factory, mock_sleep, mock_agent_config
    ):
        from agent_service.agents import Agent

        mock_client = AsyncMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_client.create_completion.side_effect = RuntimeError("LLM down")
        mock_factory.create_client.return_value = mock_client

        agent = Agent("test-agent", mock_agent_config)
        response, failed = await agent.create_response_with_retry(
            [{"role": "user", "content": "Hi"}],
            max_retries=2,
        )

        assert failed is True
        assert "apologize" in response.lower()

    @patch("agent_service.agents.LLMClientFactory")
    def test_response_config_from_sampling_params(self, mock_factory, mock_agent_config):
        from agent_service.agents import Agent

        mock_client = MagicMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_factory.create_client.return_value = mock_client

        agent = Agent("test-agent", mock_agent_config)
        assert agent.default_response_config["temperature"] == 0.5

    @patch("agent_service.agents.asyncio.sleep", new_callable=AsyncMock)
    @patch("agent_service.agents.LLMClientFactory")
    async def test_create_response_with_retry_retries_on_empty_response(
        self, mock_factory, mock_sleep, mock_agent_config
    ):
        """Lines 118-119: empty response triggers retry with reason 'empty response'."""
        from agent_service.agents import Agent

        mock_client = AsyncMock()
        mock_client.get_model_name.return_value = "gpt-4"

        # First call returns empty, second succeeds
        mock_client.create_completion.side_effect = [
            LLMResponse(
                content="   ",
                usage={"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
            ),
            LLMResponse(
                content="Good response",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
        ]
        mock_factory.create_client.return_value = mock_client

        agent = Agent("test-agent", mock_agent_config)
        response, failed = await agent.create_response_with_retry(
            [{"role": "user", "content": "Hi"}],
            max_retries=3,
        )

        assert response == "Good response"
        assert failed is False
        mock_sleep.assert_awaited_once()

    @patch("agent_service.agents.asyncio.sleep", new_callable=AsyncMock)
    @patch("agent_service.agents.LLMClientFactory")
    async def test_create_response_with_retry_exception_sets_retry_reason(
        self, mock_factory, mock_sleep, mock_agent_config
    ):
        """Lines 121-131: exception during create_response sets retry_reason='exception'.

        We mock create_response directly so the exception propagates to the
        retry loop's except clause (instead of being caught inside create_response).
        """
        from agent_service.agents import Agent

        mock_client = MagicMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_factory.create_client.return_value = mock_client

        agent = Agent("test-agent", mock_agent_config)

        # Patch create_response on the agent instance to raise, then succeed
        agent.create_response = AsyncMock(side_effect=[
            ConnectionError("Connection refused"),
            "Recovered",
        ])

        response, failed = await agent.create_response_with_retry(
            [{"role": "user", "content": "Hi"}],
            max_retries=3,
        )

        assert response == "Recovered"
        assert failed is False
        mock_sleep.assert_awaited_once()

    @patch("agent_service.agents.LLMClientFactory")
    async def test_create_response_with_non_dict_message(self, mock_factory, mock_agent_config):
        """Line 193: create_response handles non-dict messages by converting to str."""
        from agent_service.agents import Agent

        mock_client = AsyncMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_client.create_completion.return_value = LLMResponse(
            content="Response to string msg",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            model="gpt-4",
        )
        mock_factory.create_client.return_value = mock_client

        agent = Agent("test-agent", mock_agent_config)
        # Pass a non-dict message (a plain string) to trigger line 193
        result = await agent.create_response(["raw string message"])

        assert result == "Response to string msg"
        # Verify the LLM was called with the string converted to a user message
        call_args = mock_client.create_completion.call_args
        messages = call_args.kwargs["messages"]
        # Last message should be the converted non-dict message
        non_dict_msg = [m for m in messages if m.content == "raw string message"]
        assert len(non_dict_msg) == 1
        assert non_dict_msg[0].role == "user"

    @patch("agent_service.agents.LLMClientFactory")
    async def test_create_response_token_counting_exception(self, mock_factory, mock_agent_config):
        """Lines 227-228: token counting exception is caught and logged."""
        from agent_service.agents import Agent

        mock_client = AsyncMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_client.create_completion.return_value = LLMResponse(
            content="Valid response",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            model="gpt-4",
        )
        mock_factory.create_client.return_value = mock_client

        agent = Agent("test-agent", mock_agent_config)

        # Patch the token_counter module so importing it raises an error
        with patch.dict("sys.modules", {"agent_service.token_counter": None}):
            result = await agent.create_response(
                [{"role": "user", "content": "Hi"}]
            )

        # Response should still be returned despite token counting failure
        assert result == "Valid response"


class TestAgentManager:
    """Tests for the AgentManager class."""

    @patch("agent_service.agents.resolve_agent_service_path")
    @patch("agent_service.agents.load_config_from_path")
    @patch("agent_service.agents.LLMClientFactory")
    def test_init_loads_agents(
        self, mock_factory, mock_load_config, mock_resolve, tmp_path
    ):
        from agent_service.agents import AgentManager

        mock_resolve.return_value = tmp_path
        # Create a config.yaml so AgentManager can open it
        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text("llm_backend: openai\nllm_model: gpt-4\n")

        mock_load_config.return_value = {
            "agents": [
                {"name": "agent-a", "llm_backend": "openai", "llm_model": "gpt-4",
                 "system_message": "A"},
                {"name": "agent-b", "llm_backend": "openai", "llm_model": "gpt-4",
                 "system_message": "B"},
            ]
        }

        mock_client = MagicMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_factory.create_client.return_value = mock_client

        manager = AgentManager()
        assert "agent-a" in manager.agents_dict
        assert "agent-b" in manager.agents_dict

    @patch("agent_service.agents.resolve_agent_service_path")
    @patch("agent_service.agents.load_config_from_path")
    @patch("agent_service.agents.LLMClientFactory")
    def test_get_agent_returns_correct_agent(
        self, mock_factory, mock_load_config, mock_resolve, tmp_path
    ):
        from agent_service.agents import AgentManager

        mock_resolve.return_value = tmp_path
        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text("llm_backend: openai\n")

        mock_load_config.return_value = {
            "agents": [
                {"name": "routing-agent", "llm_backend": "openai", "llm_model": "gpt-4"},
            ]
        }

        mock_client = MagicMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_factory.create_client.return_value = mock_client

        manager = AgentManager()
        agent = manager.get_agent("routing-agent")
        assert agent.agent_name == "routing-agent"

    @patch("agent_service.agents.resolve_agent_service_path")
    @patch("agent_service.agents.load_config_from_path")
    @patch("agent_service.agents.LLMClientFactory")
    def test_get_agent_returns_first_if_not_found(
        self, mock_factory, mock_load_config, mock_resolve, tmp_path
    ):
        from agent_service.agents import AgentManager

        mock_resolve.return_value = tmp_path
        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text("llm_backend: openai\n")

        mock_load_config.return_value = {
            "agents": [
                {"name": "agent-a", "llm_backend": "openai", "llm_model": "gpt-4"},
            ]
        }

        mock_client = MagicMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_factory.create_client.return_value = mock_client

        manager = AgentManager()
        # Requesting a non-existent agent returns the first available
        agent = manager.get_agent("nonexistent")
        assert agent.agent_name == "agent-a"

    @patch("agent_service.agents.resolve_agent_service_path")
    @patch("agent_service.agents.load_config_from_path")
    def test_get_agent_raises_when_no_agents(
        self, mock_load_config, mock_resolve, tmp_path
    ):
        from agent_service.agents import AgentManager

        mock_resolve.return_value = tmp_path
        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text("")

        mock_load_config.return_value = {"agents": []}

        manager = AgentManager()
        with pytest.raises(ValueError, match="No agent found"):
            manager.get_agent("anything")

    @patch("agent_service.agents.resolve_agent_service_path")
    def test_init_raises_when_config_not_found(self, mock_resolve):
        """Lines 275-281: AgentManager re-raises FileNotFoundError when config dir missing."""
        from agent_service.agents import AgentManager

        mock_resolve.side_effect = FileNotFoundError("Config directory not found")

        with pytest.raises(FileNotFoundError, match="Config directory not found"):
            AgentManager()
