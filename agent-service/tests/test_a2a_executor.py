"""Tests for agent_service.a2a.executor."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import (
    TaskState,
)
from a2a.utils.errors import (
    A2AError,
    InternalError,
    InvalidParamsError,
    UnsupportedOperationError,
)

from agent_service.a2a.executor import SpecialistAgentExecutor


@pytest.fixture
def executor():
    return SpecialistAgentExecutor(agent_name="software-support")


@pytest.fixture
def mock_context():
    ctx = MagicMock()
    ctx.context_id = "ctx-1"
    ctx.task_id = "task-1"
    ctx.get_user_input.return_value = "My app is crashing"
    ctx.current_task = None
    ctx.message = MagicMock()
    return ctx


@pytest.fixture
def mock_event_queue():
    eq = AsyncMock()
    return eq


class TestSpecialistAgentExecutor:
    """Tests for the SpecialistAgentExecutor."""

    @patch(
        "agent_service.a2a.executor.SpecialistAgentExecutor._invoke_specialist",
        new_callable=AsyncMock,
    )
    async def test_execute_invokes_specialist(
        self, mock_invoke, executor, mock_context, mock_event_queue
    ):
        mock_invoke.return_value = "Here is the solution"

        await executor.execute(mock_context, mock_event_queue)

        mock_invoke.assert_awaited_once_with("My app is crashing")

    async def test_execute_handles_missing_user_input(
        self, executor, mock_context, mock_event_queue
    ):
        mock_context.get_user_input.return_value = None

        with pytest.raises(A2AError):
            await executor.execute(mock_context, mock_event_queue)

    async def test_cancel_raises_unsupported(
        self, executor, mock_context, mock_event_queue
    ):
        mock_context.current_task = None
        with pytest.raises(A2AError):
            await executor.cancel(mock_context, mock_event_queue)

    async def test_cancel_returns_if_task_completed(
        self, executor, mock_context, mock_event_queue
    ):
        mock_task = MagicMock()
        mock_task.status.state = TaskState.TASK_STATE_COMPLETED
        mock_context.current_task = mock_task

        await executor.cancel(mock_context, mock_event_queue)

    async def test_cancel_returns_if_task_failed(
        self, executor, mock_context, mock_event_queue
    ):
        mock_task = MagicMock()
        mock_task.status.state = TaskState.TASK_STATE_FAILED
        mock_context.current_task = mock_task

        await executor.cancel(mock_context, mock_event_queue)

    @patch("agent_service.agents.AgentManager")
    @patch(
        "agent_service.a2a.executor.SpecialistAgentExecutor._query_rag",
        new_callable=AsyncMock,
    )
    async def test_invoke_specialist_calls_rag_and_agent(
        self, mock_query_rag, mock_agent_manager_cls, executor
    ):
        mock_query_rag.return_value = (
            "RAG answer",
            [{"id": "T-1", "similarity": 0.9, "content": "Fix it"}],
        )

        mock_agent = AsyncMock()
        mock_agent.create_response_with_retry.return_value = ("Solution text", False)
        mock_manager = MagicMock()
        mock_manager.get_agent.return_value = mock_agent
        mock_agent_manager_cls.return_value = mock_manager

        result = await executor._invoke_specialist("My app crashes")

        mock_query_rag.assert_awaited_once_with("My app crashes")
        mock_agent.create_response_with_retry.assert_awaited_once()
        assert result == "Solution text"

    @patch("agent_service.a2a.executor.httpx.AsyncClient")
    async def test_query_rag_calls_endpoint(
        self, mock_httpx_cls, executor, monkeypatch
    ):
        monkeypatch.setenv("RAG_API_ENDPOINT", "http://rag:8080/answer")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": "RAG says fix it",
            "sources": [{"id": "T-1", "similarity": 0.95}],
        }

        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_httpx_cls.return_value = mock_client_instance

        answer, sources = await executor._query_rag("app crash")

        assert answer == "RAG says fix it"
        assert len(sources) == 1
        assert sources[0]["id"] == "T-1"

    @patch("agent_service.a2a.executor.httpx.AsyncClient")
    async def test_query_rag_handles_errors(
        self, mock_httpx_cls, executor, monkeypatch
    ):
        monkeypatch.setenv("RAG_API_ENDPOINT", "http://rag:8080/answer")

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_httpx_cls.return_value = mock_client_instance

        with pytest.raises(A2AError):
            await executor._query_rag("app crash")

    @patch(
        "agent_service.a2a.executor.SpecialistAgentExecutor._invoke_specialist",
        new_callable=AsyncMock,
    )
    async def test_execute_reraises_a2a_error(
        self, mock_invoke, executor, mock_context, mock_event_queue
    ):
        """A2AError raised during execution is re-raised as-is."""
        mock_invoke.side_effect = InternalError(message="RAG API unavailable")

        with pytest.raises(A2AError):
            await executor.execute(mock_context, mock_event_queue)

    @patch(
        "agent_service.a2a.executor.SpecialistAgentExecutor._invoke_specialist",
        new_callable=AsyncMock,
    )
    async def test_execute_wraps_generic_exception_in_internal_error(
        self, mock_invoke, executor, mock_context, mock_event_queue
    ):
        """Generic exception is wrapped in InternalError."""
        mock_invoke.side_effect = ValueError("unexpected problem")

        with pytest.raises(InternalError) as exc_info:
            await executor.execute(mock_context, mock_event_queue)

        assert "Agent execution failed" in str(exc_info.value)

    @patch("agent_service.agents.AgentManager")
    @patch(
        "agent_service.a2a.executor.SpecialistAgentExecutor._query_rag",
        new_callable=AsyncMock,
    )
    async def test_invoke_specialist_logs_warning_on_failed_response(
        self, mock_query_rag, mock_agent_manager_cls, executor
    ):
        """Warning is logged when agent response generation fails."""
        mock_query_rag.return_value = (
            "RAG answer",
            [{"id": "T-1", "similarity": 0.9, "content": "Fix it"}],
        )

        mock_agent = AsyncMock()
        mock_agent.create_response_with_retry.return_value = (
            "I apologize, but I'm having difficulty generating a response right now.",
            True,
        )
        mock_manager = MagicMock()
        mock_manager.get_agent.return_value = mock_agent
        mock_agent_manager_cls.return_value = mock_manager

        result = await executor._invoke_specialist("My app crashes")

        assert "apologize" in result.lower()

    @patch("agent_service.a2a.executor.httpx.AsyncClient")
    async def test_query_rag_handles_httpx_connection_error(
        self, mock_httpx_cls, executor, monkeypatch
    ):
        """httpx.HTTPError is caught and wrapped in InternalError."""
        import httpx

        monkeypatch.setenv("RAG_API_ENDPOINT", "http://rag:8080/answer")

        mock_client_instance = AsyncMock()
        mock_client_instance.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_httpx_cls.return_value = mock_client_instance

        with pytest.raises(InternalError) as exc_info:
            await executor._query_rag("app crash")

        assert "RAG API unavailable" in str(exc_info.value)

    async def test_cancel_returns_if_task_canceled(
        self, executor, mock_context, mock_event_queue
    ):
        """cancel returns early when task state is 'canceled'."""
        mock_task = MagicMock()
        mock_task.status.state = TaskState.TASK_STATE_CANCELED
        mock_context.current_task = mock_task

        await executor.cancel(mock_context, mock_event_queue)
