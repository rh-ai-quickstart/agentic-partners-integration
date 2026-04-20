"""Pydantic schemas for the Kubernetes partner agent."""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class AgentInvokeRequest(BaseModel):
    """Schema for invoking the agent via HTTP."""

    session_id: str = Field(..., description="Request manager session ID")
    user_id: str = Field(..., description="User identifier (email or ID)")
    message: str = Field(..., min_length=1, description="User message to process")
    transfer_context: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional context transferred from previous agent",
    )


class AgentInvokeResponse(BaseModel):
    """Schema for agent invocation response."""

    content: str = Field(..., description="Agent response text")
    agent_id: str = Field(..., description="Agent that generated this response")
    session_id: str = Field(..., description="Request manager session ID")
    routing_decision: Optional[str] = Field(
        default=None,
        description="Specialist agent to route to (if routing agent)",
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Additional metadata about the response",
    )
