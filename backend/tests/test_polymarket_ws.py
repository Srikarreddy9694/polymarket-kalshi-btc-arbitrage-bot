"""
Unit tests for PolymarketWebSocket.

Tests cover:
- Message parsing (book_snapshot, book_update)
- Order book state management
- Best bid/ask extraction
- Multi-market subscription
- Callbacks
"""

import json
import time
import pytest

from streams.polymarket_ws import PolymarketWebSocket


@pytest.fixture
def pws():
    """PolymarketWebSocket with test URL."""
    return PolymarketWebSocket(url="wss://test.example.com/ws/market")


class TestSubscription:
    def test_subscribe_market(self, pws):
        pws.subscribe("token-abc-123")
        assert "token-abc-123" in pws._subscribed_markets

    def test_subscribe_deduplicated(self, pws):
        pws.subscribe("token-abc-123")
        pws.subscribe("token-abc-123")
        assert pws._subscribed_markets.count("token-abc-123") == 1

    def test_subscribe_multiple(self, pws):
        pws.subscribe("token-1")
        pws.subscribe("token-2")
        assert len(pws._subscribed_markets) == 2


class TestMessageParsing:
    def test_book_snapshot(self, pws):
        msg = json.dumps({
            "type": "book_snapshot",
            "market": "token-abc",
            "bids": [{"price": "0.38", "size": "100"}],
            "asks": [{"price": "0.42", "size": "50"}],
        })
        pws._process_message(msg)
        book = pws.get_book("token-abc")
        assert book is not None
        assert book["best_bid"] == pytest.approx(0.38)
        assert book["best_ask"] == pytest.approx(0.42)

    def test_book_update(self, pws):
        msg = json.dumps({
            "type": "book_update",
            "market": "token-def",
            "bids": [{"price": "0.55", "size": "200"}],
            "asks": [{"price": "0.60", "size": "100"}],
        })
        pws._process_message(msg)
        assert pws.get_best_bid("token-def") == pytest.approx(0.55)
        assert pws.get_best_ask("token-def") == pytest.approx(0.60)

    def test_empty_bids_asks(self, pws):
        msg = json.dumps({
            "type": "book_snapshot",
            "market": "token-empty",
            "bids": [],
            "asks": [],
        })
        pws._process_message(msg)
        assert pws.get_best_bid("token-empty") is None
        assert pws.get_best_ask("token-empty") is None

    def test_invalid_json(self, pws):
        pws._process_message("not json")
        assert pws.message_count == 0

    def test_missing_market_field(self, pws):
        msg = json.dumps({"type": "book_snapshot", "bids": [], "asks": []})
        pws._process_message(msg)
        assert len(pws._books) == 0

    def test_unknown_message_type_ignored(self, pws):
        msg = json.dumps({"type": "heartbeat", "market": "token-hb"})
        pws._process_message(msg)
        assert "token-hb" not in pws._books

    def test_plain_list_bids_asks(self, pws):
        """Handle simplified bid/ask format (just price numbers)."""
        msg = json.dumps({
            "type": "book",
            "market": "token-simple",
            "bids": [0.40, 0.39, 0.38],
            "asks": [0.42, 0.43, 0.44],
        })
        pws._process_message(msg)
        assert pws.get_best_bid("token-simple") == pytest.approx(0.40)
        assert pws.get_best_ask("token-simple") == pytest.approx(0.42)


class TestCallbacks:
    def test_callback_fires_on_update(self, pws):
        received = []
        pws.add_callback(lambda tid, data: received.append(tid))

        msg = json.dumps({
            "type": "book_update",
            "market": "token-cb",
            "bids": [{"price": "0.50"}],
            "asks": [],
        })
        pws._process_message(msg)
        assert received == ["token-cb"]

    def test_callback_error_handled(self, pws):
        def bad_cb(tid, data):
            raise RuntimeError("callback exploded")

        pws.add_callback(bad_cb)
        msg = json.dumps({
            "type": "book_update",
            "market": "token-err",
            "bids": [{"price": "0.50"}],
            "asks": [],
        })
        pws._process_message(msg)
        assert pws.message_count == 1  # Still processed


class TestBookAccess:
    def test_nonexistent_book(self, pws):
        assert pws.get_book("nonexistent") is None
        assert pws.get_best_bid("nonexistent") is None
        assert pws.get_best_ask("nonexistent") is None


class TestStaleness:
    def test_initial_age_infinite(self, pws):
        assert pws.age_seconds == float("inf")

    def test_age_after_message(self, pws):
        msg = json.dumps({
            "type": "book_snapshot",
            "market": "token-age",
            "bids": [{"price": "0.40"}],
            "asks": [],
        })
        pws._process_message(msg)
        assert pws.age_seconds < 1.0


class TestStatus:
    def test_status_structure(self, pws):
        status = pws.get_status()
        assert "connected" in status
        assert "subscribed_markets" in status
        assert "books_cached" in status
        assert "message_count" in status

    def test_status_after_subscribe(self, pws):
        pws.subscribe("token-1")
        pws.subscribe("token-2")
        status = pws.get_status()
        assert status["subscribed_markets"] == 2
