"""
Integration tests for the FastAPI API endpoints.

Uses httpx TestClient (no actual HTTP server needed).
"""

import pytest
from fastapi.testclient import TestClient


class TestHealthEndpoint:
    def test_health_returns_ok(self):
        from api import app
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == "2.0.0"
        assert "timestamp" in data
        assert "dry_run" in data

    def test_health_has_dry_run(self):
        from api import app
        client = TestClient(app)
        data = client.get("/health").json()
        assert isinstance(data["dry_run"], bool)


class TestConfigEndpoint:
    def test_config_returns_settings(self):
        from api import app
        client = TestClient(app)
        response = client.get("/config")
        assert response.status_code == 200
        data = response.json()
        assert "dry_run" in data
        assert "max_single_trade_usd" in data
        assert "min_net_margin" in data
        assert "kalshi_fee_per_contract" in data
        assert "slippage_buffer" in data

    def test_config_no_secrets(self):
        """Config endpoint should NOT expose API keys."""
        from api import app
        client = TestClient(app)
        data = client.get("/config").json()
        assert "KALSHI_API_KEY" not in str(data)
        assert "POLYMARKET_PRIVATE_KEY" not in str(data)
        assert "KALSHI_PRIVATE_KEY_PATH" not in str(data)


class TestArbitrageEndpoint:
    def test_arbitrage_returns_structure(self):
        """Ensure the response has the expected shape even if APIs fail."""
        from api import app
        client = TestClient(app)
        response = client.get("/arbitrage")
        assert response.status_code == 200
        data = response.json()
        assert "timestamp" in data
        assert "checks" in data
        assert "opportunities" in data
        assert "errors" in data
        # Note: polymarket/kalshi may be None if APIs are unreachable
        assert isinstance(data["checks"], list)
        assert isinstance(data["errors"], list)
