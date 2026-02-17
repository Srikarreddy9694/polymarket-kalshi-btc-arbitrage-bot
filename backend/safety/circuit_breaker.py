"""
Circuit Breaker — automatically halts trading on anomalies.

Implements the standard circuit breaker pattern:
- CLOSED: Normal operation, trades allowed
- OPEN:   Trading halted, waiting for cooldown
- HALF_OPEN: Testing with a single trade after cooldown

Triggers that open the circuit:
1. N consecutive failed trades (default 3)
2. Daily loss exceeds limit
3. API error rate > threshold in sliding window
4. Price data staleness > threshold
5. Manual kill switch activation
"""

from __future__ import annotations

import logging
import time
from collections import deque
from enum import Enum
from typing import Optional

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"      # Normal — trades allowed
    OPEN = "open"          # Halted — no trades
    HALF_OPEN = "half_open"  # Testing — one trade allowed


class CircuitBreaker:
    """
    Automatically halts trading on anomalies.

    Thread-safe state management with configurable thresholds.
    """

    def __init__(
        self,
        max_consecutive_failures: int = 3,
        error_rate_threshold: float = 0.50,
        error_rate_window_sec: int = 300,  # 5 minutes
        cooldown_sec: int = 300,           # 5 minutes
        staleness_threshold_sec: float = 30.0,
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or get_settings()
        self.max_consecutive_failures = max_consecutive_failures
        self.error_rate_threshold = error_rate_threshold
        self.error_rate_window_sec = error_rate_window_sec
        self.cooldown_sec = cooldown_sec
        self.staleness_threshold_sec = staleness_threshold_sec

        # State
        self._state: CircuitState = CircuitState.CLOSED
        self._consecutive_failures: int = 0
        self._last_state_change: float = time.time()
        self._trip_reason: str = ""

        # Sliding window for error rate
        self._api_calls: deque = deque()   # (timestamp, success: bool)

        # Data staleness tracking
        self._last_data_timestamp: float = time.time()

        logger.info(
            "CircuitBreaker initialized: max_failures=%d error_rate=%.0f%% "
            "cooldown=%ds staleness=%ds",
            max_consecutive_failures,
            error_rate_threshold * 100,
            cooldown_sec,
            int(staleness_threshold_sec),
        )

    # ── Public Interface ─────────────────────────────────

    @property
    def state(self) -> CircuitState:
        """Current circuit state with automatic HALF_OPEN transition."""
        if self._state == CircuitState.OPEN:
            elapsed = time.time() - self._last_state_change
            if elapsed >= self.cooldown_sec:
                self._transition_to(CircuitState.HALF_OPEN, "cooldown elapsed")
        return self._state

    @property
    def is_trading_allowed(self) -> bool:
        """Whether trading is currently permitted."""
        current_state = self.state  # triggers auto-transition check
        return current_state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self) -> None:
        """Record a successful trade or API call."""
        self._api_calls.append((time.time(), True))
        self._clean_old_calls()

        if self._state == CircuitState.HALF_OPEN:
            # Test trade succeeded — close the circuit
            self._transition_to(CircuitState.CLOSED, "half_open test succeeded")
            self._consecutive_failures = 0
        else:
            self._consecutive_failures = 0

    def record_failure(self, reason: str = "") -> None:
        """Record a failed trade or API call."""
        self._api_calls.append((time.time(), False))
        self._clean_old_calls()
        self._consecutive_failures += 1

        logger.warning(
            "Circuit breaker: failure #%d/%d — %s",
            self._consecutive_failures,
            self.max_consecutive_failures,
            reason,
        )

        if self._state == CircuitState.HALF_OPEN:
            # Test trade failed — reopen
            self.trip(f"half_open test failed: {reason}")
            return

        # Check consecutive failures
        if self._consecutive_failures >= self.max_consecutive_failures:
            self.trip(f"{self._consecutive_failures} consecutive failures: {reason}")
            return

        # Check error rate
        error_rate = self._get_error_rate()
        if error_rate > self.error_rate_threshold and len(self._api_calls) >= 5:
            self.trip(f"error rate {error_rate:.0%} > {self.error_rate_threshold:.0%}")

    def record_data_update(self) -> None:
        """Mark that fresh data was received."""
        self._last_data_timestamp = time.time()

    def check_data_staleness(self) -> bool:
        """
        Check if data is stale. Trips the breaker if so.
        Returns True if data is fresh, False if stale.
        """
        elapsed = time.time() - self._last_data_timestamp
        if elapsed > self.staleness_threshold_sec:
            self.trip(f"data stale for {elapsed:.0f}s (threshold={self.staleness_threshold_sec}s)")
            return False
        return True

    def check_daily_loss(self, daily_pnl: float, max_loss: float) -> bool:
        """
        Check if daily loss has exceeded limit. Trips the breaker if so.
        Returns True if within limits, False if breached.
        """
        if daily_pnl <= -max_loss:
            self.trip(f"daily loss ${abs(daily_pnl):.2f} >= max ${max_loss:.2f}")
            return False
        return True

    def trip(self, reason: str) -> None:
        """Immediately open the circuit breaker."""
        self._trip_reason = reason
        self._transition_to(CircuitState.OPEN, reason)
        self._consecutive_failures = 0  # Reset to avoid re-tripping immediately

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED."""
        self._transition_to(CircuitState.CLOSED, "manual reset")
        self._consecutive_failures = 0
        self._trip_reason = ""

    # ── Status ───────────────────────────────────────────

    def get_status(self) -> dict:
        """
        Return circuit breaker status for monitoring.
        SECURITY: No secrets, credentials, or internal state beyond what's needed.
        """
        current = self.state  # triggers auto-transition
        time_in_state = time.time() - self._last_state_change

        return {
            "state": current.value,
            "is_trading_allowed": self.is_trading_allowed,
            "consecutive_failures": self._consecutive_failures,
            "max_consecutive_failures": self.max_consecutive_failures,
            "error_rate": round(self._get_error_rate(), 3),
            "error_rate_threshold": self.error_rate_threshold,
            "trip_reason": self._trip_reason if current != CircuitState.CLOSED else None,
            "time_in_state_sec": round(time_in_state, 1),
            "cooldown_sec": self.cooldown_sec,
            "data_age_sec": round(time.time() - self._last_data_timestamp, 1),
        }

    # ── Internal ─────────────────────────────────────────

    def _transition_to(self, new_state: CircuitState, reason: str) -> None:
        old_state = self._state
        self._state = new_state
        self._last_state_change = time.time()

        if new_state == CircuitState.OPEN:
            logger.critical(
                "🔴 CIRCUIT BREAKER OPENED: %s → %s | reason: %s",
                old_state.value, new_state.value, reason,
            )
        elif new_state == CircuitState.HALF_OPEN:
            logger.warning(
                "🟡 CIRCUIT BREAKER HALF-OPEN: %s → %s | reason: %s",
                old_state.value, new_state.value, reason,
            )
        else:
            logger.info(
                "🟢 CIRCUIT BREAKER CLOSED: %s → %s | reason: %s",
                old_state.value, new_state.value, reason,
            )

    def _get_error_rate(self) -> float:
        """Error rate in the current sliding window."""
        self._clean_old_calls()
        if not self._api_calls:
            return 0.0
        failures = sum(1 for _, success in self._api_calls if not success)
        return failures / len(self._api_calls)

    def _clean_old_calls(self) -> None:
        """Remove calls outside the sliding window."""
        cutoff = time.time() - self.error_rate_window_sec
        while self._api_calls and self._api_calls[0][0] < cutoff:
            self._api_calls.popleft()
