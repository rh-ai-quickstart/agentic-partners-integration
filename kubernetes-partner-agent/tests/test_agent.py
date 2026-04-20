"""Tests for kubernetes_agent.agent."""

from unittest.mock import AsyncMock, MagicMock, patch

from kubernetes_agent.llm.base import LLMResponse


class TestKubernetesAgent:
    """Tests for the KubernetesAgent class."""

    @patch("kubernetes_agent.agent.LLMClientFactory")
    def test_init_creates_llm_client(self, mock_factory, mock_agent_config):
        from kubernetes_agent.agent import KubernetesAgent

        mock_client = MagicMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_factory.create_client.return_value = mock_client

        agent = KubernetesAgent(config=mock_agent_config)

        mock_factory.create_client.assert_called_once_with(
            backend="openai", model="gpt-4"
        )
        assert agent.agent_name == "kubernetes-support"
        assert agent.model == "gpt-4"

    @patch("kubernetes_agent.agent.LLMClientFactory")
    def test_system_message_loaded(self, mock_factory, mock_agent_config):
        from kubernetes_agent.agent import KubernetesAgent

        mock_client = MagicMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_factory.create_client.return_value = mock_client

        agent = KubernetesAgent(config=mock_agent_config)
        assert agent.system_message == "You are a Kubernetes support specialist."

    @patch("kubernetes_agent.agent.LLMClientFactory")
    def test_temperature_from_config(self, mock_factory, mock_agent_config):
        from kubernetes_agent.agent import KubernetesAgent

        mock_client = MagicMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_factory.create_client.return_value = mock_client

        agent = KubernetesAgent(config=mock_agent_config)
        assert agent.temperature == 0.7

    @patch("kubernetes_agent.agent.LLMClientFactory")
    async def test_create_response(self, mock_factory, mock_agent_config):
        from kubernetes_agent.agent import KubernetesAgent

        mock_client = MagicMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_client.create_completion = AsyncMock(
            return_value=LLMResponse(
                content="Check kubectl logs",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                model="gpt-4",
            )
        )
        mock_factory.create_client.return_value = mock_client

        agent = KubernetesAgent(config=mock_agent_config)
        result = await agent.create_response(
            [{"role": "user", "content": "Pod is crashing"}]
        )

        assert result == "Check kubectl logs"
        mock_client.create_completion.assert_awaited_once()

    @patch("kubernetes_agent.agent.LLMClientFactory")
    async def test_create_response_empty_returns_empty(
        self, mock_factory, mock_agent_config
    ):
        from kubernetes_agent.agent import KubernetesAgent

        mock_client = MagicMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_client.create_completion = AsyncMock(
            return_value=LLMResponse(
                content="",
                usage={"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
            )
        )
        mock_factory.create_client.return_value = mock_client

        agent = KubernetesAgent(config=mock_agent_config)
        result = await agent.create_response([{"role": "user", "content": "Hi"}])
        assert result == ""

    @patch("kubernetes_agent.agent.asyncio.sleep", new_callable=AsyncMock)
    @patch("kubernetes_agent.agent.LLMClientFactory")
    async def test_retry_succeeds_first_try(
        self, mock_factory, mock_sleep, mock_agent_config
    ):
        from kubernetes_agent.agent import KubernetesAgent

        mock_client = MagicMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_client.create_completion = AsyncMock(
            return_value=LLMResponse(
                content="Success",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )
        )
        mock_factory.create_client.return_value = mock_client

        agent = KubernetesAgent(config=mock_agent_config)
        response, failed = await agent.create_response_with_retry(
            [{"role": "user", "content": "Hi"}]
        )

        assert response == "Success"
        assert failed is False
        mock_sleep.assert_not_awaited()

    @patch("kubernetes_agent.agent.asyncio.sleep", new_callable=AsyncMock)
    @patch("kubernetes_agent.agent.LLMClientFactory")
    async def test_retry_recovers_after_failure(
        self, mock_factory, mock_sleep, mock_agent_config
    ):
        from kubernetes_agent.agent import KubernetesAgent

        mock_client = MagicMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_client.create_completion = AsyncMock(
            side_effect=[
                RuntimeError("LLM error"),
                LLMResponse(
                    content="Recovered",
                    usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                ),
            ]
        )
        mock_factory.create_client.return_value = mock_client

        agent = KubernetesAgent(config=mock_agent_config)
        response, failed = await agent.create_response_with_retry(
            [{"role": "user", "content": "Hi"}], max_retries=3
        )

        assert response == "Recovered"
        assert failed is False

    @patch("kubernetes_agent.agent.asyncio.sleep", new_callable=AsyncMock)
    @patch("kubernetes_agent.agent.LLMClientFactory")
    async def test_retry_all_fail(self, mock_factory, mock_sleep, mock_agent_config):
        from kubernetes_agent.agent import KubernetesAgent

        mock_client = MagicMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_client.create_completion = AsyncMock(
            side_effect=RuntimeError("LLM down")
        )
        mock_factory.create_client.return_value = mock_client

        agent = KubernetesAgent(config=mock_agent_config)
        response, failed = await agent.create_response_with_retry(
            [{"role": "user", "content": "Hi"}], max_retries=2
        )

        assert failed is True
        assert "apologize" in response.lower()

    @patch("kubernetes_agent.agent.LLMClientFactory")
    async def test_create_response_with_non_dict_message(
        self, mock_factory, mock_agent_config
    ):
        from kubernetes_agent.agent import KubernetesAgent

        mock_client = MagicMock()
        mock_client.get_model_name.return_value = "gpt-4"
        mock_client.create_completion = AsyncMock(
            return_value=LLMResponse(
                content="Response to string",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )
        )
        mock_factory.create_client.return_value = mock_client

        agent = KubernetesAgent(config=mock_agent_config)
        result = await agent.create_response(["raw string message"])
        assert result == "Response to string"
