"""Tests for request-manager application.

Note: JWT/password auth endpoints have been replaced by SPIFFE identity +
OPA authorization. See policies/ for Rego rules and shared_models.identity
for the SPIFFE identity module.
"""

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
