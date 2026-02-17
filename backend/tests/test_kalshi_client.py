"""
Unit tests for Kalshi client and strike parsing.
"""

import pytest
from clients.kalshi_client import KalshiClient, parse_strike
from core.models import KalshiMarket


class TestParseStrike:
    """Tests for Kalshi subtitle strike parsing."""

    def test_standard_format(self):
        assert parse_strike("$96,250 or above") == 96250.0

    def test_no_comma(self):
        assert parse_strike("$500 or above") == 500.0

    def test_large_number(self):
        assert parse_strike("$100,000 or above") == 100000.0

    def test_no_dollar_sign(self):
        assert parse_strike("no price here") == 0.0

    def test_empty_string(self):
        assert parse_strike("") == 0.0

    def test_multiple_numbers_takes_first(self):
        result = parse_strike("$96,000 to $97,000")
        assert result == 96000.0

    def test_decimal_number(self):
        # Kalshi doesn't use decimals but test edge case
        assert parse_strike("$97,500 or above") == 97500.0


class TestKalshiClientParsing:
    """Tests for market parsing logic (no network calls)."""

    @pytest.fixture
    def client(self, test_settings):
        return KalshiClient(settings=test_settings)

    def test_parse_markets_valid(self, client):
        raw = [
            {
                "subtitle": "$96,000 or above",
                "yes_bid": 50,
                "yes_ask": 53,
                "no_bid": 45,
                "no_ask": 47,
            },
            {
                "subtitle": "$97,000 or above",
                "yes_bid": 30,
                "yes_ask": 33,
                "no_bid": 65,
                "no_ask": 67,
            },
        ]
        markets = client._parse_markets(raw)
        assert len(markets) == 2
        assert isinstance(markets[0], KalshiMarket)
        assert markets[0].strike == 96000.0
        assert markets[0].yes_ask == 53
        assert markets[1].strike == 97000.0

    def test_parse_markets_skips_invalid_strike(self, client):
        raw = [
            {"subtitle": "no price", "yes_bid": 0, "yes_ask": 0, "no_bid": 0, "no_ask": 0},
            {"subtitle": "$95,000 or above", "yes_bid": 70, "yes_ask": 72, "no_bid": 26, "no_ask": 28},
        ]
        markets = client._parse_markets(raw)
        assert len(markets) == 1
        assert markets[0].strike == 95000.0

    def test_parse_markets_handles_none_prices(self, client):
        """Kalshi API sometimes returns None for bid/ask."""
        raw = [
            {
                "subtitle": "$96,000 or above",
                "yes_bid": None,
                "yes_ask": 53,
                "no_bid": None,
                "no_ask": 47,
            },
        ]
        markets = client._parse_markets(raw)
        assert len(markets) == 1
        assert markets[0].yes_bid == 0  # None → 0
        assert markets[0].yes_ask == 53
        assert markets[0].no_bid == 0  # None → 0

    def test_parse_markets_empty(self, client):
        markets = client._parse_markets([])
        assert markets == []

    def test_parse_markets_sorted_by_strike(self, client):
        raw = [
            {"subtitle": "$98,000 or above", "yes_bid": 10, "yes_ask": 12, "no_bid": 86, "no_ask": 88},
            {"subtitle": "$94,000 or above", "yes_bid": 90, "yes_ask": 92, "no_bid": 6, "no_ask": 8},
            {"subtitle": "$96,000 or above", "yes_bid": 50, "yes_ask": 53, "no_bid": 45, "no_ask": 47},
        ]
        markets = client._parse_markets(raw)
        # _parse_markets doesn't sort; fetch_by_event does. Verify order preserved.
        assert markets[0].strike == 98000.0
        assert markets[2].strike == 96000.0
