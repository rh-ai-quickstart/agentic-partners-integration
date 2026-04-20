"""Agent Service for Partner Agent Integration."""

import os
from datetime import datetime, timezone
from typing import Any, Dict

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, status
from shared_models import (
    configure_logging,
    create_shared_lifespan,
    get_db_session_dependency,
    simple_health_check,
)
from shared_models.audit import AuditService
from sqlalchemy.ext.asyncio import AsyncSession

from . import __version__
from .agents import AgentManager
from .schemas import AgentInvokeRequest, AgentInvokeResponse

# Configure structured logging
SERVICE_NAME = "agent-service"
logger = configure_logging(SERVICE_NAME)


# Create lifespan using shared utility
def lifespan(app: FastAPI) -> Any:
    return create_shared_lifespan(
        service_name="agent-service",
        version=__version__,
    )


# Create FastAPI application
app = FastAPI(
    title="Partner Agent Service",
    description="Agent Service for Partner Agent Integration",
    version=__version__,
    lifespan=lifespan,
)

# Mount standard A2A protocol endpoints for each specialist agent.
# These serve agent cards at /.well-known/agent.json and accept
# JSON-RPC messages at / under each mount prefix.
# Agents are discovered dynamically from YAML configs.
from .a2a.server import get_a2a_app
from .agents import AgentManager as _AgentManagerForA2A

_a2a_manager = _AgentManagerForA2A()
for _name, _config in _a2a_manager.get_specialist_agents().items():
    app.mount(f"/a2a/{_name}", get_a2a_app(_name, _config))

# Add SPIFFE identity middleware — extracts caller identity from
# X-SPIFFE-ID header (mock mode) or mTLS peer certificate (production).
from shared_models.identity_middleware import IdentityMiddleware

app.add_middleware(IdentityMiddleware)


