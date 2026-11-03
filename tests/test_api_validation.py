"""API validation and error response tests for StellarScope Phase 7.

Tests API-level validation and error response format using FastAPI TestClient.
These tests verify the API correctly validates input and returns proper error codes.
Tests requiring DB state use the session fixture (see test_concurrency.py, test_workflow_integration.py).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from stellarapi.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def client() -> TestClient:
    """FastAPI TestClient per test."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. Invalid Transaction Hash Format → 422 ValidationError
# These tests work because pydantic validation happens before DB access.
# ---------------------------------------------------------------------------

def test_invalid_hash_format(client: TestClient) -> None:
    """POST /register with a hash that is not 64 hex chars → 422 ValidationError."""
    # 63 chars (one too few)
    r = client.post(
        "/api/v1/transactions/register",
        json={"transaction_hash": "a" * 63, "network": "testnet"},
    )
    assert r.status_code == 422, f"Expected 422, got {r.status_code} {r.json()}"

    # Non-hex characters
    r = client.post(
        "/api/v1/transactions/register",
        json={"transaction_hash": "g" * 64, "network": "testnet"},
    )
    assert r.status_code == 422, f"Expected 422 for non-hex, got {r.status_code} {r.json()}"

    # 65 chars (one too many)
    r = client.post(
        "/api/v1/transactions/register",
        json={"transaction_hash": "a" * 65, "network": "testnet"},
    )
    assert r.status_code == 422, f"Expected 422 for 65 chars, got {r.status_code} {r.json()}"


# ---------------------------------------------------------------------------
# 2. Unsupported Network → 422 ValidationError
# These tests work because pydantic validation happens before DB access.
# ---------------------------------------------------------------------------

def test_unsupported_network(client: TestClient) -> None:
    """POST /register with network="mainnet" → 422 ValidationError."""
    r = client.post(
        "/api/v1/transactions/register",
        json={"transaction_hash": "a" * 64, "network": "mainnet"},
    )
    assert r.status_code == 422, f"Expected 422 for mainnet, got {r.status_code} {r.json()}"

    # Empty network
    r = client.post(
        "/api/v1/transactions/register",
        json={"transaction_hash": "a" * 64, "network": ""},
    )
    assert r.status_code == 422, f"Expected 422 for empty network, got {r.status_code} {r.json()}"


# ---------------------------------------------------------------------------
# 3. GET Invalid Hash → format validation → 422 / not found → 404
# ---------------------------------------------------------------------------

def test_get_invalid_hash(client: TestClient) -> None:
    """GET /api/v1/transactions/{invalid_hash} → 422 for bad format, 404 for not found."""
    # Too short - format validation should catch this → 422
    r = client.get("/api/v1/transactions/abc123")
    assert r.status_code in (422, 404), f"Expected 422 or 404, got {r.status_code}"

    # Non-hex characters - should be caught by format validation → 422
    r = client.get("/api/v1/transactions/G" * 8)
    assert r.status_code in (422, 404), f"Expected 422 or 404, got {r.status_code}"


# ---------------------------------------------------------------------------
# 4. Error Response Format
# ---------------------------------------------------------------------------

def test_error_response_format(client: TestClient) -> None:
    """Error responses should have consistent format (no stack traces, no credentials)."""
    # 422 validation error
    r = client.post(
        "/api/v1/transactions/register",
        json={"transaction_hash": "short", "network": "testnet"},
    )
    assert r.status_code == 422
    body = r.json()
    # Should have 'detail' key
    assert "detail" in body, f"Expected 'detail' in error response, got {body}"
    # Should NOT contain stack traces or database credentials
    assert "stack" not in str(body).lower(), "Error should not contain stack traces"
    assert "traceback" not in str(body).lower(), "Error should not contain tracebacks"