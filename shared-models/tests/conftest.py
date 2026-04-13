"""Shared test fixtures for shared-models tests."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from shared_models.models import User, UserRole


@pytest.fixture
def mock_db_session():
    """Create a mock async database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.scalar = AsyncMock()
    session.scalars = AsyncMock()
    session.refresh = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def mock_user():
    """Create a mock User model instance with test data."""
    user = MagicMock(spec=User)
    user.user_id = str(uuid.uuid4())
    user.primary_email = "test@example.com"
    user.role = UserRole.USER
    user.departments = ["software"]
    user.status = "active"
    user.is_active = True
    user.organization = "TestOrg"
    user.department = "Engineering"
    user.privileges = {}
    user.spiffe_id = None
    user.last_login = None
    return user
