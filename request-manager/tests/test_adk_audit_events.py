"""Tests for request_manager.adk_endpoints — audit-events and agents endpoints."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
class TestAdkAuditEvents:
    """Tests for the /adk/audit-events endpoint."""

    def _make_audit_event(self, **kwargs):
        """Create a mock AuditEvent row."""
        defaults = {
            "event_id": "evt-1",
            "event_type": "auth.login.success",
            "actor": "carlos@example.com",
            "action": "login",
            "resource": "/api/v1/chat",
            "outcome": "success",
            "reason": "",
            "metadata_": {},
            "source_ip": "10.0.0.1",
            "service": "request-manager",
            "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        }
        defaults.update(kwargs)
        event = MagicMock()
        for k, v in defaults.items():
            setattr(event, k, v)
        return event

    @patch("request_manager.adk_endpoints.AAAService")
    @patch("request_manager.adk_endpoints.decode_token")
    async def test_audit_events_regular_user(self, mock_decode, mock_aaa):
        """Regular user sees their own events."""
        from request_manager.adk_endpoints import adk_audit_events

        mock_decode.return_value = {"email": "carlos@example.com"}

        mock_user = MagicMock()
        mock_user.role = MagicMock(value="user")
        mock_aaa.get_user_by_email = AsyncMock(return_value=mock_user)

        mock_event = self._make_audit_event()

        db = AsyncMock()
        # First execute: count query (.scalar())
        count_result = MagicMock()
        count_result.scalar.return_value = 1
        # Second execute: rows query (.scalars().all())
        rows_result = MagicMock()
        rows_result.scalars.return_value.all.return_value = [mock_event]

        db.execute = AsyncMock(side_effect=[count_result, rows_result])

        http_request = MagicMock()
        http_request.headers = {"Authorization": "Bearer valid-token"}

        result = await adk_audit_events(http_request, limit=50, db=db)

        assert result.total == 1
        assert result.user_email == "carlos@example.com"
        assert result.user_role == "user"
        assert result.entries[0].event_id == "evt-1"

    @patch("request_manager.adk_endpoints.AAAService")
    @patch("request_manager.adk_endpoints.decode_token")
    async def test_audit_events_admin_sees_all(self, mock_decode, mock_aaa):
        """Admin user sees all events."""
        from request_manager.adk_endpoints import adk_audit_events

        mock_decode.return_value = {"email": "admin@example.com"}

        mock_user = MagicMock()
        mock_user.role = MagicMock(value="admin")
        mock_aaa.get_user_by_email = AsyncMock(return_value=mock_user)

        mock_event = self._make_audit_event(actor="other@example.com")

        db = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 1
        rows_result = MagicMock()
        rows_result.scalars.return_value.all.return_value = [mock_event]

        db.execute = AsyncMock(side_effect=[count_result, rows_result])

        http_request = MagicMock()
        http_request.headers = {"Authorization": "Bearer admin-token"}

        result = await adk_audit_events(http_request, limit=50, db=db)

        assert result.user_role == "admin"
        assert result.total == 1

    @patch("request_manager.adk_endpoints.decode_token")
    async def test_audit_events_requires_auth(self, mock_decode):
        """Missing auth header raises 401."""
        from request_manager.adk_endpoints import adk_audit_events

        http_request = MagicMock()
        http_request.headers = {}

        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await adk_audit_events(http_request, limit=50, db=db)
        assert exc_info.value.status_code == 401

    @patch("request_manager.adk_endpoints.AAAService")
    @patch("request_manager.adk_endpoints.decode_token")
    async def test_audit_events_user_not_found(self, mock_decode, mock_aaa):
        """When user not found, raise 404."""
        from request_manager.adk_endpoints import adk_audit_events

        mock_decode.return_value = {"email": "ghost@example.com"}
        mock_aaa.get_user_by_email = AsyncMock(return_value=None)

        http_request = MagicMock()
        http_request.headers = {"Authorization": "Bearer valid"}

        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await adk_audit_events(http_request, limit=50, db=db)
        assert exc_info.value.status_code == 404

    @patch("request_manager.adk_endpoints.AAAService")
    @patch("request_manager.adk_endpoints.decode_token")
    async def test_audit_events_with_filters(self, mock_decode, mock_aaa):
        """Filtering by event_type and outcome calls execute with filters."""
        from request_manager.adk_endpoints import adk_audit_events

        mock_decode.return_value = {"email": "user@example.com"}

        mock_user = MagicMock()
        mock_user.role = MagicMock(value="user")
        mock_aaa.get_user_by_email = AsyncMock(return_value=mock_user)

        mock_event = self._make_audit_event(
            event_type="auth.denied", outcome="failure"
        )

        db = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 1
        rows_result = MagicMock()
        rows_result.scalars.return_value.all.return_value = [mock_event]

        db.execute = AsyncMock(side_effect=[count_result, rows_result])

        http_request = MagicMock()
        http_request.headers = {"Authorization": "Bearer valid"}

        result = await adk_audit_events(
            http_request, limit=10, event_type="auth.denied", outcome="failure", db=db
        )

        assert result.total == 1
        assert result.entries[0].event_type == "auth.denied"

    @patch("request_manager.adk_endpoints.AAAService")
    @patch("request_manager.adk_endpoints.decode_token")
    async def test_audit_events_db_error_returns_500(self, mock_decode, mock_aaa):
        """Database error raises 500."""
        from request_manager.adk_endpoints import adk_audit_events

        mock_decode.return_value = {"email": "user@example.com"}

        mock_user = MagicMock()
        mock_user.role = MagicMock(value="user")
        mock_aaa.get_user_by_email = AsyncMock(return_value=mock_user)

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=RuntimeError("DB connection lost"))

        http_request = MagicMock()
        http_request.headers = {"Authorization": "Bearer valid"}

        with pytest.raises(HTTPException) as exc_info:
            await adk_audit_events(http_request, limit=50, db=db)
        assert exc_info.value.status_code == 500


@pytest.mark.asyncio
class TestAdkAgents:
    """Tests for the /adk/agents endpoint."""

    @patch("request_manager.adk_endpoints.httpx.AsyncClient")
    async def test_agents_returns_registry(self, mock_httpx):
        """Successfully proxies agent registry from agent-service."""
        from request_manager.adk_endpoints import adk_agents

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "agents": [
                {"name": "software-support", "departments": ["software"]},
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_httpx.return_value = mock_client

        result = await adk_agents()
        assert "agents" in result

    @patch("request_manager.adk_endpoints.httpx.AsyncClient")
    async def test_agents_registry_unavailable(self, mock_httpx):
        """When agent-service is unreachable, raise 502."""
        import httpx as real_httpx

        from request_manager.adk_endpoints import adk_agents

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=real_httpx.ConnectError("Connection refused")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_httpx.return_value = mock_client

        with pytest.raises(HTTPException) as exc_info:
            await adk_agents()
        assert exc_info.value.status_code == 502
