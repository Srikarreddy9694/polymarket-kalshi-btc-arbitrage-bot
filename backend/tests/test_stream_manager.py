"""
Unit tests for StreamManager.

Tests cover:
- Subscriber pub/sub system
- Callback routing from all 3 feeds
- Event emission and queue management
- Dead subscriber cleanup
- Status reporting
"""

import asyncio
import json
import pytest

from streams.stream_manager import StreamManager, StreamEvent
from streams.binance_ws import BinanceWebSocket
from streams.polymarket_ws import PolymarketWebSocket
from streams.kalshi_ws import KalshiPollingFeed


@pytest.fixture
def sm():
    """StreamManager with test feed instances."""
    return StreamManager(
        binance=BinanceWebSocket(url="wss://test/binance"),
        polymarket=PolymarketWebSocket(url="wss://test/poly"),
        kalshi=KalshiPollingFeed(poll_interval=999),
    )


class TestStreamEvent:
    def test_event_creation(self):
        e = StreamEvent("binance", "price", {"price": 96000})
        assert e.source == "binance"
        assert e.event_type == "price"
        assert e.timestamp > 0

    def test_event_to_dict(self):
        e = StreamEvent("kalshi", "market_data", {"strike": 96000}, timestamp=1000.0)
        d = e.to_dict()
        assert d["source"] == "kalshi"
        assert d["timestamp"] == 1000.0


class TestSubscribers:
    def test_subscribe_creates_queue(self, sm):
        q = sm.subscribe()
        assert isinstance(q, asyncio.Queue)
        assert len(sm._subscribers) == 1

    def test_unsubscribe_removes_queue(self, sm):
        q = sm.subscribe()
        sm.unsubscribe(q)
        assert len(sm._subscribers) == 0

    def test_unsubscribe_nonexistent_safe(self, sm):
        q = asyncio.Queue()
        sm.unsubscribe(q)  # Should not raise

    def test_multiple_subscribers(self, sm):
        q1 = sm.subscribe()
        q2 = sm.subscribe()
        assert len(sm._subscribers) == 2


class TestEventEmission:
    def test_binance_callback_emits(self, sm):
        q = sm.subscribe()
        sm._on_binance_price(96543.21, 1000.0)

        assert not q.empty()
        event = q.get_nowait()
        assert event["source"] == "binance"
        assert event["data"]["price"] == 96543.21

    def test_polymarket_callback_emits(self, sm):
        q = sm.subscribe()
        sm._on_polymarket_book("token-123", {"best_bid": 0.38, "best_ask": 0.42})

        event = q.get_nowait()
        assert event["source"] == "polymarket"
        assert event["data"]["token_id"] == "token-123"

    def test_kalshi_callback_emits(self, sm):
        q = sm.subscribe()
        sm._on_kalshi_data({"markets": [{"ticker": "KXBTCD"}]})

        event = q.get_nowait()
        assert event["source"] == "kalshi"
        assert "markets" in event["data"]

    def test_event_reaches_all_subscribers(self, sm):
        q1 = sm.subscribe()
        q2 = sm.subscribe()
        sm._on_binance_price(95000, 1000.0)

        assert not q1.empty()
        assert not q2.empty()

    def test_dead_subscriber_cleaned_up(self, sm):
        # Create a tiny queue that overflows immediately
        q = asyncio.Queue(maxsize=1)
        sm._subscribers.append(q)

        # Fill the queue
        q.put_nowait({"test": True})

        # This emit should overflow and remove the dead subscriber
        sm._on_binance_price(95000, 1000.0)

        # Dead queue should be removed
        assert q not in sm._subscribers


class TestEventCount:
    def test_event_count_increments(self, sm):
        sm.subscribe()
        sm._on_binance_price(95000, 1000.0)
        sm._on_binance_price(96000, 1001.0)
        assert sm._event_count == 2


class TestStatus:
    def test_status_structure(self, sm):
        status = sm.get_status()
        assert "running" in status
        assert "subscribers" in status
        assert "total_events" in status
        assert "binance" in status
        assert "polymarket" in status
        assert "kalshi" in status

    def test_status_shows_subscriber_count(self, sm):
        sm.subscribe()
        sm.subscribe()
        assert sm.get_status()["subscribers"] == 2

    def test_is_all_connected_initially_false(self, sm):
        assert sm.is_all_connected is False
