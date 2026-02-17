"""
Unit tests for PolymarketExecClient.

Tests cover:
- Dry-run order placement
- Order intent logging
- Token allowance handling
- Client initialization without py-clob-client installed
"""

import pytest
from unittest.mock import patch, MagicMock

from clients.polymarket_exec_client import PolymarketExecClient
from config.settings import Settings


@pytest.fixture
def exec_client(test_settings):
    """PolymarketExecClient with test settings (no real key)."""
    test_settings.POLYMARKET_PRIVATE_KEY = "0xtest_private_key"
    return PolymarketExecClient(
        private_key="0xtest_private_key",
        settings=test_settings,
    )


@pytest.fixture
def exec_client_no_key(test_settings):
    """PolymarketExecClient without a private key."""
    test_settings.POLYMARKET_PRIVATE_KEY = ""
    return PolymarketExecClient(private_key="", settings=test_settings)


class TestPolyClientInit:
    def test_init_with_key(self, exec_client):
        assert exec_client.private_key == "0xtest_private_key"
        assert exec_client.chain_id == 137

    def test_init_without_key(self, exec_client_no_key):
        assert exec_client_no_key.private_key == ""


class TestPolyDryRunOrders:
    def test_dry_run_order_returns_intent(self, exec_client, test_settings):
        test_settings.DRY_RUN = True

        result, err = exec_client.place_order(
            token_id="0xabcdef1234567890",
            side="BUY",
            price=0.55,
            size=10.0,
        )
        assert err is None
        assert result is not None
        assert result["dry_run"] is True
        assert result["intent"]["side"] == "BUY"
        assert result["intent"]["price"] == 0.55
        assert result["intent"]["size"] == 10.0

    def test_dry_run_sell_order(self, exec_client, test_settings):
        test_settings.DRY_RUN = True

        result, err = exec_client.place_order(
            token_id="0xabcdef",
            side="SELL",
            price=0.70,
            size=5.0,
            order_type="GTC",
        )
        assert result["dry_run"] is True
        assert result["intent"]["side"] == "SELL"
        assert result["intent"]["order_type"] == "GTC"

    def test_explicit_dry_run_overrides_settings(self, exec_client, test_settings):
        test_settings.DRY_RUN = False

        result, err = exec_client.place_order(
            token_id="0xabcdef",
            side="BUY",
            price=0.40,
            size=1.0,
            dry_run=True,
        )
        assert result["dry_run"] is True

    def test_fok_is_default_order_type(self, exec_client, test_settings):
        test_settings.DRY_RUN = True

        result, err = exec_client.place_order(
            token_id="0xtest",
            side="BUY",
            price=0.50,
            size=1.0,
        )
        assert result["intent"]["order_type"] == "FOK"


class TestPolyAllowances:
    def test_dry_run_allowances(self, exec_client, test_settings):
        test_settings.DRY_RUN = True

        success, err = exec_client.set_allowances()
        assert success is True
        assert err is None

    def test_explicit_dry_run_allowances(self, exec_client, test_settings):
        test_settings.DRY_RUN = False

        success, err = exec_client.set_allowances(dry_run=True)
        assert success is True
        assert err is None


class TestPolyClientLazyInit:
    def test_get_client_fails_without_key(self, exec_client_no_key):
        with pytest.raises(ValueError, match="POLYMARKET_PRIVATE_KEY not configured"):
            exec_client_no_key._get_client()

    def test_get_client_fails_without_library(self, exec_client):
        """If py-clob-client is not installed, should raise ImportError."""
        with patch.dict("sys.modules", {"py_clob_client": None, "py_clob_client.client": None}):
            # The import inside _get_client will fail
            with pytest.raises((ImportError, ValueError)):
                exec_client._get_client()
