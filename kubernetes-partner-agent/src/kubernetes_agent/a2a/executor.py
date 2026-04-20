"""A2A AgentExecutor for the Kubernetes partner agent.

Handles A2A protocol requests by:
1. Querying the RAG API for relevant knowledge
2. Invoking the LLM with RAG context
"""

import logging
import os
import uuid

import httpx
from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import (
    InternalError,
    InvalidParamsError,
    Message,
    Part,
    Role,
    TaskState,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import new_task
from a2a.utils.errors import ServerError

logger = logging.getLogger(__name__)


class KubernetesAgentExecutor(AgentExecutor):
    """A2A executor for the Kubernetes support agent."""

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        user_input = context.get_user_input()
        if not user_input:
            raise ServerError(InvalidParamsError(message="No input message provided"))

        task = context.current_task or new_task(context.message)
        if not context.current_task:
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)

        try:
            await updater.update_status(
                TaskState.working,
                message=Message(
                    role=Role.agent,
                    parts=[Part(root=TextPart(text="Searching knowledge base..."))],
                    message_id=str(uuid.uuid4()),
                    task_id=updater.task_id,
                    context_id=updater.context_id,
                ),
                final=False,
            )

            response_text = await self._invoke(user_input)

            await updater.update_status(
                TaskState.completed,
                message=Message(
                    role=Role.agent,
                    parts=[Part(root=TextPart(text=response_text))],
                    metadata={"agent": "kubernetes-support"},
                    message_id=str(uuid.uuid4()),
                    task_id=updater.task_id,
                    context_id=updater.context_id,
                ),
                final=True,
            )
        except ServerError:
            raise
        except Exception as exc:
            logger.exception("Agent execution failed: %s", exc)
            raise ServerError(
                InternalError(message=f"Agent execution failed: {exc}")
            ) from exc

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        raise ServerError(
            UnsupportedOperationError(message="Task cancellation is not supported.")
        )

    async def _invoke(self, user_message: str) -> str:
        """Run the full flow: RAG lookup then LLM generation."""
        from kubernetes_agent.agent import KubernetesAgent

        agent = KubernetesAgent()

        rag_answer, rag_sources = await self._query_rag(user_message)

        source_refs = []
        for src in rag_sources:
            ticket_id = src.get("id", "unknown")
            similarity = src.get("similarity", 0)
            source_refs.append(f"[{ticket_id}] (similarity: {similarity:.1%})")

        rag_context = f"""## Knowledge Base Results

The following information was retrieved from the support knowledge base:

### RAG Answer:
{rag_answer}

### Sources:
{chr(10).join(source_refs) if source_refs else "No matching sources found."}

### Source Details:
"""
        for src in rag_sources[:3]:
            rag_context += (
                f"\n**{src.get('id', 'unknown')}:**\n"
                f"{src.get('content', '')[:500]}\n"
            )

        messages = [
            {
                "role": "user",
                "content": (
                    f"User Query: {user_message}\n\n{rag_context}\n\n"
                    "Based on the knowledge base results above, provide a helpful response. "
                    "Reference the specific ticket IDs and solutions from the knowledge base. "
                    "If the knowledge base doesn't have relevant information, say so explicitly."
                ),
            }
        ]

        response_content, failed = await agent.create_response_with_retry(
            messages=messages, temperature=0.7
        )

        if failed:
            logger.warning("Kubernetes agent response generation failed")

        return response_content

    async def _query_rag(self, query: str) -> tuple[str, list[dict]]:
        """Query the RAG API and return (answer, sources)."""
        rag_endpoint = os.getenv(
            "RAG_API_ENDPOINT",
            "http://partner-rag-api-full:8080/answer",
        )

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    rag_endpoint,
                    json={
                        "user_query": query,
                        "num_sources": 3,
                        "only_high_similarity_nodes": False,
                    },
                )

                if resp.status_code != 200:
                    logger.error("RAG API returned %s: %s", resp.status_code, resp.text)
                    return "", []

                data = resp.json()
                return data.get("response", ""), data.get("sources", [])

        except httpx.HTTPError as exc:
            logger.warning("RAG API unavailable, proceeding without context: %s", exc)
            return "", []
