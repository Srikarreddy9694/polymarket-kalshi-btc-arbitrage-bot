"""
Unit tests for LatencyTracker.

Tests cover:
- Measurement lifecycle (start → mark legs → complete)
- Per-leg timing accuracy
- Percentile calculations (P50, P95, P99)
- Rolling window history
- Target compliance check
"""

import time
import pytest

from execution.latency_tracker import LatencyTracker, LatencyMeasurement


@pytest.fixture
def tracker():
    return LatencyTracker(max_history=100)


class TestLatencyMeasurement:
    def test_mark_detected(self):
        m = LatencyMeasurement(trade_id="t1")
        m.mark_detected()
        assert m.detected_at > 0

    def test_detection_to_leg1(self):
        m = LatencyMeasurement()
        m.detected_at = 1000.0
        m.leg1_sent_at = 1000.05  # 50ms later
        assert m.detection_to_leg1_ms == pytest.approx(50.0)

    def test_leg1_to_leg2(self):
        m = LatencyMeasurement()
        m.leg1_sent_at = 1000.0
        m.leg2_sent_at = 1000.200  # 200ms
        assert m.leg1_to_leg2_ms == pytest.approx(200.0)

    def test_total_ms(self):
        m = LatencyMeasurement()
        m.detected_at = 1000.0
        m.completed_at = 1000.450  # 450ms
        assert m.total_ms == pytest.approx(450.0)

    def test_fill_times(self):
        m = LatencyMeasurement()
        m.leg1_sent_at = 1000.0
        m.leg1_filled_at = 1000.030  # 30ms fill
        m.leg2_sent_at = 1000.200
        m.leg2_filled_at = 1000.350  # 150ms fill
        assert m.leg1_fill_ms == pytest.approx(30.0)
        assert m.leg2_fill_ms == pytest.approx(150.0)

    def test_none_when_incomplete(self):
        m = LatencyMeasurement()
        assert m.detection_to_leg1_ms is None
        assert m.leg1_to_leg2_ms is None
        assert m.total_ms is None

    def test_to_dict(self):
        m = LatencyMeasurement(trade_id="t1")
        m.detected_at = 1000.0
        m.leg1_sent_at = 1000.050
        m.completed_at = 1000.400
        d = m.to_dict()
        assert d["trade_id"] == "t1"
        assert d["detection_to_leg1_ms"] == pytest.approx(50.0)
        assert d["total_ms"] == pytest.approx(400.0)


class TestTracker:
    def test_start_measurement(self, tracker):
        m = tracker.start_measurement("trade-1")
        assert m.detected_at > 0
        assert m.trade_id == "trade-1"

    def test_complete_measurement(self, tracker):
        m = tracker.start_measurement()
        time.sleep(0.01)
        tracker.complete_measurement(m)
        assert m.completed_at > 0
        assert tracker._total_trades == 1

    def test_auto_generated_trade_id(self, tracker):
        m = tracker.start_measurement()
        assert m.trade_id == "trade-1"

    def test_history_grows(self, tracker):
        for i in range(5):
            m = tracker.start_measurement(f"t-{i}")
            tracker.complete_measurement(m)
        assert len(tracker._history) == 5

    def test_history_max_limit(self):
        tracker = LatencyTracker(max_history=3)
        for i in range(5):
            m = tracker.start_measurement(f"t-{i}")
            tracker.complete_measurement(m)
        assert len(tracker._history) == 3


class TestPercentiles:
    def test_empty_percentiles(self, tracker):
        p = tracker.get_percentiles()
        assert p["p50_ms"] is None
        assert p["count"] == 0

    def test_percentiles_with_data(self, tracker):
        # Simulate measurements with known latencies
        for total_ms in [100, 200, 300, 400, 500]:
            m = LatencyMeasurement()
            m.detected_at = 1000.0
            m.completed_at = 1000.0 + (total_ms / 1000)
            tracker._history.append(m)
            tracker._total_trades += 1

        p = tracker.get_percentiles()
        assert p["count"] == 5
        assert p["min_ms"] == pytest.approx(100, abs=1)
        assert p["max_ms"] == pytest.approx(500, abs=1)
        assert p["avg_ms"] == pytest.approx(300, abs=1)
        assert p["p50_ms"] is not None

    def test_p95_calculation(self, tracker):
        # 100 measurements: 1ms, 2ms, ..., 100ms
        for i in range(1, 101):
            m = LatencyMeasurement()
            m.detected_at = 1000.0
            m.completed_at = 1000.0 + (i / 1000)
            tracker._history.append(m)
            tracker._total_trades += 1

        p = tracker.get_percentiles()
        # P95 should be around 95ms
        assert p["p95_ms"] == pytest.approx(95.0, abs=2)


class TestTargetCompliance:
    def test_meets_target_no_data(self, tracker):
        assert tracker._meets_target() is None

    def test_meets_target_fast(self, tracker):
        for _ in range(10):
            m = LatencyMeasurement()
            m.detected_at = 1000.0
            m.completed_at = 1000.200  # 200ms — under 500ms target
            tracker._history.append(m)
            tracker._total_trades += 1

        assert tracker._meets_target() is True

    def test_fails_target_slow(self, tracker):
        for _ in range(10):
            m = LatencyMeasurement()
            m.detected_at = 1000.0
            m.completed_at = 1001.0  # 1000ms — over 500ms target
            tracker._history.append(m)
            tracker._total_trades += 1

        assert tracker._meets_target() is False


class TestGetRecent:
    def test_get_recent_empty(self, tracker):
        assert tracker.get_recent() == []

    def test_get_recent_limit(self, tracker):
        for i in range(10):
            m = tracker.start_measurement(f"t-{i}")
            tracker.complete_measurement(m)

        recent = tracker.get_recent(n=3)
        assert len(recent) == 3
        assert recent[-1]["trade_id"] == "t-9"


class TestStatus:
    def test_status_structure(self, tracker):
        status = tracker.get_status()
        assert "total_trades_measured" in status
        assert "percentiles" in status
        assert "target_ms" in status
        assert status["target_ms"] == 500
