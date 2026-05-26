"""Tests for kubernetes_agent.a2a.executor — A2A protocol handling."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import Message, Part, Role, TaskState, TextPart
from a2a.utils.errors import ServerError


@pytest.fixture
def mock_context():
    """Build a mock RequestContext with valid A2A types."""
    ctx = MagicMock()
    ctx.get_user_input.return_value = "My pods are crashing"
    ctx.current_task = None
    ctx.message = Message(
        role=Role.user,
        parts=[Part(root=TextPart(text="My pods are crashing"))],
        message_id=str(uuid.uuid4()),
    )
    return ctx


@pytest.fixture
def mock_event_queue():
    """Build a mock EventQueue."""
    eq = AsyncMock()
    eq.enqueue_event = AsyncMock()
    return eq


class TestKubernetesAgentExecutor:
    """Tests for the A2A KubernetesAgentExecutor."""

    @patch("kubernetes_agent.agent.KubernetesAgent")
    @patch("kubernetes_agent.a2a.executor.httpx.AsyncClient")
    async def test_execute_success_with_rag(
        self, mock_httpx, mock_agent_cls, mock_context, mock_event_queue
    ):
        """Full execution: RAG returns results, LLM generates answer."""
        from kubernetes_agent.a2a.executor import KubernetesAgentExecutor

        mock_agent = MagicMock()
        mock_agent.create_response_with_retry = AsyncMock(
            return_value=("Check resource limits on your pods", False)
        )
        mock_agent_cls.return_value = mock_agent

        mock_rag_resp = MagicMock()
        mock_rag_resp.status_code = 200
        mock_rag_resp.json.return_value = {
            "response": "OOMKill is caused by memory limits",
            "sources": [
                {"id": "K8S-101", "similarity": 0.92, "content": "Memory limit fix"},
            ],
        }
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_rag_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_httpx.return_value = mock_client

        executor = KubernetesAgentExecutor()
        await executor.execute(mock_context, mock_event_queue)

        mock_event_queue.enqueue_event.assert_awaited()
        mock_agent.create_response_with_retry.assert_awaited_once()

    @patch("kubernetes_agent.agent.KubernetesAgent")
    @patch("kubernetes_agent.a2a.executor.httpx.AsyncClient")
    async def test_execute_rag_unavailable_still_responds(
        self, mock_httpx, mock_agent_cls, mock_context, mock_event_queue
    ):
        """When RAG API is unreachable, agent still generates a response."""
        import httpx as real_httpx

        from kubernetes_agent.a2a.executor import KubernetesAgentExecutor

        mock_agent = MagicMock()
        mock_agent.create_response_with_retry = AsyncMock(
            return_value=("Generic kubernetes advice", False)
        )
        mock_agent_cls.return_value = mock_agent

        mock_client = AsyncMock()
        mock_client.post.side_effect = real_httpx.ConnectError("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_httpx.return_value = mock_client

        executor = KubernetesAgentExecutor()
        await executor.execute(mock_context, mock_event_queue)

        mock_agent.create_response_with_retry.assert_awaited_once()

    @patch("kubernetes_agent.agent.KubernetesAgent")
    @patch("kubernetes_agent.a2a.executor.httpx.AsyncClient")
    async def test_execute_rag_non_200_returns_empty(
        self, mock_httpx, mock_agent_cls, mock_context, mock_event_queue
    ):
        """When RAG returns non-200, proceed with empty context."""
        from kubernetes_agent.a2a.executor import KubernetesAgentExecutor

        mock_agent = MagicMock()
        mock_agent.create_response_with_retry = AsyncMock(
            return_value=("Fallback answer", False)
        )
        mock_agent_cls.return_value = mock_agent

        mock_rag_resp = MagicMock()
        mock_rag_resp.status_code = 500
        mock_rag_resp.text = "Internal Server Error"
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_rag_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_httpx.return_value = mock_client

        executor = KubernetesAgentExecutor()
        await executor.execute(mock_context, mock_event_queue)

        mock_agent.create_response_with_retry.assert_awaited_once()

    async def test_execute_no_input_raises(self, mock_event_queue):
        """Missing user input should raise ServerError."""
        from kubernetes_agent.a2a.executor import KubernetesAgentExecutor

        ctx = MagicMock()
        ctx.get_user_input.return_value = None

        executor = KubernetesAgentExecutor()
        with pytest.raises(ServerError):
            await executor.execute(ctx, mock_event_queue)

    @patch("kubernetes_agent.agent.KubernetesAgent")
    @patch("kubernetes_agent.a2a.executor.httpx.AsyncClient")
    async def test_execute_llm_failure_raises(
        self, mock_httpx, mock_agent_cls, mock_context, mock_event_queue
    ):
        """When LLM raises an unexpected exception, wrap in ServerError."""
        from kubernetes_agent.a2a.executor import KubernetesAgentExecutor

        mock_agent = MagicMock()
        mock_agent.create_response_with_retry = AsyncMock(
            side_effect=RuntimeError("LLM crash")
        )
        mock_agent_cls.return_value = mock_agent

        mock_client = AsyncMock()
        mock_client.post.return_value = MagicMock(
            status_code=200, json=MagicMock(return_value={"response": "", "sources": []})
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_httpx.return_value = mock_client

        executor = KubernetesAgentExecutor()
        with pytest.raises(ServerError):
            await executor.execute(mock_context, mock_event_queue)

    async def test_cancel_raises_unsupported(self, mock_context, mock_event_queue):
        """cancel() should raise ServerError with UnsupportedOperationError."""
        from kubernetes_agent.a2a.executor import KubernetesAgentExecutor

        executor = KubernetesAgentExecutor()
        with pytest.raises(ServerError):
            await executor.cancel(mock_context, mock_event_queue)

    @patch("kubernetes_agent.agent.KubernetesAgent")
    @patch("kubernetes_agent.a2a.executor.httpx.AsyncClient")
    async def test_execute_with_existing_task(
        self, mock_httpx, mock_agent_cls, mock_event_queue
    ):
        """When context has a current_task, don't enqueue a new task object."""
        from kubernetes_agent.a2a.executor import KubernetesAgentExecutor

        mock_agent = MagicMock()
        mock_agent.create_response_with_retry = AsyncMock(
            return_value=("Answer", False)
        )
        mock_agent_cls.return_value = mock_agent

        mock_client = AsyncMock()
        mock_client.post.return_value = MagicMock(
            status_code=200, json=MagicMock(return_value={"response": "", "sources": []})
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_httpx.return_value = mock_client

        ctx = MagicMock()
        ctx.get_user_input.return_value = "Help me"
        ctx.current_task = MagicMock(id="task-1", context_id="ctx-1")

        executor = KubernetesAgentExecutor()
        await executor.execute(ctx, mock_event_queue)

        # With existing task, the first enqueue_event (for the task itself) is skipped.
        # Only status updates (working + completed) are enqueued via TaskUpdater.
        calls = mock_event_queue.enqueue_event.call_args_list
        # Should not contain a raw Task object as the first call
        from a2a.types import Task
        for call in calls:
            arg = call[0][0] if call[0] else call.kwargs.get("event")
            if isinstance(arg, Task):
                pytest.fail("Should not enqueue a new Task when current_task exists")

    @patch("kubernetes_agent.agent.KubernetesAgent")
    @patch("kubernetes_agent.a2a.executor.httpx.AsyncClient")
    async def test_execute_server_error_reraise(
        self, mock_httpx, mock_agent_cls, mock_context, mock_event_queue
    ):
        """ServerError raised during execution is re-raised (not wrapped)."""
        from kubernetes_agent.a2a.executor import KubernetesAgentExecutor

        mock_agent = MagicMock()
        mock_agent.create_response_with_retry = AsyncMock(
            side_effect=ServerError(error=MagicMock(message="Server issue"))
        )
        mock_agent_cls.return_value = mock_agent

        mock_client = AsyncMock()
        mock_client.post.return_value = MagicMock(
            status_code=200, json=MagicMock(return_value={"response": "", "sources": []})
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_httpx.return_value = mock_client

        executor = KubernetesAgentExecutor()
        with pytest.raises(ServerError):
            await executor.execute(mock_context, mock_event_queue)

    @patch("kubernetes_agent.agent.KubernetesAgent")
    @patch("kubernetes_agent.a2a.executor.httpx.AsyncClient")
    async def test_execute_llm_response_failed_still_completes(
        self, mock_httpx, mock_agent_cls, mock_context, mock_event_queue
    ):
        """When LLM fails (returns default), executor still completes with that text."""
        from kubernetes_agent.a2a.executor import KubernetesAgentExecutor

        mock_agent = MagicMock()
        mock_agent.create_response_with_retry = AsyncMock(
            return_value=(
                "I apologize, but I'm having difficulty generating a response right now.",
                True,
            )
        )
        mock_agent_cls.return_value = mock_agent

        mock_client = AsyncMock()
        mock_client.post.return_value = MagicMock(
            status_code=200, json=MagicMock(return_value={"response": "", "sources": []})
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_httpx.return_value = mock_client

        executor = KubernetesAgentExecutor()
        await executor.execute(mock_context, mock_event_queue)

        mock_agent.create_response_with_retry.assert_awaited_once()

    @patch("kubernetes_agent.agent.KubernetesAgent")
    @patch("kubernetes_agent.a2a.executor.httpx.AsyncClient")
    async def test_execute_multiple_rag_sources(
        self, mock_httpx, mock_agent_cls, mock_context, mock_event_queue
    ):
        """RAG with multiple sources formats all of them in context."""
        from kubernetes_agent.a2a.executor import KubernetesAgentExecutor

        mock_agent = MagicMock()
        mock_agent.create_response_with_retry = AsyncMock(
            return_value=("Based on K8S-101 and K8S-102...", False)
        )
        mock_agent_cls.return_value = mock_agent

        sources = [
            {"id": "K8S-101", "similarity": 0.95, "content": "Fix A"},
            {"id": "K8S-102", "similarity": 0.88, "content": "Fix B"},
            {"id": "K8S-103", "similarity": 0.82, "content": "Fix C"},
            {"id": "K8S-104", "similarity": 0.70, "content": "Fix D"},
        ]
        mock_rag_resp = MagicMock()
        mock_rag_resp.status_code = 200
        mock_rag_resp.json.return_value = {"response": "Combined answer", "sources": sources}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_rag_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_httpx.return_value = mock_client

        executor = KubernetesAgentExecutor()
        await executor.execute(mock_context, mock_event_queue)

        call_args = mock_agent.create_response_with_retry.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages") or call_args[0][0]
        msg_content = messages[0]["content"] if isinstance(messages[0], dict) else str(messages[0])
        assert "K8S-101" in msg_content
