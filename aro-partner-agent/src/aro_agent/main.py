"""ARO Partner Agent — standalone FastAPI service with MCP tool calling.

This is a fully independent agent service that demonstrates the
MCP-powered remote partner agent pattern.  It serves:

- POST /api/v1/agents/aro-support/invoke  — agent invocation
- GET  /health                             — health check
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, status

from . import __version__
from .agent import AROAgent, load_agent_config
from .schemas import AgentInvokeRequest, AgentInvokeResponse

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
logger = logging.getLogger("aro-agent")

AGENT_NAME = "aro-support"

app = FastAPI(
    title="ARO Partner Agent",
    description="Standalone ARO support agent with MCP tool calling",
    version=__version__,
)


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    config = load_agent_config()
    mcp_configs = config.get("mcp_servers", [])
    return {
        "status": "healthy",
        "service": "aro-partner-agent",
        "version": __version__,
        "mcp_configured": bool(mcp_configs) or bool(os.getenv("MCP_SERVER_URL")),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post(
    f"/api/v1/agents/{AGENT_NAME}/invoke",
    response_model=AgentInvokeResponse,
)
async def invoke_agent(request: AgentInvokeRequest) -> AgentInvokeResponse:
    """Invoke the ARO support agent.

    Follows the same A2A contract as all agents in the quickstart
    so the request-manager can route here seamlessly.

    When an MCP server is configured, the agent uses LLM tool calling
    to interact with Azure services (AI Search, Blob Storage, AKS, etc.)
    through the MCP protocol.
    """
    logger.info(
        "Invoke: session=%s user=%s len=%d",
        request.session_id,
        request.user_id,
        len(request.message),
    )

    try:
        agent = AROAgent()

        transfer_ctx = request.transfer_context or {}
        conversation_history = transfer_ctx.get("conversation_history", [])

        messages: list[dict[str, str]] = []
        for turn in conversation_history[-10:]:
            role = turn.get("role", "user")
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": turn.get("content", "")})

        messages.append({"role": "user", "content": request.message})

        response_content, failed, tool_calls = await agent.create_response_with_retry(
            messages=messages, temperature=0.7
        )

        if failed:
            logger.warning(
                "Response generation failed for session=%s", request.session_id
            )

        logger.info(
            "Completed: session=%s len=%d mcp=%s tools=%d",
            request.session_id,
            len(response_content),
            bool(agent.mcp_server_url),
            len(tool_calls),
        )

        return AgentInvokeResponse(
            content=response_content,
            agent_id=AGENT_NAME,
            session_id=request.session_id,
            routing_decision=None,
            metadata={
                **(request.transfer_context or {}),
                "mcp_enabled": bool(agent.mcp_server_url),
                "mcp_tool_calls": tool_calls,
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
        "aro_agent.main:app",
        host=host,
        port=port,
        reload=os.getenv("RELOAD", "false").lower() == "true",
        log_level="info",
    )
