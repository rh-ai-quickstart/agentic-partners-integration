"""Tests for kubernetes_agent.agent."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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

    @patch("kubernetes_agent.agent.AsyncOpenAI")
    async def test_create_response_llm_error_returns_error_string(
        self, mock_openai_cls, mock_agent_config
    ):
        """When the OpenAI call raises, return an error string."""
        from kubernetes_agent.agent import KubernetesAgent

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("API key invalid")
        )
        mock_openai_cls.return_value = mock_client

        agent = KubernetesAgent(config=mock_agent_config)
        result = await agent.create_response(
            [{"role": "user", "content": "test"}]
        )
        assert result.startswith("Error: Unable to get response from LLM")

    def test_find_config_path_not_found(self, tmp_path):
        """When no config directory exists, raise FileNotFoundError."""
        from kubernetes_agent.agent import _find_config_path

        fake_paths = [tmp_path / "nope1", tmp_path / "nope2", tmp_path / "nope3"]
        with patch("kubernetes_agent.agent.Path") as mock_path_cls:
            mock_path_cls.side_effect = fake_paths
            # None of the candidates exist
            with pytest.raises(FileNotFoundError, match="Config directory not found"):
                _find_config_path()

    def test_load_agent_config_file_not_found(self, tmp_path):
        """When YAML config file doesn't exist, raise FileNotFoundError."""
        from kubernetes_agent.agent import load_agent_config

        # Create config dir without the expected YAML file
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "agents").mkdir()

        with patch("kubernetes_agent.agent._find_config_path", return_value=config_dir):
            with pytest.raises(FileNotFoundError, match="Agent config not found"):
                load_agent_config()

    @patch("kubernetes_agent.agent.asyncio.sleep", new_callable=AsyncMock)
    @patch("kubernetes_agent.agent.AsyncOpenAI")
    async def test_retry_on_error_string_response(
        self, mock_openai_cls, mock_sleep, mock_agent_config
    ):
        """Retry when response starts with 'Error: Unable to get response'."""
        from kubernetes_agent.agent import KubernetesAgent

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[
                RuntimeError("Timeout"),
                _mock_openai_response("Good answer"),
            ]
        )
        mock_openai_cls.return_value = mock_client

        agent = KubernetesAgent(config=mock_agent_config)
        response, failed = await agent.create_response_with_retry(
            [{"role": "user", "content": "test"}], max_retries=2
        )

        assert response == "Good answer"
        assert failed is False
        mock_sleep.assert_awaited()

    @patch("kubernetes_agent.agent.asyncio.sleep", new_callable=AsyncMock)
    @patch("kubernetes_agent.agent.AsyncOpenAI")
    async def test_retry_exception_in_create_response(
        self, mock_openai_cls, mock_sleep, mock_agent_config
    ):
        """When create_response raises directly, retry logic catches it."""
        from kubernetes_agent.agent import KubernetesAgent

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        agent = KubernetesAgent(config=mock_agent_config)

        with patch.object(
            agent,
            "create_response",
            new_callable=AsyncMock,
            side_effect=[Exception("Unexpected failure"), "Recovered answer"],
        ):
            response, failed = await agent.create_response_with_retry(
                [{"role": "user", "content": "test"}], max_retries=2
            )

        assert response == "Recovered answer"
        assert failed is False
        mock_sleep.assert_awaited()
