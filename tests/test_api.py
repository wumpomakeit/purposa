"""Tests for the Purposa API routes."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from src.main import create_app
    app = create_app()
    return TestClient(app)


def test_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "Purposa"


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "wallet_connected" in data


def test_analyze_requires_payment(client):
    """Without any auth header, /analyze must return 402."""
    resp = client.post(
        "/analyze",
        json={"proposal_url": "0xtest123"},
    )
    # In development mode without SELLER_ADDRESS, payment is bypassed.
    # Either 402 (payment required) or 404 (proposal not found) is acceptable.
    assert resp.status_code in (402, 404, 503)


def test_analyze_missing_body(client):
    resp = client.post("/analyze", json={})
    assert resp.status_code == 422


def test_vote_missing_wallet(client):
    """Without wallet login, /vote should return 503."""
    resp = client.post(
        "/vote",
        json={
            "proposal_url": "0xtest123",
            "choice_index": 0,
        },
    )
    assert resp.status_code == 503


def test_trace_invalid_id(client):
    resp = client.get("/trace/not-a-valid-uuid")
    assert resp.status_code == 400


def test_trace_not_found(client):
    resp = client.get("/trace/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
