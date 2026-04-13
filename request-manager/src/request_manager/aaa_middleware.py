"""
AAA Middleware for Request Manager.

Builds user context with department-based access control for OPA authorization.
"""

from typing import Any, Dict

import structlog
from shared_models.aaa_service import AAAService
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


class AAAMiddleware:
    """Middleware for building user authorization context."""

    @staticmethod
    async def get_user_context(
        db: AsyncSession,
        user_email: str
    ) -> Dict[str, Any]:
        """
        Get user context with departments for OPA-based authorization.

        Args:
            db: Database session
            user_email: User email

        Returns:
            User context dictionary with departments
        """
        try:
            user = await AAAService.get_user_by_email(db, user_email)

            if not user:
                return {
                    "email": user_email,
                    "role": "user",
                    "status": "unknown",
                    "departments": []
                }

            # Get departments from DB or OPA fallback
            departments = await AAAService.get_user_departments(db, user_email)

            return {
                "user_id": str(user.user_id),
                "email": user.primary_email,
                "role": user.role.value if user.role else "user",
                "status": user.status,
                "organization": user.organization,
                "department": user.department,
                "departments": departments,
                "spiffe_id": user.spiffe_id,
                "privileges": user.privileges or {}
            }

        except Exception as e:
            logger.error(
                "Failed to get user context",
                user=user_email,
                error=str(e)
            )
            return {
                "email": user_email,
                "role": "user",
                "status": "error",
                "departments": []
            }
