"""
Unit tests for KalshiAuthClient.

Tests cover:
- Dry-run order placement (no actual API calls)
- Auth header generation (mocked keys)
- Balance/position methods
- Order intent logging
"""

import pytest
from unittest.mock import patch, MagicMock, mock_open

from clients.kalshi_auth_client import KalshiAuthClient
from config.settings import Settings


@pytest.fixture
def auth_settings(test_settings):
    """Settings with auth credentials set."""
    test_settings.KALSHI_API_KEY = "test-api-key"
    test_settings.KALSHI_PRIVATE_KEY_PATH = "/tmp/test_key.pem"
    return test_settings


@pytest.fixture
def auth_client(auth_settings):
    """KalshiAuthClient with test settings (no real key loaded)."""
    return KalshiAuthClient(
        api_key="test-api-key",
        private_key_path="/tmp/test_key.pem",
        base_url="https://demo.kalshi.com/trade-api/v2",
        settings=auth_settings,
    )


class TestKalshiAuthClientInit:
    def test_init_with_credentials(self, auth_settings):
        client = KalshiAuthClient(
            api_key="key123",
            private_key_path="/tmp/k.pem",
            settings=auth_settings,
        )
        assert client.api_key == "key123"
        assert client.private_key_path == "/tmp/k.pem"

    def test_init_without_credentials(self, test_settings):
        test_settings.KALSHI_API_KEY = ""
        client = KalshiAuthClient(settings=test_settings)
        assert client.api_key == ""

    def test_base_url_trailing_slash_stripped(self, auth_settings):
        client = KalshiAuthClient(
            base_url="https://demo.kalshi.com/trade-api/v2/",
            settings=auth_settings,
        )
        assert not client.base_url.endswith("/")


class TestKalshiDryRunOrders:
    def test_dry_run_order_returns_intent(self, auth_client, auth_settings):
        auth_settings.DRY_RUN = True

        result, err = auth_client.place_order(
            ticker="KXBTCD-TEST",
            side="yes",
            action="buy",
            count=5,
            price_cents=45,
        )
        assert err is None
        assert result is not None
        assert result["dry_run"] is True
        assert result["intent"]["ticker"] == "KXBTCD-TEST"
        assert result["intent"]["side"] == "yes"
        assert result["intent"]["count"] == 5

    def test_dry_run_no_order(self, auth_client, auth_settings):
        auth_settings.DRY_RUN = True

        result, err = auth_client.place_order(
            ticker="KXBTCD-TEST",
            side="no",
            action="buy",
            count=3,
            price_cents=67,
        )
        assert result["dry_run"] is True
        assert result["intent"]["side"] == "no"
        assert "no_price" in result["intent"]

    def test_explicit_dry_run_overrides_settings(self, auth_client, auth_settings):
        auth_settings.DRY_RUN = False

        result, err = auth_client.place_order(
            ticker="KXBTCD-TEST",
            side="yes",
            action="buy",
            count=1,
            price_cents=50,
            dry_run=True,  # Explicit override
        )
        assert result["dry_run"] is True


class TestKalshiAuthHeaders:
    def test_auth_headers_structure(self, auth_client):
        """Test that _auth_headers produces the right keys (without actual signing)."""
        # We can't test actual RSA signing without a real key,
        # but we can mock _sign_request
        with patch.object(auth_client, '_sign_request', return_value="mock-signature"):
            headers = auth_client._auth_headers("GET", "/portfolio/balance")
            assert "KALSHI-ACCESS-KEY" in headers
            assert "KALSHI-ACCESS-SIGNATURE" in headers
            assert "KALSHI-ACCESS-TIMESTAMP" in headers
            assert headers["KALSHI-ACCESS-KEY"] == "test-api-key"
            assert headers["KALSHI-ACCESS-SIGNATURE"] == "mock-signature"


class TestKalshiAccountMethods:
    def test_get_balance_with_mocked_response(self, auth_client):
        """Test balance parsing with mocked API response."""
        with patch.object(auth_client, '_authenticated_request', return_value={"balance": 5000}):
            balance, err = auth_client.get_balance()
            assert err is None
            assert balance == 50.0  # 5000 cents = $50.00

    def test_get_balance_error_handling(self, auth_client):
        """Test that balance errors are handled gracefully."""
        with patch.object(auth_client, '_authenticated_request', side_effect=Exception("Connection failed")):
            balance, err = auth_client.get_balance()
            assert err is not None
            assert "Connection failed" in err
            assert balance == 0.0

    def test_get_positions_with_mocked_response(self, auth_client):
        mock_positions = [
            {"ticker": "KXBTCD-96000", "side": "yes", "count": 1},
            {"ticker": "KXBTCD-97000", "side": "no", "count": 2},
        ]
        with patch.object(auth_client, '_authenticated_request', return_value={"market_positions": mock_positions}):
            positions, err = auth_client.get_positions()
            assert err is None
            assert len(positions) == 2

    def test_get_positions_error_handling(self, auth_client):
        with patch.object(auth_client, '_authenticated_request', side_effect=Exception("Timeout")):
            positions, err = auth_client.get_positions()
            assert err is not None
            assert positions == []


class TestKalshiCancelOrder:
    def test_cancel_with_mocked_response(self, auth_client):
        with patch.object(auth_client, '_authenticated_request', return_value={"status": "cancelled"}):
            result, err = auth_client.cancel_order("order-123")
            assert err is None
            assert result["status"] == "cancelled"

    def test_cancel_error_handling(self, auth_client):
        with patch.object(auth_client, '_authenticated_request', side_effect=Exception("Not found")):
            result, err = auth_client.cancel_order("invalid-id")
            assert err is not None
            assert result is None
