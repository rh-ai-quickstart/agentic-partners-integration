"""Tests for request-manager application."""

from fastapi.testclient import TestClient

from request_manager.main import app


def test_app_import() -> None:
    """Smoke test: app imports successfully."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "healthy"
    assert data.get("service") == "request-manager"
