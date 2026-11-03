"""Basic health endpoint test.

Verifies that ``GET /health`` returns a 200 response with the expected
structure.  No database or Stellar RPC dependencies.
"""
import pytest

from stellarapi.main import app


@pytest.mark.asyncio
async def test_health_endpoint() -> None:
    """Health check returns 200 and a structured JSON body."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "stellarscope"
    assert body["version"] == "0.1.0"
    assert "environment" in body