"""
Unit tests for BinanceWebSocket.

Tests cover:
- Message parsing (ticker format)
- Price updates and callbacks
- Reconnect state management
- Staleness detection
- Status reporting
"""

import json
import time
import pytest

from streams.binance_ws import BinanceWebSocket


@pytest.fixture
def bws():
    """BinanceWebSocket with test URL."""
    return BinanceWebSocket(url="wss://test.example.com/ws/btcusdt@ticker")


class TestMessageParsing:
    def test_valid_ticker_message(self, bws):
        msg = json.dumps({"e": "24hrTicker", "c": "96543.21", "s": "BTCUSDT"})
        bws._process_message(msg)
        assert bws.price == pytest.approx(96543.21)
        assert bws.message_count == 1

    def test_multiple_updates(self, bws):
        for price in [96000, 96100, 96200]:
            msg = json.dumps({"c": str(price)})
            bws._process_message(msg)
        assert bws.price == pytest.approx(96200)
        assert bws.message_count == 3

    def test_zero_price_ignored(self, bws):
        msg = json.dumps({"c": "0"})
        bws._process_message(msg)
        assert bws.price is None

    def test_negative_price_ignored(self, bws):
        msg = json.dumps({"c": "-100"})
        bws._process_message(msg)
        assert bws.price is None

    def test_invalid_json_handled(self, bws):
        bws._process_message("not json at all")
        assert bws.price is None
        assert bws.message_count == 0

    def test_missing_price_field(self, bws):
        msg = json.dumps({"e": "24hrTicker", "s": "BTCUSDT"})
        bws._process_message(msg)
        assert bws.price is None


class TestCallbacks:
    def test_callback_fires_on_update(self, bws):
        received = []
        bws.add_callback(lambda p, t: received.append(p))

        msg = json.dumps({"c": "97000.50"})
        bws._process_message(msg)

        assert len(received) == 1
        assert received[0] == pytest.approx(97000.50)

    def test_multiple_callbacks(self, bws):
        r1, r2 = [], []
        bws.add_callback(lambda p, t: r1.append(p))
        bws.add_callback(lambda p, t: r2.append(p))

        bws._process_message(json.dumps({"c": "95000"}))
        assert len(r1) == 1
        assert len(r2) == 1

    def test_callback_error_doesnt_crash(self, bws):
        def bad_callback(p, t):
            raise ValueError("boom")

        bws.add_callback(bad_callback)
        bws._process_message(json.dumps({"c": "95000"}))
        assert bws.price is not None  # Still processed despite callback error


class TestStaleness:
    def test_initial_age_is_infinite(self, bws):
        assert bws.age_seconds == float("inf")

    def test_age_after_update(self, bws):
        bws._process_message(json.dumps({"c": "95000"}))
        assert bws.age_seconds < 1.0

    def test_last_update_tracked(self, bws):
        assert bws.last_update == 0.0
        bws._process_message(json.dumps({"c": "95000"}))
        assert bws.last_update > 0


class TestState:
    def test_initial_not_connected(self, bws):
        assert bws.is_connected is False

    def test_initial_price_is_none(self, bws):
        assert bws.price is None

    @pytest.mark.asyncio
    async def test_stop(self, bws):
        await bws.stop()
        assert bws.is_connected is False


class TestStatus:
    def test_status_structure(self, bws):
        status = bws.get_status()
        assert "connected" in status
        assert "price" in status
        assert "message_count" in status
        assert "url" in status

    def test_status_after_update(self, bws):
        bws._process_message(json.dumps({"c": "96000"}))
        status = bws.get_status()
        assert status["price"] == pytest.approx(96000)
        assert status["message_count"] == 1
        assert status["age_seconds"] is not None