def _enforce_agent_auth() -> bool:
    """Check if agent authentication enforcement is enabled.

    When enabled (default), the /invoke endpoint requires callers to provide
    a SPIFFE identity and verifies authorization via OPA when delegation
    context is present. Disable with ENFORCE_AGENT_AUTH=false for testing.
    """
    return os.getenv("ENFORCE_AGENT_AUTH", "true").lower() == "true"


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """Health check endpoint - lightweight without database dependency."""
    return {
        "status": "healthy",
        "service": "agent-service",
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health/detailed")
async def detailed_health_check(
    db: AsyncSession = Depends(get_db_session_dependency),
) -> Dict[str, Any]:
    """Detailed health check with database dependency for monitoring."""
    return dict(
        await simple_health_check(
            service_name="agent-service",
            version=__version__,
            db=db,
        )
    )


@app.get("/api/v1/agents/registry")
async def agent_registry() -> Dict[str, Any]:
    """Return the agent registry for service discovery.

    Request-manager calls this endpoint to learn where each specialist
    agent lives (local or remote) so it can route A2A calls to the
    correct URL.  The response includes endpoints, departments, and
    descriptions derived from the agent YAML configs.
    """
    from .agents import AgentManager

    agent_manager = AgentManager()
    specialists = agent_manager.get_specialist_agents()
    dept_map = agent_manager.get_agent_dept_map()
    descriptions = agent_manager.get_agent_descriptions()

    agents: Dict[str, Any] = {}
    for name, config in specialists.items():
        entry: Dict[str, Any] = {
            "departments": dept_map.get(name, []),
            "description": descriptions.get(name, ""),
        }
        explicit_endpoint = config.get("endpoint")
        if explicit_endpoint:
            entry["endpoint"] = explicit_endpoint.rstrip("/")
        agents[name] = entry

    return {"agents": agents}


@app.post("/api/v1/agents/{agent_name}/invoke", response_model=AgentInvokeResponse)
async def invoke_agent(
    agent_name: str,
    request: AgentInvokeRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db_session_dependency),
) -> AgentInvokeResponse:
    """Invoke a specific agent directly via HTTP for A2A communication.

    This endpoint enables agent-to-agent communication by allowing
    one agent (or Request Manager) to invoke another agent synchronously.

    DIRECT INVOCATION (POC): When called with a specialist agent name
    (software-support, network-support), this endpoint directly invokes
    that agent's LLM capabilities, bypassing the routing-agent flow.

    Security: When ENFORCE_AGENT_AUTH=true (default), requires caller
    identity via X-SPIFFE-ID header or mTLS. When delegation headers
    are present (X-Delegation-User), verifies authorization via OPA
    using the permission intersection model.

    Args:
        agent_name: Name of the agent to invoke (routing-agent, software-support, etc.)
        request: Agent invocation request containing session, user, and message
        http_request: The underlying HTTP request (for identity/header extraction)
        db: Database session dependency

    Returns:
        Agent response with content and optional routing decision

    Raises:
        HTTPException: If agent not found, invocation fails, or authorization denied
    """
    logger.info(
        "Agent invocation request",
        agent_name=agent_name,
        session_id=request.session_id,
        user_id=request.user_id,
        message_length=len(request.message),
    )

    # ── Caller identity & authorization enforcement ─────────────────────
    if _enforce_agent_auth():
        identity = getattr(http_request.state, "identity", None)
        if not identity:
            logger.warning(
                "Agent invocation rejected: no caller identity",
                agent_name=agent_name,
                session_id=request.session_id,
            )
            await AuditService.emit(
                event_type="authz.no_identity",
                actor="unknown",
                action="invoke_agent",
                resource=agent_name,
                outcome="failure",
                reason="No caller identity provided",
                metadata={"session_id": request.session_id, "user_id": request.user_id},
                service="agent-service",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Caller identity required — provide X-SPIFFE-ID header "
                    "or mTLS certificate"
                ),
            )

        # If delegation context is present (a service acting on behalf of
        # a user), verify authorization via OPA. Without delegation headers,
        # this is a plain service-to-service call (OPA Rule 1: allowed).
        delegation_user = http_request.headers.get("X-Delegation-User")
        if delegation_user:
            transfer_ctx = request.transfer_context or {}
            delegation_departments = transfer_ctx.get("departments", [])

            from shared_models.identity import make_spiffe_id
            from shared_models.opa_client import (
                Delegation,
                check_agent_authorization,
            )

            delegation = Delegation(
                user_spiffe_id=delegation_user,
                agent_spiffe_id=make_spiffe_id("agent", agent_name),
                user_departments=delegation_departments,
            )

            opa_decision = await check_agent_authorization(
                caller_spiffe_id=identity.spiffe_id,
                agent_name=agent_name,
                delegation=delegation,
            )

            if not opa_decision.allow:
                logger.warning(
                    "Agent invocation rejected by OPA",
                    agent_name=agent_name,
                    caller=identity.spiffe_id,
                    delegation_user=delegation_user,
                    reason=opa_decision.reason,
                )
                await AuditService.emit(
                    event_type="authz.deny",
                    actor=delegation_user,
                    action="invoke_agent",
                    resource=agent_name,
                    outcome="failure",
                    reason=opa_decision.reason,
                    metadata={
                        "caller": identity.spiffe_id,
                        "departments": delegation_departments,
                        "layer": "defense-in-depth",
                    },
                    service="agent-service",
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Authorization denied: {opa_decision.reason}",
                )

            logger.info(
                "Agent invocation authorized by OPA",
                agent_name=agent_name,
                caller=identity.spiffe_id,
                effective_departments=opa_decision.effective_departments,
            )
            await AuditService.emit(
                event_type="authz.allow",
                actor=delegation_user,
                action="invoke_agent",
                resource=agent_name,
                outcome="success",
                metadata={
                    "caller": identity.spiffe_id,
                    "effective_departments": opa_decision.effective_departments,
                    "layer": "defense-in-depth",
                },
                service="agent-service",
            )

    try:
        if agent_name == "routing-agent":
            # Single-turn routing: Use the routing-agent's LLM with a routing-aware
            # system prompt to classify and respond in one call.
            # The LLM either handles the message conversationally OR returns a
            # routing marker for delegation to a specialist agent.
            logger.info(
                "Routing-agent single-turn evaluation",
                agent_name=agent_name,
                session_id=request.session_id,
            )

            from .agents import AgentManager

            agent_manager = AgentManager()
            agent = agent_manager.get_agent(agent_name)

            # Extract user's departments from transfer_context for routing decisions.
            # Authorization enforcement happens in request-manager via OPA;
            # here we use departments for LLM prompt steering (soft gate).
            transfer_ctx = request.transfer_context or {}
            user_departments = transfer_ctx.get("departments", [])

            # Load specialist agent capabilities dynamically from YAML configs.
            # Each agent YAML defines its departments and description — the
            # AgentManager is the single source of truth.
            agent_dept_map = agent_manager.get_agent_dept_map()
            agent_descriptions = agent_manager.get_agent_descriptions()

            all_specialists = list(agent_dept_map.keys())
            accessible_agents = [
                agent
                for agent, required_depts in agent_dept_map.items()
                if any(d in user_departments for d in required_depts)
            ]

            if accessible_agents:
                agents_section = "\n".join(
                    f"- {agent}: {agent_descriptions[agent]}"
                    for agent in accessible_agents
                )
            else:
                agents_section = "(No specialist agents available for this user)"

            # Build list of agents the user CANNOT access for denial instructions
            blocked_agents = [a for a in all_specialists if a not in accessible_agents]
            if blocked_agents:
                blocked_section = (
                    "\n\nIMPORTANT ACCESS RESTRICTION: The user does NOT have access to: "
                    + ", ".join(blocked_agents)
                    + ". "
                    "If the user's question relates to a blocked agent, do NOT use ROUTE:. "
                    "Instead, respond politely explaining that they don't have access to that "
                    "specialist and suggest they contact their administrator for access."
                )
            else:
                blocked_section = ""

            # Build dynamic routing rules from accessible agents
            routing_rules = []
            routing_rules.append(
                "1. If the message is a greeting, chitchat, or general conversation "
                '(like "Hello", "Hi", "How are you", "Thanks"), respond '
                "conversationally as a friendly routing agent. Introduce yourself "
                "briefly and ask how you can help."
            )
            for i, agent_name_item in enumerate(accessible_agents, start=2):
                desc = agent_descriptions.get(agent_name_item, "")
                routing_rules.append(
                    f"{i}. If the message relates to: {desc}, AND the user has "
                    f"access to {agent_name_item}, respond with EXACTLY this format:\n"
                    f"   ROUTE:{agent_name_item}\n"
                    f"   I'll connect you with our {agent_name_item.replace('-', ' ')} specialist to help with your issue."
                )
            next_rule = len(accessible_agents) + 2
            routing_rules.append(
                f"{next_rule}. If unclear, ask clarifying questions to determine the right specialist."
            )
            routing_rules.append(
                f"{next_rule + 1}. Use conversation history to understand follow-up questions. "
                "If the user references something from earlier, use that context."
            )
            rules_section = "\n".join(routing_rules)

            routing_system_prompt = f"""You are a routing agent for a support system. Analyze the user's message and decide how to respond.

Specialist agents the user has access to:
{agents_section}

RULES:
{rules_section}

IMPORTANT: Only use the ROUTE: prefix when you are confident the message needs a specialist AND the user has access to that specialist. For greetings and general chat, just respond normally without any ROUTE: prefix.{blocked_section}"""

            # Build messages with conversation history
            conversation_history = transfer_ctx.get("conversation_history", [])
            messages = [
                {"role": "system", "content": routing_system_prompt},
            ]
            # Add prior conversation turns for context
            for turn in conversation_history[-20:]:
                role = turn.get("role", "user")
                if role in ("user", "assistant"):
                    messages.append({"role": role, "content": turn.get("content", "")})
            # Add current user message
            messages.append({"role": "user", "content": request.message})

            response_content, failed = await agent.create_response_with_retry(
                messages=messages,
                temperature=0.1,  # Deterministic for routing
                token_context=request.session_id,
            )

            # Parse response for routing decision
            routing_decision = None
            handling_agent = "routing-agent"

            if response_content.startswith("ROUTE:"):
                lines = response_content.split("\n", 1)
                route_line = lines[0].strip()
                target = route_line.replace("ROUTE:", "").strip()

                available_agents = list(agent_manager.agents_dict.keys())
                if target in available_agents:
                    routing_decision = target
                    # Use the message after the ROUTE line as the response
                    response_content = (
                        lines[1].strip()
                        if len(lines) > 1
                        else f"I'll connect you with our {target.replace('-', ' ')} specialist."
                    )
                    logger.info(
                        "Routing decision detected",
                        routing_decision=routing_decision,
                        response_preview=response_content[:100],
                    )

            logger.info(
                "Routing-agent evaluation completed",
                agent_name=agent_name,
                handling_agent=handling_agent,
                routing_decision=routing_decision,
                session_id=request.session_id,
                response_length=len(response_content),
            )

            return AgentInvokeResponse(
                content=response_content,
                agent_id=handling_agent,
                session_id=request.session_id,
                routing_decision=routing_decision,
                metadata={
                    **(request.transfer_context or {}),
                    "handling_agent": (
                        handling_agent if not routing_decision else routing_decision
                    ),
                    "routing_reason": (
                        f"Delegated to {routing_decision}"
                        if routing_decision
                        else "Handled by routing-agent"
                    ),
                },
            )
        else:
            # Specialist agent invocation with mandatory RAG
            # 1. Query RAG API for relevant knowledge
            # 2. Include RAG context in the LLM prompt
            # 3. FAIL if RAG is unavailable (no silent degradation)
            from .agents import AgentManager

            agent_manager = AgentManager()

            try:
                agent = agent_manager.get_agent(agent_name)
                logger.info(
                    "Specialist agent invocation with RAG",
                    agent_name=agent_name,
                    agent_found=True,
                )
            except ValueError as e:
                logger.error(
                    "Agent not found",
                    agent_name=agent_name,
                    error=str(e),
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Agent '{agent_name}' not found",
                )

            # Query RAG API - this MUST succeed for the POC
            rag_endpoint = os.getenv(
                "RAG_API_ENDPOINT", "http://partner-rag-api-full:8080/answer"
            )

            logger.info(
                "Querying RAG API",
                agent_name=agent_name,
                rag_endpoint=rag_endpoint,
                query=request.message[:100],
            )

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

                    if rag_response.status_code != 200:
                        error_text = rag_response.text
                        logger.error(
                            "RAG API call failed",
                            status=rag_response.status_code,
                            error=error_text,
                            agent_name=agent_name,
                        )
                        raise HTTPException(
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"RAG API unavailable (status {rag_response.status_code}): {error_text}",
                        )

                    rag_data = rag_response.json()
                    rag_answer = (
                        rag_data.get("response", "") or ""
                    )  # Handle None response
                    rag_sources = rag_data.get("sources", [])

                    logger.info(
                        "RAG API response received",
                        agent_name=agent_name,
                        source_count=len(rag_sources),
                    )

            except httpx.HTTPError as e:
                logger.error(
                    "RAG API connection failed",
                    error=str(e),
                    rag_endpoint=rag_endpoint,
                    agent_name=agent_name,
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"RAG API unavailable: {str(e)}",
                )

            # Build source references for the response
            source_refs = []
            for src in rag_sources:
                ticket_id = src.get("id", "unknown")
                similarity = src.get("similarity", 0)
                source_refs.append(f"[{ticket_id}] (similarity: {similarity:.1%})")

            # Build LLM prompt with RAG context
            rag_context = f"""## Knowledge Base Results

The following information was retrieved from the support knowledge base for the user's query:

### Relevant Support Tickets:
{chr(10).join(source_refs) if source_refs else "No matching sources found."}

### Ticket Details:
"""
            for src in rag_sources[:3]:
                rag_context += f"\n**{src.get('id', 'unknown')}:**\n{src.get('content', '')[:500]}\n"

            # Build messages with conversation history for follow-up context
            specialist_transfer_ctx = request.transfer_context or {}
            conversation_history = specialist_transfer_ctx.get(
                "conversation_history", []
            )

            messages = []
            # Add prior conversation turns so the specialist can handle follow-ups
            for turn in conversation_history[-10:]:
                role = turn.get("role", "user")
                if role in ("user", "assistant"):
                    messages.append({"role": role, "content": turn.get("content", "")})

            # Add current user query with RAG context
            messages.append(
                {
                    "role": "user",
                    "content": f"""User Query: {request.message}

{rag_context}

Based on the knowledge base results above, provide a helpful response to the user.
Reference the specific ticket IDs and solutions from the knowledge base.
If the knowledge base doesn't have relevant information, say so explicitly.""",
                }
            )

            # Invoke agent with RAG-augmented context
            response_content, failed = await agent.create_response_with_retry(
                messages=messages,
                temperature=0.7,
                token_context=request.session_id,
            )

            if failed:
                logger.warning(
                    "Agent response generation failed",
                    agent_name=agent_name,
                    session_id=request.session_id,
                )

            logger.info(
                "Specialist agent invocation completed with RAG",
                agent_name=agent_name,
                session_id=request.session_id,
                response_length=len(response_content),
                rag_sources=len(rag_sources),
                failed=failed,
            )

            return AgentInvokeResponse(
                content=response_content,
                agent_id=agent_name,
                session_id=request.session_id,
                routing_decision=None,
                metadata={
                    **(request.transfer_context or {}),
                    "rag_sources": [s.get("id") for s in rag_sources],
                    "rag_source_count": len(rag_sources),
                    "rag_used": True,
                },
            )

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(
            "Agent invocation failed",
            agent_name=agent_name,
            session_id=request.session_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent invocation failed: {str(e)}",
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "0.0.0.0")

    uvicorn.run(
        "agent_service.main:app",
        host=host,
        port=port,
        reload=os.getenv("RELOAD", "false").lower() == "true",
        log_level="info",
    )
