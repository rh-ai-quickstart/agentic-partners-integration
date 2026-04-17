"""
SOC 2 Audit Event Service (CC7.1, CC7.2).

Persists security-relevant events to the audit_events table.
Each event is written in its own database session so audit records
are never lost due to business-transaction rollbacks.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger()


class AuditService:
    """Append-only audit event writer.

    All methods are fire-and-forget: a failed audit write logs an error
    but never crashes the calling request.
    """

    @staticmethod
    async def emit(
        *,
        event_type: str,
        actor: str,
        action: str,
        resource: str = "",
        outcome: str = "success",
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        source_ip: str = "",
        service: str = "",
    ) -> None:
        """Write an audit event to the database.

        Args:
            event_type: Dotted event type (e.g. "auth.login.success")
            actor: Who performed the action (email or SPIFFE ID)
            action: What was done (e.g. "login", "invoke_agent")
            resource: What was accessed (e.g. agent name, endpoint)
            outcome: "success" or "failure"
            reason: Why it was allowed/denied
            metadata: Extra context (departments, effective_departments, etc.)
            source_ip: Client IP address
            service: Which service emitted the event
        """
        try:
            from .database import get_database_manager
            from .models import AuditEvent

            db_manager = get_database_manager()
            async with db_manager.get_session() as db:
                event = AuditEvent(
                    event_id=str(uuid.uuid4()),
                    event_type=event_type,
                    actor=actor,
                    action=action,
                    resource=resource,
                    outcome=outcome,
                    reason=reason[:1000] if reason else "",
                    metadata_=metadata or {},
                    source_ip=source_ip[:45] if source_ip else "",
                    service=service,
                    created_at=datetime.now(timezone.utc),
                )
                db.add(event)
                await db.commit()
        except Exception as e:
            # Never crash the request — log and move on.
            logger.error(
                "Failed to write audit event",
                event_type=event_type,
                actor=actor,
                action=action,
                error=str(e),
            )
