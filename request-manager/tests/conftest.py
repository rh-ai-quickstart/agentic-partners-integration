"""Shared fixtures for request-manager tests."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db_session():
    """Provide an AsyncMock of sqlalchemy.ext.asyncio.AsyncSession."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.close = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Model fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_user():
    """Provide a mock User model instance with test data."""
    user = MagicMock()
    user.user_id = str(uuid.uuid4())
    user.primary_email = "testuser@example.com"
    user.spiffe_id = "spiffe://example.com/user/testuser"
    user.is_active = True
    user.role = MagicMock()
    user.role.value = "user"
    user.status = "active"
    user.organization = "TestOrg"
    user.department = "engineering"
    user.departments = ["engineering"]
    user.privileges = {"can_view": True}
    user.last_login = datetime.now(timezone.utc)
    return user


@pytest.fixture
def mock_request_session():
    """Provide a mock RequestSession model instance."""
    session = MagicMock()
    session.id = 1
    session.session_id = str(uuid.uuid4())
    session.user_id = str(uuid.uuid4())
    session.integration_type = "WEB"
    session.status = "ACTIVE"
    session.channel_id = None
    session.thread_id = None
    session.external_session_id = None
    session.current_agent_id = "routing-agent"
    session.conversation_thread_id = None
    session.integration_metadata = {}
    session.user_context = {}
    session.conversation_context = {}
    session.total_requests = 0
    session.last_request_id = None
    session.last_request_at = datetime.now(timezone.utc)
    session.expires_at = None
    session.created_at = datetime.now(timezone.utc)
    session.updated_at = datetime.now(timezone.utc)
    return session


@pytest.fixture
def mock_request_log():
    """Provide a mock RequestLog model instance."""
    log = MagicMock()
    log.id = 1
    log.request_id = str(uuid.uuid4())
    log.session_id = str(uuid.uuid4())
    log.request_type = "message"
    log.request_content = "I need help with my laptop"
    log.normalized_request = {
        "user_id": "testuser@example.com",
        "integration_type": "WEB",
        "content": "I need help with my laptop",
        "request_type": "message",
        "integration_context": {},
    }
    log.agent_id = "routing-agent"
    log.processing_time_ms = 150
    log.response_content = "Sure, I can help with that."
    log.response_metadata = {}
    log.completed_at = datetime.now(timezone.utc)
    log.created_at = datetime.now(timezone.utc)
    log.pod_name = "request-manager-abc123"
    return log


# ---------------------------------------------------------------------------
# Environment variable fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def env_keycloak(monkeypatch):
    """Set Keycloak-related environment variables for tests."""
    monkeypatch.setenv("KEYCLOAK_URL", "http://test-keycloak:8080")
    monkeypatch.setenv("KEYCLOAK_REALM", "test-realm")
    monkeypatch.setenv("KEYCLOAK_CLIENT_ID", "test-client")


@pytest.fixture
def env_agent_service(monkeypatch):
    """Set agent service related environment variables."""
    monkeypatch.setenv("AGENT_SERVICE_URL", "http://test-agent-service:8080")
    monkeypatch.setenv("AGENT_TIMEOUT", "30")


@pytest.fixture
def env_session_config(monkeypatch):
    """Set session configuration environment variables."""
    monkeypatch.setenv("SESSION_TIMEOUT_HOURS", "24")
    monkeypatch.setenv("SESSION_PER_INTEGRATION_TYPE", "false")
    monkeypatch.setenv("SESSION_CLEANUP_INTERVAL_HOURS", "1")
    monkeypatch.setenv("INACTIVE_SESSION_RETENTION_DAYS", "7")
