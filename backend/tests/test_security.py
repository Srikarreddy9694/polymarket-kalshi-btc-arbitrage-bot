"""
Security integration tests.

Tests that verify NO secrets are leaked through any API endpoint or
status reporting interface. This is the final security gate.

CRITICAL: These tests must ALWAYS pass before any deployment.
"""

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from config.settings import Settings


# ── Secret fields that must NEVER appear in API responses ──
SECRET_VALUES = [
    "test-kalshi-api-key",
    "test-private-key-path",
    "0xtest_polymarket_key",
    "super-secret-kill-token",
]

SECRET_FIELD_NAMES = [
    "KALSHI_API_KEY",
    "KALSHI_PRIVATE_KEY_PATH",
    "POLYMARKET_PRIVATE_KEY",
    "KILL_SWITCH_TOKEN",
]


@pytest.fixture
def app_with_secrets(tmp_path):
    """
    Create the FastAPI app with known secret values,
    then verify none of them leak through any endpoint.
    """
    from api import app, settings as api_settings

    # Directly set secrets on the module-level settings object
    # (env patching doesn't work because settings are cached at import time)
    original_kalshi_key = api_settings.KALSHI_API_KEY
    original_kalshi_path = api_settings.KALSHI_PRIVATE_KEY_PATH
    original_poly_key = api_settings.POLYMARKET_PRIVATE_KEY
    original_token = api_settings.KILL_SWITCH_TOKEN

    api_settings.KALSHI_API_KEY = "test-kalshi-api-key"
    api_settings.KALSHI_PRIVATE_KEY_PATH = "test-private-key-path"
    api_settings.POLYMARKET_PRIVATE_KEY = "0xtest_polymarket_key"
    api_settings.KILL_SWITCH_TOKEN = "super-secret-kill-token"

    client = TestClient(app)
    yield client

    # Restore originals
    api_settings.KALSHI_API_KEY = original_kalshi_key
    api_settings.KALSHI_PRIVATE_KEY_PATH = original_kalshi_path
    api_settings.POLYMARKET_PRIVATE_KEY = original_poly_key
    api_settings.KILL_SWITCH_TOKEN = original_token


class TestNoSecretsInHealthEndpoint:
    def test_health_no_secrets(self, app_with_secrets):
        response = app_with_secrets.get("/health")
        body = response.text.lower()
        for secret in SECRET_VALUES:
            assert secret not in body, f"Secret '{secret}' leaked in /health response"
        for field in SECRET_FIELD_NAMES:
            assert field.lower() not in body, f"Field '{field}' appeared in /health response"


class TestNoSecretsInConfigEndpoint:
    def test_config_no_secrets(self, app_with_secrets):
        response = app_with_secrets.get("/config")
        body = response.text.lower()
        for secret in SECRET_VALUES:
            assert secret not in body, f"Secret '{secret}' leaked in /config response"

    def test_config_no_secret_fields(self, app_with_secrets):
        response = app_with_secrets.get("/config")
        data = response.json()
        for field in SECRET_FIELD_NAMES:
            assert field not in data, f"Secret field '{field}' in /config response"
            assert field.lower() not in data, f"Secret field '{field.lower()}' in /config response"


class TestNoSecretsInStatusEndpoint:
    def test_status_no_secrets(self, app_with_secrets):
        response = app_with_secrets.get("/status")
        body = response.text.lower()
        for secret in SECRET_VALUES:
            assert secret not in body, f"Secret '{secret}' leaked in /status response"


class TestKillSwitchAuth:
    def test_no_auth_returns_401(self, app_with_secrets):
        response = app_with_secrets.post("/kill-switch")
        assert response.status_code == 401

    def test_wrong_token_returns_403(self, app_with_secrets):
        response = app_with_secrets.post(
            "/kill-switch",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert response.status_code == 403

    def test_valid_token_activates(self, app_with_secrets):
        response = app_with_secrets.post(
            "/kill-switch",
            headers={"Authorization": "Bearer super-secret-kill-token"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "activated"

    def test_deactivate_no_auth_returns_401(self, app_with_secrets):
        response = app_with_secrets.post("/kill-switch/deactivate")
        assert response.status_code == 401

    def test_deactivate_valid_token(self, app_with_secrets):
        # Activate first
        app_with_secrets.post(
            "/kill-switch",
            headers={"Authorization": "Bearer super-secret-kill-token"},
        )
        # Deactivate
        response = app_with_secrets.post(
            "/kill-switch/deactivate",
            headers={"Authorization": "Bearer super-secret-kill-token"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "deactivated"

    def test_error_message_doesnt_leak_token(self, app_with_secrets):
        """SECURITY: Error responses must not reveal what the correct token is."""
        response = app_with_secrets.post(
            "/kill-switch",
            headers={"Authorization": "Bearer wrong"},
        )
        body = response.text.lower()
        assert "super-secret-kill-token" not in body
        assert "correct" not in body
        assert "expected" not in body


class TestPositionsEndpoint:
    def test_positions_no_secrets(self, app_with_secrets):
        response = app_with_secrets.get("/positions")
        assert response.status_code == 200
        body = response.text.lower()
        for secret in SECRET_VALUES:
            assert secret not in body

    def test_positions_structure(self, app_with_secrets):
        response = app_with_secrets.get("/positions")
        data = response.json()
        assert "open_positions" in data
        assert "total_exposure" in data


class TestRiskManagerStatusSecurity:
    def test_risk_status_no_secrets(self):
        """Direct test: RiskManager.get_status() must not contain secrets."""
        from safety.risk_manager import RiskManager
        settings = Settings(
            KALSHI_API_KEY="leaked-key",
            POLYMARKET_PRIVATE_KEY="0xleaked",
            KILL_SWITCH_TOKEN="leaked-token",
        )
        rm = RiskManager(settings=settings)
        status = rm.get_status()
        status_str = str(status)
        assert "leaked-key" not in status_str
        assert "0xleaked" not in status_str
        assert "leaked-token" not in status_str


class TestCircuitBreakerStatusSecurity:
    def test_cb_status_no_secrets(self):
        """Direct test: CircuitBreaker.get_status() must not contain secrets."""
        from safety.circuit_breaker import CircuitBreaker
        settings = Settings(
            KALSHI_API_KEY="leaked-key",
            POLYMARKET_PRIVATE_KEY="0xleaked",
        )
        cb = CircuitBreaker(settings=settings)
        status = cb.get_status()
        status_str = str(status)
        assert "leaked-key" not in status_str
        assert "0xleaked" not in status_str
