"""
Unit tests for SSE endpoint and Sprint 5 API additions.

Tests cover:
- /latency endpoint structure
- /streams endpoint structure
- /stream SSE endpoint connectivity
"""

import pytest
from fastapi.testclient import TestClient

from api import app


@pytest.fixture
def client():
    return TestClient(app)


class TestLatencyEndpoint:
    def test_latency_returns_200(self, client):
        response = client.get("/latency")
        assert response.status_code == 200

    def test_latency_structure(self, client):
        data = client.get("/latency").json()
        assert "timestamp" in data
        assert "total_trades_measured" in data
        assert "percentiles" in data
        assert "target_ms" in data
        assert data["target_ms"] == 500
        assert "recent" in data

    def test_latency_no_secrets(self, client):
        body = client.get("/latency").text.lower()
        assert "api_key" not in body
        assert "private_key" not in body
        assert "token" not in body


class TestStreamsEndpoint:
    def test_streams_returns_200(self, client):
        response = client.get("/streams")
        assert response.status_code == 200

    def test_streams_structure(self, client):
        data = client.get("/streams").json()
        assert "timestamp" in data
        assert "binance" in data
        assert "polymarket" in data
        assert "kalshi" in data
        assert "subscribers" in data

    def test_streams_shows_feed_status(self, client):
        data = client.get("/streams").json()
        assert "connected" in data["binance"]
        assert "message_count" in data["binance"]

    def test_streams_no_secrets(self, client):
        body = client.get("/streams").text.lower()
        assert "api_key" not in body
        assert "private_key" not in body


class TestSSEEndpoint:
    def test_stream_endpoint_registered(self, client):
        """Verify /stream endpoint is registered in the router."""
        routes = [r.path for r in app.routes]
        assert "/stream" in routes
