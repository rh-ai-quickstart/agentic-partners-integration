"""Tests for kubernetes_agent.agent."""

from unittest.mock import AsyncMock, MagicMock, patch


def _mock_openai_response(content: str):
    """Build a mock OpenAI ChatCompletion response."""
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


class TestKubernetesAgent:
    """Tests for the KubernetesAgent class."""

    @patch("kubernetes_agent.agent.AsyncOpenAI")
    def test_init_reads_config(self, mock_openai_cls, mock_agent_config):
        from kubernetes_agent.agent import KubernetesAgent

        agent = KubernetesAgent(config=mock_agent_config)

        assert agent.agent_name == "kubernetes-support"
        assert agent.model == "gpt-4"
        assert agent.temperature == 0.7
        assert agent.system_message == "You are a Kubernetes support specialist."
        mock_openai_cls.assert_called_once()

    @patch("kubernetes_agent.agent.AsyncOpenAI")
    async def test_create_response(self, mock_openai_cls, mock_agent_config):
        from kubernetes_agent.agent import KubernetesAgent

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_openai_response("Check kubectl logs")
        )
        mock_openai_cls.return_value = mock_client

        agent = KubernetesAgent(config=mock_agent_config)
        result = await agent.create_response(
            [{"role": "user", "content": "Pod is crashing"}]
        )

        assert result == "Check kubectl logs"
        mock_client.chat.completions.create.assert_awaited_once()

    @patch("kubernetes_agent.agent.AsyncOpenAI")
    async def test_create_response_empty_returns_empty(
        self, mock_openai_cls, mock_agent_config
    ):
        from kubernetes_agent.agent import KubernetesAgent

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_openai_response("")
        )
        mock_openai_cls.return_value = mock_client

        agent = KubernetesAgent(config=mock_agent_config)
        result = await agent.create_response([{"role": "user", "content": "Hi"}])
        assert result == ""

    @patch("kubernetes_agent.agent.asyncio.sleep", new_callable=AsyncMock)
    @patch("kubernetes_agent.agent.AsyncOpenAI")
    async def test_retry_succeeds_first_try(
        self, mock_openai_cls, mock_sleep, mock_agent_config
    ):
        from kubernetes_agent.agent import KubernetesAgent

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_openai_response("Success")
        )
        mock_openai_cls.return_value = mock_client

        agent = KubernetesAgent(config=mock_agent_config)
        response, failed = await agent.create_response_with_retry(
            [{"role": "user", "content": "Hi"}]
        )

        assert response == "Success"
        assert failed is False
        mock_sleep.assert_not_awaited()

    @patch("kubernetes_agent.agent.asyncio.sleep", new_callable=AsyncMock)
    @patch("kubernetes_agent.agent.AsyncOpenAI")
    async def test_retry_recovers_after_failure(
        self, mock_openai_cls, mock_sleep, mock_agent_config
    ):
        from kubernetes_agent.agent import KubernetesAgent

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[
                RuntimeError("LLM error"),
                _mock_openai_response("Recovered"),
            ]
        )
        mock_openai_cls.return_value = mock_client

        agent = KubernetesAgent(config=mock_agent_config)
        response, failed = await agent.create_response_with_retry(
            [{"role": "user", "content": "Hi"}], max_retries=3
        )

        assert response == "Recovered"
        assert failed is False

    @patch("kubernetes_agent.agent.asyncio.sleep", new_callable=AsyncMock)
    @patch("kubernetes_agent.agent.AsyncOpenAI")
    async def test_retry_all_fail(
        self, mock_openai_cls, mock_sleep, mock_agent_config
    ):
        from kubernetes_agent.agent import KubernetesAgent

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("LLM down")
        )
        mock_openai_cls.return_value = mock_client

        agent = KubernetesAgent(config=mock_agent_config)
        response, failed = await agent.create_response_with_retry(
            [{"role": "user", "content": "Hi"}], max_retries=2
        )

        assert failed is True
        assert "apologize" in response.lower()

    @patch("kubernetes_agent.agent.AsyncOpenAI")
    async def test_create_response_with_non_dict_message(
        self, mock_openai_cls, mock_agent_config
    ):
        from kubernetes_agent.agent import KubernetesAgent

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_openai_response("Response to string")
        )
        mock_openai_cls.return_value = mock_client

        agent = KubernetesAgent(config=mock_agent_config)
        result = await agent.create_response(["raw string message"])
        assert result == "Response to string"
