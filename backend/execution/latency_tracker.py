"""
Latency Tracker — measures execution timing for each trade leg.

Provides precise timing for:
- Opportunity detection to first leg placed
- First leg placed to second leg placed
- Total round-trip time
- Historical latency statistics (P50, P95, P99)

Target: < 500ms from detection to both orders placed.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class LatencyMeasurement:
    """A single latency measurement for an execution cycle."""

    __slots__ = (
        "trade_id", "detected_at", "leg1_sent_at", "leg1_filled_at",
        "leg2_sent_at", "leg2_filled_at", "completed_at",
    )

    def __init__(self, trade_id: str = ""):
        self.trade_id = trade_id
        self.detected_at: float = 0.0
        self.leg1_sent_at: float = 0.0
        self.leg1_filled_at: float = 0.0
        self.leg2_sent_at: float = 0.0
        self.leg2_filled_at: float = 0.0
        self.completed_at: float = 0.0

    def mark_detected(self) -> None:
        self.detected_at = time.time()

    def mark_leg1_sent(self) -> None:
        self.leg1_sent_at = time.time()

    def mark_leg1_filled(self) -> None:
        self.leg1_filled_at = time.time()

    def mark_leg2_sent(self) -> None:
        self.leg2_sent_at = time.time()

    def mark_leg2_filled(self) -> None:
        self.leg2_filled_at = time.time()

    def mark_completed(self) -> None:
        self.completed_at = time.time()

    @property
    def detection_to_leg1_ms(self) -> Optional[float]:
        if self.detected_at and self.leg1_sent_at:
            return (self.leg1_sent_at - self.detected_at) * 1000
        return None

    @property
    def leg1_to_leg2_ms(self) -> Optional[float]:
        if self.leg1_sent_at and self.leg2_sent_at:
            return (self.leg2_sent_at - self.leg1_sent_at) * 1000
        return None

    @property
    def total_ms(self) -> Optional[float]:
        if self.detected_at and self.completed_at:
            return (self.completed_at - self.detected_at) * 1000
        return None

    @property
    def leg1_fill_ms(self) -> Optional[float]:
        if self.leg1_sent_at and self.leg1_filled_at:
            return (self.leg1_filled_at - self.leg1_sent_at) * 1000
        return None

    @property
    def leg2_fill_ms(self) -> Optional[float]:
        if self.leg2_sent_at and self.leg2_filled_at:
            return (self.leg2_filled_at - self.leg2_sent_at) * 1000
        return None

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "detection_to_leg1_ms": self._round(self.detection_to_leg1_ms),
            "leg1_fill_ms": self._round(self.leg1_fill_ms),
            "leg1_to_leg2_ms": self._round(self.leg1_to_leg2_ms),
            "leg2_fill_ms": self._round(self.leg2_fill_ms),
            "total_ms": self._round(self.total_ms),
        }

    @staticmethod
    def _round(val: Optional[float]) -> Optional[float]:
        return round(val, 2) if val is not None else None


class LatencyTracker:
    """
    Tracks and reports execution latency across all trades.

    Maintains a rolling window of measurements for percentile calculations.
    """

    def __init__(self, max_history: int = 500):
        self._history: deque = deque(maxlen=max_history)
        self._current: Optional[LatencyMeasurement] = None
        self._total_trades: int = 0

    def start_measurement(self, trade_id: str = "") -> LatencyMeasurement:
        """Start a new latency measurement."""
        m = LatencyMeasurement(trade_id=trade_id or f"trade-{self._total_trades + 1}")
        m.mark_detected()
        self._current = m
        return m

    def complete_measurement(self, measurement: LatencyMeasurement) -> None:
        """Complete a measurement and add to history."""
        measurement.mark_completed()
        self._history.append(measurement)
        self._total_trades += 1

        total = measurement.total_ms
        if total is not None:
            level = "info" if total < 500 else "warning"
            getattr(logger, level)(
                "⏱ Latency: total=%.0fms | detect→leg1=%.0fms | leg1→leg2=%.0fms | trade=%s",
                total,
                measurement.detection_to_leg1_ms or 0,
                measurement.leg1_to_leg2_ms or 0,
                measurement.trade_id,
            )

    def get_percentiles(self) -> dict:
        """Calculate P50, P95, P99 from recent history."""
        totals = sorted(
            [m.total_ms for m in self._history if m.total_ms is not None]
        )
        if not totals:
            return {"p50_ms": None, "p95_ms": None, "p99_ms": None, "count": 0}

        return {
            "p50_ms": round(self._percentile(totals, 50), 1),
            "p95_ms": round(self._percentile(totals, 95), 1),
            "p99_ms": round(self._percentile(totals, 99), 1),
            "count": len(totals),
            "min_ms": round(min(totals), 1),
            "max_ms": round(max(totals), 1),
            "avg_ms": round(sum(totals) / len(totals), 1),
        }

    def get_recent(self, n: int = 10) -> List[dict]:
        """Get the N most recent measurements."""
        recent = list(self._history)[-n:]
        return [m.to_dict() for m in recent]

    def get_status(self) -> dict:
        """Full latency status for monitoring. No secrets."""
        return {
            "total_trades_measured": self._total_trades,
            "percentiles": self.get_percentiles(),
            "target_ms": 500,
            "meets_target": self._meets_target(),
        }

    def _meets_target(self) -> Optional[bool]:
        """Check if P95 latency is under 500ms target."""
        p = self.get_percentiles()
        p95 = p.get("p95_ms")
        if p95 is None:
            return None
        return p95 < 500

    @staticmethod
    def _percentile(sorted_data: List[float], pct: int) -> float:
        """Calculate percentile from sorted list."""
        if not sorted_data:
            return 0.0
        k = (len(sorted_data) - 1) * (pct / 100)
        f = int(k)
        c = f + 1
        if c >= len(sorted_data):
            return sorted_data[-1]
        d0 = sorted_data[f] * (c - k)
        d1 = sorted_data[c] * (k - f)
        return d0 + d1
