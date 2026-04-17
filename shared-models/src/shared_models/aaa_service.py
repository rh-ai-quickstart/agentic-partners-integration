"""
AAA (Authentication, Authorization, Audit) Service.

Authorization is now handled by OPA (Open Policy Agent) with Rego policies.
This module retains user management and department-based access helpers.
"""

from typing import Any, Dict, List, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User, UserRole
from .opa_client import get_user_departments_from_opa

logger = structlog.get_logger()


class AAAService:
    """Service for user management and department-based access control."""

    @staticmethod
    async def get_user_by_email(
        db: AsyncSession,
        email: str
    ) -> Optional[User]:
        """Get user by email address."""
        try:
            stmt = select(User).where(User.primary_email == email)
            result = await db.execute(stmt)
            return result.scalar_one_or_none()

        except Exception as e:
            logger.error(
                "Failed to get user by email",
                email=email,
                error=str(e)
            )
            return None

    @staticmethod
    async def get_or_create_user(
        db: AsyncSession,
        email: str,
        role: UserRole = UserRole.USER,
        organization: Optional[str] = None,
        department: Optional[str] = None,
        departments: Optional[List[str]] = None
    ) -> Optional[User]:
        """Get existing user or create new one.

        Args:
            db: Database session
            email: User email address
            role: User role (default: USER)
            organization: User organization
            department: User department (legacy single field)
            departments: List of department tags for OPA authorization
        """
        try:
            user = await AAAService.get_user_by_email(db, email)

            if user:
                logger.debug("Found existing user", email=email, role=user.role)
                return user

            user = User(
                primary_email=email,
                role=role.value if isinstance(role, UserRole) else role,
                privileges={},
                departments=departments or [],
                status="active",
                organization=organization,
                department=department
            )

            db.add(user)
            await db.commit()
            await db.refresh(user)

            logger.info(
                "Created new user",
                email=email,
                role=role,
                departments=departments
            )

            return user

        except Exception as e:
            logger.error(
                "Failed to get or create user",
                email=email,
                error=str(e)
            )
            await db.rollback()
            return None

    @staticmethod
    async def get_user_departments(
        db: AsyncSession,
        user_email: str
    ) -> List[str]:
        """Get user's departments for OPA authorization.

        First checks the database user record. If empty, falls back to
        OPA's static fallback map (useful for local/mock development).
        """
        try:
            user = await AAAService.get_user_by_email(db, user_email)

            if user and user.departments:
                return user.departments

            # Fall back to OPA static map
            return await get_user_departments_from_opa(user_email)

        except Exception as e:
            logger.error(
                "Failed to get user departments",
                user_email=user_email,
                error=str(e)
            )
            return []

    @staticmethod
    async def update_user_permissions(
        db: AsyncSession,
        user_email: str,
        role: Optional[UserRole] = None,
        departments: Optional[List[str]] = None,
        privileges: Optional[Dict[str, Any]] = None,
        status: Optional[str] = None
    ) -> bool:
        """Update user permissions and department access.

        Args:
            db: Database session
            user_email: User email address
            role: New role (optional)
            departments: New departments list (optional)
            privileges: New privileges dict (optional)
            status: New status (optional)
        """
        try:
            user = await AAAService.get_user_by_email(db, user_email)

            if not user:
                logger.error("Cannot update permissions for non-existent user", email=user_email)
                return False

            if role is not None:
                user.role = role
            if departments is not None:
                user.departments = departments
            if privileges is not None:
                user.privileges = privileges
            if status is not None:
                user.status = status

            await db.commit()

            logger.info(
                "Updated user permissions",
                email=user_email,
                role=user.role,
                departments=user.departments,
                status=user.status
            )

            return True

        except Exception as e:
            logger.error(
                "Failed to update user permissions",
                email=user_email,
                error=str(e)
            )
            await db.rollback()
            return False
