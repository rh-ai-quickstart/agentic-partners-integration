"""A2A AgentExecutor that wraps the existing specialist agent logic.

Each specialist agent:
1. Queries the RAG API for relevant knowledge
2. Builds an LLM prompt with RAG context
3. Calls the LLM to generate a response
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


class SpecialistAgentExecutor(AgentExecutor):
    """A2A executor for specialist agents (software-support, network-support).

    Wraps the existing specialist invocation logic: RAG query + LLM call.
    """

    def __init__(self, agent_name: str) -> None:
        self._agent_name = agent_name

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        logger.info(
            "A2A execute: agent=%s context_id=%s task_id=%s",
            self._agent_name,
            context.context_id,
            context.task_id,
        )

        user_input = context.get_user_input()
        if not user_input:
            raise ServerError(InvalidParamsError(message="No input message provided"))

        task_updater = await self._init_task(context, event_queue)

        try:
            await task_updater.update_status(
                TaskState.working,
                message=Message(
                    role=Role.agent,
                    parts=[Part(root=TextPart(text="Searching knowledge base..."))],
                    message_id=str(uuid.uuid4()),
                    task_id=task_updater.task_id,
                    context_id=task_updater.context_id,
                ),
                final=False,
            )

            response_text = await self._invoke_specialist(user_input)

            await task_updater.update_status(
                TaskState.completed,
                message=Message(
                    role=Role.agent,
                    parts=[Part(root=TextPart(text=response_text))],
                    metadata={"agent": self._agent_name},
                    message_id=str(uuid.uuid4()),
                    task_id=task_updater.task_id,
                    context_id=task_updater.context_id,
                ),
                final=True,
            )
        except ServerError:
            raise
        except Exception as exc:
            logger.exception("Specialist agent execution failed: %s", exc)
            raise ServerError(
                InternalError(message=f"Agent execution failed: {exc}")
            ) from exc

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        if (
            context.current_task
            and context.current_task.status.state
            in {TaskState.completed, TaskState.failed, TaskState.canceled}
        ):
            return
        raise ServerError(
            UnsupportedOperationError(message="Task cancellation is not supported.")
        )

    async def _init_task(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> TaskUpdater:
        task = context.current_task
        if not task:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)
        return TaskUpdater(event_queue, task.id, task.context_id)

    async def _invoke_specialist(self, user_message: str) -> str:
        """Run the full specialist flow: RAG lookup then LLM generation."""
        from agent_service.agents import AgentManager

        agent_manager = AgentManager()
        agent = agent_manager.get_agent(self._agent_name)

        rag_answer, rag_sources = await self._query_rag(user_message)

        source_refs = []
        for src in rag_sources:
            ticket_id = src.get("id", "unknown")
            similarity = src.get("similarity", 0)
            source_refs.append(f"[{ticket_id}] (similarity: {similarity:.1%})")

        rag_context = f"""## Knowledge Base Results

The following information was retrieved from the support knowledge base for the user's query:

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
                    "Based on the knowledge base results above, provide a helpful response to the user. "
                    "Reference the specific ticket IDs and solutions from the knowledge base. "
                    "If the knowledge base doesn't have relevant information, say so explicitly."
                ),
            }
        ]

        response_content, failed = await agent.create_response_with_retry(
            messages=messages,
            temperature=0.7,
            token_context=f"a2a-{self._agent_name}",
        )

        if failed:
            logger.warning("Agent %s response generation failed", self._agent_name)

        return response_content

    async def _query_rag(self, query: str) -> tuple[str, list[dict]]:
        """Query the RAG API and return (answer, sources)."""
        rag_endpoint = os.getenv(
            "RAG_API_ENDPOINT",
            "http://partner-rag-api-full:8080/answer",
        )

        logger.info("Querying RAG API: endpoint=%s query=%s", rag_endpoint, query[:100])

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
                    logger.error(
                        "RAG API returned %s: %s", resp.status_code, resp.text
                    )
                    raise ServerError(
                        InternalError(
                            message=f"RAG API unavailable (HTTP {resp.status_code})"
                        )
                    )

                data = resp.json()
                return data.get("response", ""), data.get("sources", [])

        except httpx.HTTPError as exc:
            logger.error("RAG API connection failed: %s", exc)
            raise ServerError(
                InternalError(message=f"RAG API unavailable: {exc}")
            ) from exc
