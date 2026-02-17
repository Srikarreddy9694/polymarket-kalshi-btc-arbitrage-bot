"""
Unit tests for Polymarket client and order book depth analysis.
"""

import pytest
from clients.polymarket_client import OrderBook, OrderBookLevel, PolymarketClient
from config.settings import Settings


# ── OrderBookLevel Tests ──────────────────────────────


class TestOrderBookLevel:
    def test_repr(self):
        level = OrderBookLevel(price=0.55, size=100.0)
        assert "0.55" in repr(level)
        assert "100.0" in repr(level)


# ── OrderBook Tests ───────────────────────────────────


class TestOrderBook:
    @pytest.fixture
    def sample_book(self):
        """Order book with known depth."""
        return OrderBook(
            bids=[
                OrderBookLevel(price=0.50, size=200.0),
                OrderBookLevel(price=0.49, size=300.0),
                OrderBookLevel(price=0.48, size=500.0),
            ],
            asks=[
                OrderBookLevel(price=0.52, size=150.0),
                OrderBookLevel(price=0.53, size=250.0),
                OrderBookLevel(price=0.55, size=400.0),
            ],
        )

    def test_best_bid(self, sample_book):
        assert sample_book.best_bid == 0.50

    def test_best_ask(self, sample_book):
        assert sample_book.best_ask == 0.52

    def test_spread(self, sample_book):
        assert abs(sample_book.spread - 0.02) < 1e-10

    def test_mid_price(self, sample_book):
        assert abs(sample_book.mid_price - 0.51) < 1e-10

    def test_empty_book(self):
        book = OrderBook(bids=[], asks=[])
        assert book.best_bid == 0.0
        assert book.best_ask == 0.0
        assert book.spread == 0.0

    def test_bids_sorted_descending(self, sample_book):
        prices = [b.price for b in sample_book.bids]
        assert prices == sorted(prices, reverse=True)

    def test_asks_sorted_ascending(self, sample_book):
        prices = [a.price for a in sample_book.asks]
        assert prices == sorted(prices)


class TestOrderBookFillableAmount:
    @pytest.fixture
    def book(self):
        return OrderBook(
            bids=[
                OrderBookLevel(price=0.50, size=100.0),
                OrderBookLevel(price=0.48, size=200.0),
            ],
            asks=[
                OrderBookLevel(price=0.52, size=100.0),  # $52 total
                OrderBookLevel(price=0.55, size=200.0),  # $110 total
                OrderBookLevel(price=0.60, size=300.0),  # $180 total
            ],
        )

    def test_buy_all_at_first_level(self, book):
        contracts, cost = book.fillable_amount("BUY", max_price=0.52, max_usd=1000.0)
        assert contracts == 100.0
        assert abs(cost - 52.0) < 1e-6

    def test_buy_across_levels(self, book):
        contracts, cost = book.fillable_amount("BUY", max_price=0.55, max_usd=1000.0)
        # Level 1: 100 @ 0.52 = $52
        # Level 2: 200 @ 0.55 = $110
        assert contracts == 300.0
        assert abs(cost - 162.0) < 1e-6

    def test_buy_budget_limited(self, book):
        contracts, cost = book.fillable_amount("BUY", max_price=1.0, max_usd=60.0)
        # Level 1: 100 @ 0.52 = $52, remaining $8
        # Level 2: $8 / 0.55 ≈ 14.545 contracts
        assert contracts == pytest.approx(114.545, abs=0.01)
        assert cost == pytest.approx(60.0, abs=0.01)

    def test_buy_nothing_if_price_too_low(self, book):
        contracts, cost = book.fillable_amount("BUY", max_price=0.40, max_usd=1000.0)
        assert contracts == 0.0
        assert cost == 0.0

    def test_sell_walks_bids(self, book):
        contracts, cost = book.fillable_amount("SELL", max_price=0.48, max_usd=1000.0)
        # Bids: 0.50 (100), 0.48 (200) — both at or above 0.48
        assert contracts == 300.0
        assert abs(cost - 146.0) < 1e-6  # 100*0.50 + 200*0.48


class TestOrderBookLiquidity:
    def test_total_ask_liquidity(self):
        book = OrderBook(
            bids=[],
            asks=[
                OrderBookLevel(price=0.52, size=100.0),
                OrderBookLevel(price=0.55, size=200.0),
                OrderBookLevel(price=0.80, size=50.0),
            ],
        )
        assert book.total_ask_liquidity(0.60) == 300.0  # 100 + 200
        assert book.total_ask_liquidity(1.0) == 350.0  # all
        assert book.total_ask_liquidity(0.50) == 0.0  # none

    def test_total_bid_liquidity(self):
        book = OrderBook(
            bids=[
                OrderBookLevel(price=0.50, size=100.0),
                OrderBookLevel(price=0.45, size=200.0),
                OrderBookLevel(price=0.30, size=50.0),
            ],
            asks=[],
        )
        assert book.total_bid_liquidity(0.45) == 300.0  # 100 + 200
        assert book.total_bid_liquidity(0.0) == 350.0  # all
        assert book.total_bid_liquidity(0.60) == 0.0  # none
