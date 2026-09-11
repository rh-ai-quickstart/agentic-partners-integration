"""
Enhanced HTTP client for invoking agents with structured context support.

This extends the base AgentClient with conversation history tracking
and structured context extraction support.
"""

import os
from typing import Any, Dict, List, Optional

import httpx
import jwt
from shared_models import configure_logging
from shared_models.identity import make_spiffe_id, outbound_identity_headers

from .credential_service import CredentialService
from .token_exchange import TokenExchangeClient, TokenExchangeError

logger = configure_logging("request-manager")

# Feature flag
STRUCTURED_CONTEXT_ENABLED = (
    os.getenv("STRUCTURED_CONTEXT_ENABLED", "true").lower() == "true"
)


class EnhancedAgentClient:
    """
    Enhanced HTTP client for agent-to-agent communication with structured context.

    This client extends the basic agent invocation with:
    - Conversation history tracking
    - Previous agent tracking (for context optimization)
    - Structured context extraction support
    - Automatic credential propagation
    """

    def __init__(
        self,
        agent_service_url: str = "http://agent-service:8080",
        timeout: float = 120.0,
        agent_endpoints: Optional[Dict[str, str]] = None,
    ):
        """
        Initialize enhanced agent client.

        Args:
            agent_service_url: Base URL for agent service (default for local agents)
            timeout: Request timeout in seconds
            agent_endpoints: Optional mapping of agent name -> full invoke URL
                for remote agents. When an agent appears here, its URL is used
                instead of constructing one from ``agent_service_url``.
        """
        self.agent_service_url = agent_service_url.rstrip("/")
        self.agent_endpoints: Dict[str, str] = agent_endpoints or {}
        self.client = httpx.AsyncClient(timeout=timeout)

        logger.info(
            "Initialized EnhancedAgentClient",
            agent_service_url=self.agent_service_url,
            timeout=timeout,
            structured_context_enabled=STRUCTURED_CONTEXT_ENABLED,
            remote_agents=(
                list(self.agent_endpoints.keys()) if self.agent_endpoints else []
            ),
        )

    async def invoke_agent(
        self,
        agent_name: str,
        session_id: str,
        user_id: str,
        message: str,
        transfer_context: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        previous_agent: Optional[str] = None,
        delegation_user_spiffe_id: Optional[str] = None,
        current_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Invoke an agent via HTTP with structured context support.

        Args:
            agent_name: Name of agent to invoke (routing-agent, laptop-refresh, etc.)
            session_id: Request manager session ID
            user_id: User identifier (email or ID)
            message: User message to process
            transfer_context: Optional context from previous agent
            conversation_history: Optional conversation history for context extraction
            previous_agent: Optional name of the agent that handled previous turn
            delegation_user_spiffe_id: Optional SPIFFE ID for delegation headers
            current_token: Optional token from previous hop (for delegation chain)

        Returns:
            Dictionary with agent response:
            {
                "content": str,
                "agent_id": str,
                "session_id": str,
                "routing_decision": Optional[str],
                "metadata": Optional[Dict]
            }

        Raises:
            httpx.HTTPError: If HTTP request fails

        Example:
            >>> client = EnhancedAgentClient()
            >>> response = await client.invoke_agent(
            ...     agent_name="laptop-refresh-agent",
            ...     session_id="session-123",
            ...     user_id="user@example.com",
            ...     message="What models are available?",
            ...     conversation_history=[
            ...         {"role": "user", "content": "I need a laptop"},
            ...         {"role": "assistant", "content": "What region?"},
            ...         {"role": "user", "content": "EMEA"}
            ...     ],
            ...     previous_agent="routing-agent"
            ... )
        """
        # Use per-agent endpoint if registered, otherwise construct from base URL
        url = self.agent_endpoints.get(
            agent_name,
            f"{self.agent_service_url}/api/v1/agents/{agent_name}/invoke",
        )

        # Build enhanced transfer context
        enhanced_context = transfer_context or {}

        # Add conversation history if provided and feature enabled
        if STRUCTURED_CONTEXT_ENABLED and conversation_history:
            enhanced_context["conversation_history"] = conversation_history
            enhanced_context["previous_agent"] = previous_agent
            enhanced_context["current_agent"] = agent_name
            enhanced_context["enable_context_extraction"] = True

            # Log context optimization info
            is_agent_switch = previous_agent and (previous_agent != agent_name)
            logger.debug(
                "Enhanced context with conversation history",
                agent_name=agent_name,
                history_length=len(conversation_history),
                previous_agent=previous_agent,
                is_agent_switch=is_agent_switch,
                extraction_mode="rewrite" if is_agent_switch else "metadata",
            )

        payload = {
            "session_id": session_id,
            "user_id": user_id,
            "message": message,
            "transfer_context": enhanced_context,
        }

        # Token exchange: get agent-scoped token via RFC 8693
        exchanged_token = None
        token_exchanged = False
        token_id = None

        try:
            # Determine subject token: use current_token (from previous hop) if provided,
            # otherwise get the original user token
            raw_token = current_token if current_token else CredentialService.get_token()

            # Strip "Bearer " prefix if present (CredentialService stores full auth header)
            if raw_token and raw_token.startswith("Bearer "):
                subject_token = raw_token[7:]  # Remove "Bearer " prefix
            else:
                subject_token = raw_token

            if subject_token:
                # Initialize token exchange client
                token_exchange_client = TokenExchangeClient()

                # Use agent SPIFFE ID as the target audience
                target_agent = make_spiffe_id("agent", agent_name)

                # If we have a current_token, extract its act claim for delegation chain
                if current_token:
                    # Extract act claim from subject_token (current_token with Bearer prefix stripped)
                    existing_act_claim = None
                    try:
                        unverified_payload = jwt.decode(
                            subject_token,
                            options={"verify_signature": False},
                        )
                        existing_act_claim = unverified_payload.get("act")
                        logger.debug(
                            "Extracted act claim from subject_token",
                            agent_name=agent_name,
                            has_act_claim=existing_act_claim is not None,
                            session_id=session_id,
                        )
                    except Exception as decode_err:
                        logger.warning(
                            "Failed to decode subject_token for act extraction",
                            agent_name=agent_name,
                            session_id=session_id,
                            error=str(decode_err),
                        )

                    # Exchange with delegation (builds nested act claim)
                    result = await token_exchange_client.exchange_with_delegation(
                        subject_token=subject_token,
                        target_agent=target_agent,
                        actor_service="request-manager",
                        existing_act_claim=existing_act_claim,
                    )
                    exchanged_token = result["access_token"]
                    token_exchanged = True

                    # Extract token ID for audit (first 20 + last 8 chars)
                    if len(exchanged_token) >= 28:
                        token_id = f"{exchanged_token[:20]}...{exchanged_token[-8:]}"
                    else:
                        token_id = exchanged_token[:28]

                    logger.debug(
                        "Token exchange with delegation successful",
                        agent_name=agent_name,
                        target_agent=target_agent,
                        session_id=session_id,
                        delegation_chain=result.get("delegation_chain", []),
                        token_id=token_id,
                    )
                else:
                    # First hop: exchange user token for agent-scoped token
                    result = await token_exchange_client.exchange_for_agent(
                        subject_token=subject_token,
                        target_agent=target_agent,
                        actor_service="request-manager"
                    )
                    exchanged_token = result["access_token"]
                    token_exchanged = True

                    # Extract token ID for audit (first 20 + last 8 chars)
                    if len(exchanged_token) >= 28:
                        token_id = f"{exchanged_token[:20]}...{exchanged_token[-8:]}"
                    else:
                        token_id = exchanged_token[:28]

                    logger.debug(
                        "Token exchange successful",
                        agent_name=agent_name,
                        target_agent=target_agent,
                        session_id=session_id,
                        token_id=token_id,
                    )
        except TokenExchangeError as e:
            logger.error(
                "Token exchange failed - NO FALLBACK",
                agent_name=agent_name,
                session_id=session_id,
                error=str(e),
                error_type=type(e).__name__,
                status_code=e.status_code,
                detail=e.detail,
            )
            # NO FALLBACK: Raise the exception to fail the request
            raise RuntimeError(
                f"Token exchange failed for agent '{agent_name}': {e.detail or str(e)}. "
                "Token exchange is REQUIRED - no fallback allowed."
            ) from e
        except Exception as e:
            logger.error(
                "Unexpected error during token exchange - NO FALLBACK",
                agent_name=agent_name,
                session_id=session_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            # NO FALLBACK: Raise the exception to fail the request
            raise RuntimeError(
                f"Unexpected error during token exchange for agent '{agent_name}': {str(e)}. "
                "Token exchange is REQUIRED - no fallback allowed."
            ) from e

        # Build headers: SPIFFE identity for service-to-service auth,
        # delegation headers to carry user authority, and JWT for token propagation.
        headers = outbound_identity_headers(
            "request-manager",
            delegation_user=delegation_user_spiffe_id,
            delegation_agent=(
                make_spiffe_id("agent", agent_name)
                if delegation_user_spiffe_id
                else None
            ),
        )

        # Use exchanged token - NO FALLBACK
        if exchanged_token:
            headers["Authorization"] = f"Bearer {exchanged_token}"
        else:
            # This should never happen as token exchange errors now raise exceptions
            raise RuntimeError(
                f"No exchanged token available for agent '{agent_name}' - this indicates a bug in token exchange logic"
            )

        logger.info(
            "Invoking agent",
            agent_name=agent_name,
            session_id=session_id,
            url=url,
            message_length=len(message),
            has_auth=bool(exchanged_token or headers.get("Authorization")),
            token_exchanged=token_exchanged,
            target_agent=make_spiffe_id("agent", agent_name) if token_exchanged else None,
            has_conversation_history=bool(conversation_history),
            structured_context_enabled=STRUCTURED_CONTEXT_ENABLED,
            token_id=token_id,
            is_delegated_token=bool(current_token),
        )

        try:
            response = await self.client.post(url, json=payload, headers=headers)
            response.raise_for_status()

            data = response.json()

            logger.info(
                "Agent invocation successful",
                agent_name=agent_name,
                session_id=session_id,
                has_routing=bool(data.get("routing_decision")),
                response_length=len(data.get("content", "")),
                context_was_extracted=data.get("metadata", {}).get(
                    "context_extracted", False
                ),
            )

            return data

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                detail = e.response.json().get("detail", "") if e.response.headers.get("content-type", "").startswith("application/json") else str(e)
                logger.warning(
                    "Agent invocation denied (403)",
                    agent_name=agent_name,
                    session_id=session_id,
                    detail=detail,
                )
                return {
                    "content": (
                        f"Access denied. Your account does not have the required department "
                        f"permissions to use the **{agent_name.replace('-', ' ').title()}** agent. "
                        f"Please contact your administrator to request access."
                    ),
                    "agent_id": "routing-agent",
                    "session_id": session_id,
                    "routing_decision": None,
                    "metadata": {
                        "handling_agent": "routing-agent",
                        "routing_reason": "Access denied by authorization policy",
                        "blocked_agent": agent_name,
                        "authorization_detail": detail,
                    },
                }
            logger.error(
                "Agent invocation failed",
                agent_name=agent_name,
                session_id=session_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise
        except httpx.HTTPError as e:
            logger.error(
                "Agent invocation failed",
                agent_name=agent_name,
                session_id=session_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()

    async def __aenter__(self):
        """Async context manager enter."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
