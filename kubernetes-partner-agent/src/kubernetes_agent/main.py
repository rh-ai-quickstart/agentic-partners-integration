"""Kubernetes Partner Agent — standalone FastAPI service.

This is a fully independent agent service that demonstrates the remote
partner agent pattern.  It serves:

- POST /api/v1/agents/kubernetes-support/invoke  — agent invocation
- GET  /health                                    — health check
- /a2a/kubernetes-support/                        — A2A protocol
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict

import httpx
from fastapi import FastAPI, HTTPException, status

from . import __version__
from .a2a.server import get_a2a_app
from .agent import KubernetesAgent, load_agent_config
from .schemas import AgentInvokeRequest, AgentInvokeResponse

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
logger = logging.getLogger("kubernetes-agent")

AGENT_NAME = "kubernetes-support"

app = FastAPI(
    title="Kubernetes Partner Agent",
    description="Standalone Kubernetes support agent — remote partner agent",
    version=__version__,
)

# Mount A2A protocol endpoint
_config = load_agent_config()
app.mount(f"/a2a/{AGENT_NAME}", get_a2a_app(AGENT_NAME, _config))


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "kubernetes-partner-agent",
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post(
    f"/api/v1/agents/{AGENT_NAME}/invoke",
    response_model=AgentInvokeResponse,
)
async def invoke_agent(request: AgentInvokeRequest) -> AgentInvokeResponse:
    """Invoke the Kubernetes support agent.

    Follows the same contract as agent-service /invoke endpoints so
    the request-manager can route here seamlessly.
    """
    logger.info(
        "Invoke: session=%s user=%s len=%d",
        request.session_id,
        request.user_id,
        len(request.message),
    )

    try:
        agent = KubernetesAgent()

        # Query RAG API for relevant knowledge
        rag_endpoint = os.getenv(
            "RAG_API_ENDPOINT",
            "http://partner-rag-api-full:8080/answer",
        )

        rag_sources: list[dict] = []

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                rag_response = await client.post(
                    rag_endpoint,
                    json={
                        "user_query": request.message,
                        "num_sources": 3,
                        "only_high_similarity_nodes": False,
                    },
                )
                if rag_response.status_code == 200:
                    rag_data = rag_response.json()
                    rag_sources = rag_data.get("sources", [])
                else:
                    logger.warning("RAG API returned %s", rag_response.status_code)
        except httpx.HTTPError as e:
            logger.warning("RAG API unavailable: %s", e)

        # Build source references
        source_refs = []
        for src in rag_sources:
            ticket_id = src.get("id", "unknown")
            similarity = src.get("similarity", 0)
            source_refs.append(f"[{ticket_id}] (similarity: {similarity:.1%})")

        # Build LLM prompt with RAG context
        rag_context = f"""## Knowledge Base Results

The following information was retrieved from the support knowledge base:

### Relevant Support Tickets:
{chr(10).join(source_refs) if source_refs else "No matching sources found."}

### Ticket Details:
"""
        for src in rag_sources[:3]:
            rag_context += (
                f"\n**{src.get('id', 'unknown')}:**\n"
                f"{src.get('content', '')[:500]}\n"
            )

        # Build messages with conversation history
        transfer_ctx = request.transfer_context or {}
        conversation_history = transfer_ctx.get("conversation_history", [])

        messages = []
        for turn in conversation_history[-10:]:
            role = turn.get("role", "user")
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": turn.get("content", "")})

        messages.append(
            {
                "role": "user",
                "content": (
                    f"User Query: {request.message}\n\n{rag_context}\n\n"
                    "Based on the knowledge base results above, provide a helpful response. "
                    "Reference the specific ticket IDs and solutions from the knowledge base. "
                    "If the knowledge base doesn't have relevant information, say so explicitly."
                ),
            }
        )

        response_content, failed = await agent.create_response_with_retry(
            messages=messages, temperature=0.7
        )

        if failed:
            logger.warning(
                "Response generation failed for session=%s", request.session_id
            )

        logger.info(
            "Completed: session=%s len=%d rag_sources=%d",
            request.session_id,
            len(response_content),
            len(rag_sources),
        )

        return AgentInvokeResponse(
            content=response_content,
            agent_id=AGENT_NAME,
            session_id=request.session_id,
            routing_decision=None,
            metadata={
                **(request.transfer_context or {}),
                "rag_sources": [s.get("id") for s in rag_sources],
                "rag_source_count": len(rag_sources),
                "rag_used": bool(rag_sources),
            },
        )

    except Exception as e:
        logger.error("Invocation failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent invocation failed: {e}",
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "0.0.0.0")

    uvicorn.run(
        "kubernetes_agent.main:app",
        host=host,
        port=port,
        reload=os.getenv("RELOAD", "false").lower() == "true",
        log_level="info",
    )
