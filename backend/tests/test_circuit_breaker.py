"""
Unit tests for CircuitBreaker.

Tests cover:
- State transitions: CLOSED → OPEN → HALF_OPEN → CLOSED
- All 5 trigger types
- Auto-recovery after cooldown
- Error rate sliding window
- Data staleness detection
"""

import time
import pytest
from unittest.mock import patch

from safety.circuit_breaker import CircuitBreaker, CircuitState


@pytest.fixture
def cb():
    """CircuitBreaker with short cooldown for fast tests."""
    return CircuitBreaker(
        max_consecutive_failures=3,
        error_rate_threshold=0.50,
        error_rate_window_sec=60,
        cooldown_sec=1,  # 1 second for test speed
        staleness_threshold_sec=2.0,
    )


class TestInitialState:
    def test_starts_closed(self, cb):
        assert cb.state == CircuitState.CLOSED
        assert cb.is_trading_allowed is True

    def test_initial_status(self, cb):
        status = cb.get_status()
        assert status["state"] == "closed"
        assert status["is_trading_allowed"] is True
        assert status["consecutive_failures"] == 0


class TestConsecutiveFailures:
    def test_one_failure_stays_closed(self, cb):
        cb.record_failure("transient error")
        assert cb.state == CircuitState.CLOSED
        assert cb._consecutive_failures == 1

    def test_three_failures_opens_circuit(self, cb):
        for i in range(3):
            cb.record_failure(f"error {i}")
        assert cb.state == CircuitState.OPEN
        assert cb.is_trading_allowed is False

    def test_success_resets_failure_count(self, cb):
        cb.record_failure("err 1")
        cb.record_failure("err 2")
        cb.record_success()
        assert cb._consecutive_failures == 0
        assert cb.state == CircuitState.CLOSED


class TestManualTrip:
    def test_manual_trip(self, cb):
        cb.trip("manual emergency")
        assert cb.state == CircuitState.OPEN
        assert cb.is_trading_allowed is False

    def test_manual_reset(self, cb):
        cb.trip("test")
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.is_trading_allowed is True


class TestAutoRecovery:
    def test_half_open_after_cooldown(self, cb):
        cb.trip("test trip")
        assert cb.state == CircuitState.OPEN

        # Wait for cooldown (1 second)
        time.sleep(1.1)
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.is_trading_allowed is True

    def test_half_open_success_closes(self, cb):
        cb.trip("test")
        time.sleep(1.1)
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self, cb):
        cb.trip("test")
        time.sleep(1.1)
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_failure("still broken")
        assert cb.state == CircuitState.OPEN


class TestErrorRate:
    def test_high_error_rate_opens_circuit(self, cb):
        # 5 calls, 3 failures = 60% error rate > 50% threshold
        cb.record_success()
        cb.record_success()
        cb.record_failure("fail 1")
        cb.record_failure("fail 2")
        cb.record_failure("fail 3")
        assert cb.state == CircuitState.OPEN

    def test_low_error_rate_stays_closed(self, cb):
        # 5 calls, 1 failure = 20% error rate < 50% threshold
        for _ in range(4):
            cb.record_success()
        cb.record_failure("transient")
        assert cb.state == CircuitState.CLOSED

    def test_error_rate_requires_minimum_calls(self, cb):
        # 2 calls, 1 failure = 50%, but below 5-call minimum
        cb.record_success()
        cb.record_failure("fail")
        # Should stay closed because < 5 calls in window
        assert cb.state == CircuitState.CLOSED


class TestDataStaleness:
    def test_fresh_data_passes(self, cb):
        cb.record_data_update()
        assert cb.check_data_staleness() is True
        assert cb.state == CircuitState.CLOSED

    def test_stale_data_trips_breaker(self, cb):
        # Set last data timestamp to 5 seconds ago (threshold is 2s)
        cb._last_data_timestamp = time.time() - 5
        assert cb.check_data_staleness() is False
        assert cb.state == CircuitState.OPEN


class TestDailyLoss:
    def test_within_limit_passes(self, cb):
        assert cb.check_daily_loss(daily_pnl=-50, max_loss=100) is True
        assert cb.state == CircuitState.CLOSED

    def test_exceeding_limit_trips(self, cb):
        assert cb.check_daily_loss(daily_pnl=-100, max_loss=100) is False
        assert cb.state == CircuitState.OPEN


class TestStatus:
    def test_status_structure(self, cb):
        status = cb.get_status()
        required_keys = [
            "state", "is_trading_allowed", "consecutive_failures",
            "error_rate", "trip_reason", "cooldown_sec",
        ]
        for key in required_keys:
            assert key in status

    def test_status_shows_trip_reason_when_open(self, cb):
        cb.trip("test reason")
        status = cb.get_status()
        assert status["trip_reason"] == "test reason"

    def test_status_no_trip_reason_when_closed(self, cb):
        status = cb.get_status()
        assert status["trip_reason"] is None
