"""
Unit tests for PositionTracker.

Tests cover:
- Opening and closing positions
- Paired arbitrage tracking
- Exposure calculations per platform
- Summary statistics
"""

import pytest

from execution.position_tracker import (
    PositionTracker,
    Position,
    ArbitragePosition,
    Platform,
    PositionSide,
)


@pytest.fixture
def tracker():
    return PositionTracker()


class TestOpenClosePositions:
    def test_open_position(self, tracker):
        pos = tracker.open_position(
            platform=Platform.KALSHI,
            side=PositionSide.LONG,
            ticker="KXBTCD-96000",
            entry_price=0.45,
            size=10,
        )
        assert pos.id == "POS-000001"
        assert pos.platform == Platform.KALSHI
        assert pos.side == PositionSide.LONG
        assert pos.cost_usd == pytest.approx(4.5)
        assert tracker.get_open_position_count() == 1

    def test_close_position(self, tracker):
        pos = tracker.open_position(
            platform=Platform.POLYMARKET,
            side=PositionSide.SHORT,
            ticker="poly-down",
            entry_price=0.38,
            size=5,
        )
        closed = tracker.close_position(pos.id)
        assert closed is not None
        assert closed.id == pos.id
        assert tracker.get_open_position_count() == 0

    def test_close_nonexistent_position(self, tracker):
        result = tracker.close_position("POS-999999")
        assert result is None

    def test_multiple_positions(self, tracker):
        tracker.open_position(Platform.KALSHI, PositionSide.LONG, "A", 0.50, 10)
        tracker.open_position(Platform.POLYMARKET, PositionSide.SHORT, "B", 0.40, 10)
        tracker.open_position(Platform.KALSHI, PositionSide.LONG, "C", 0.60, 5)
        assert tracker.get_open_position_count() == 3

    def test_position_ids_increment(self, tracker):
        p1 = tracker.open_position(Platform.KALSHI, PositionSide.LONG, "A", 0.50, 1)
        p2 = tracker.open_position(Platform.KALSHI, PositionSide.LONG, "B", 0.50, 1)
        assert p1.id == "POS-000001"
        assert p2.id == "POS-000002"


class TestExposureCalculations:
    def test_total_exposure(self, tracker):
        tracker.open_position(Platform.KALSHI, PositionSide.LONG, "A", 0.50, 10)
        tracker.open_position(Platform.POLYMARKET, PositionSide.SHORT, "B", 0.30, 10)
        assert tracker.get_total_exposure() == pytest.approx(8.0)  # 5.0 + 3.0

    def test_platform_exposure(self, tracker):
        tracker.open_position(Platform.KALSHI, PositionSide.LONG, "A", 0.50, 10)
        tracker.open_position(Platform.KALSHI, PositionSide.LONG, "B", 0.30, 5)
        tracker.open_position(Platform.POLYMARKET, PositionSide.SHORT, "C", 0.40, 10)

        assert tracker.get_platform_exposure(Platform.KALSHI) == pytest.approx(6.5)  # 5.0 + 1.5
        assert tracker.get_platform_exposure(Platform.POLYMARKET) == pytest.approx(4.0)

    def test_zero_exposure_empty_tracker(self, tracker):
        assert tracker.get_total_exposure() == 0.0

    def test_exposure_after_close(self, tracker):
        pos = tracker.open_position(Platform.KALSHI, PositionSide.LONG, "A", 0.50, 10)
        assert tracker.get_total_exposure() == pytest.approx(5.0)
        tracker.close_position(pos.id)
        assert tracker.get_total_exposure() == 0.0


class TestArbitrageTracking:
    def test_open_arbitrage(self, tracker):
        kalshi = tracker.open_position(Platform.KALSHI, PositionSide.LONG, "K1", 0.45, 1)
        poly = tracker.open_position(Platform.POLYMARKET, PositionSide.SHORT, "P1", 0.40, 1)

        arb = tracker.open_arbitrage(kalshi, poly, expected_profit=0.12)
        assert arb.id == "ARB-000001"
        assert arb.total_cost == pytest.approx(0.85)
        assert arb.expected_profit == pytest.approx(0.12)
        assert arb.status == "open"
        assert tracker.get_open_arbitrage_count() == 1

    def test_settle_arbitrage(self, tracker):
        kalshi = tracker.open_position(Platform.KALSHI, PositionSide.LONG, "K1", 0.45, 1)
        poly = tracker.open_position(Platform.POLYMARKET, PositionSide.SHORT, "P1", 0.40, 1)
        arb = tracker.open_arbitrage(kalshi, poly, expected_profit=0.12)

        settled = tracker.settle_arbitrage(arb.id)
        assert settled is not None
        assert settled.status == "settled"
        assert settled.settled_at is not None
        # Both positions should be closed
        assert tracker.get_open_position_count() == 0
        assert tracker.get_open_arbitrage_count() == 0

    def test_settle_nonexistent_arb(self, tracker):
        result = tracker.settle_arbitrage("ARB-999999")
        assert result is None


class TestSummary:
    def test_summary_empty(self, tracker):
        summary = tracker.get_summary()
        assert summary["open_positions"] == 0
        assert summary["total_exposure_usd"] == 0.0
        assert summary["open_arbitrages"] == 0

    def test_summary_with_positions(self, tracker):
        k = tracker.open_position(Platform.KALSHI, PositionSide.LONG, "K1", 0.50, 4)
        p = tracker.open_position(Platform.POLYMARKET, PositionSide.SHORT, "P1", 0.40, 4)
        tracker.open_arbitrage(k, p, expected_profit=0.08)

        summary = tracker.get_summary()
        assert summary["open_positions"] == 2
        assert summary["kalshi_exposure_usd"] == 2.0
        assert summary["polymarket_exposure_usd"] == 1.6
        assert summary["total_exposure_usd"] == 3.6
        assert summary["open_arbitrages"] == 1
        assert summary["total_expected_profit"] == pytest.approx(0.08)

    def test_get_all_positions(self, tracker):
        tracker.open_position(Platform.KALSHI, PositionSide.LONG, "A", 0.50, 1)
        tracker.open_position(Platform.POLYMARKET, PositionSide.SHORT, "B", 0.40, 1)
        positions = tracker.get_all_positions()
        assert len(positions) == 2

    def test_get_all_arbitrages(self, tracker):
        k = tracker.open_position(Platform.KALSHI, PositionSide.LONG, "K", 0.50, 1)
        p = tracker.open_position(Platform.POLYMARKET, PositionSide.SHORT, "P", 0.40, 1)
        tracker.open_arbitrage(k, p, 0.05)
        arbs = tracker.get_all_arbitrages()
        assert len(arbs) == 1
