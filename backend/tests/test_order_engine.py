"""
Unit tests for OrderEngine.

Tests cover:
- Pre-flight checks (all 5 conditions)
- Dry-run mode
- Execution flow with mocked clients
- Unwind logic
- Status reporting
"""

import pytest
from unittest.mock import patch, MagicMock

from execution.order_engine import OrderEngine, ExecutionStatus, ExecutionResult
from execution.position_tracker import PositionTracker
from clients.kalshi_auth_client import KalshiAuthClient
from clients.polymarket_exec_client import PolymarketExecClient
from core.models import ArbitrageCheck
from config.settings import Settings


@pytest.fixture
def mock_kalshi(test_settings):
    return KalshiAuthClient(api_key="test", private_key_path="", settings=test_settings)


@pytest.fixture
def mock_poly(test_settings):
    return PolymarketExecClient(private_key="0xtest", settings=test_settings)


@pytest.fixture
def engine(test_settings, mock_kalshi, mock_poly):
    """OrderEngine with test settings and mocked clients."""
    test_settings.DRY_RUN = True
    return OrderEngine(
        kalshi=mock_kalshi,
        poly=mock_poly,
        settings=test_settings,
    )


@pytest.fixture
def profitable_opportunity():
    """An arbitrage opportunity with good margin."""
    return ArbitrageCheck(
        kalshi_strike=96000.0,
        kalshi_yes=0.45,
        kalshi_no=0.55,
        type="Poly > Kalshi",
        poly_leg="Down",
        kalshi_leg="Yes",
        poly_cost=0.38,
        kalshi_cost=0.45,
        total_cost=0.83,
        fee_adjusted_cost=0.87,
        is_arbitrage=True,
        margin=0.17,
        net_margin=0.13,
    )


@pytest.fixture
def marginal_opportunity():
    """An arbitrage opportunity near the threshold."""
    return ArbitrageCheck(
        kalshi_strike=96500.0,
        kalshi_yes=0.52,
        kalshi_no=0.48,
        type="Poly < Kalshi",
        poly_leg="Up",
        kalshi_leg="No",
        poly_cost=0.47,
        kalshi_cost=0.48,
        total_cost=0.95,
        fee_adjusted_cost=0.985,
        is_arbitrage=True,
        margin=0.05,
        net_margin=0.015,  # Below default MIN_NET_MARGIN of 0.02
    )


class TestPreflightChecks:
    def test_profitable_passes_preflight(self, engine, profitable_opportunity):
        ok, err = engine._preflight_check(profitable_opportunity)
        assert ok is True
        assert err is None

    def test_below_min_margin_fails(self, engine, marginal_opportunity):
        ok, err = engine._preflight_check(marginal_opportunity)
        assert ok is False
        assert "margin" in err.lower()

    def test_rate_limit_fails(self, engine, profitable_opportunity):
        engine._trade_count_this_hour = engine.settings.MAX_TRADES_PER_HOUR
        ok, err = engine._preflight_check(profitable_opportunity)
        assert ok is False
        assert "rate limit" in err.lower()

    def test_exposure_limit_fails(self, engine, profitable_opportunity):
        # Add enough positions to exceed exposure limit
        tracker = engine.tracker
        from execution.position_tracker import Platform, PositionSide
        for i in range(100):
            tracker.open_position(Platform.KALSHI, PositionSide.LONG, f"T{i}", 5.0, 1)
        # Now exposure = 500, which is at the limit
        ok, err = engine._preflight_check(profitable_opportunity)
        assert ok is False
        assert "exposure" in err.lower()

    def test_daily_loss_limit_fails(self, engine, profitable_opportunity):
        engine._daily_loss = engine.settings.MAX_DAILY_LOSS_USD
        ok, err = engine._preflight_check(profitable_opportunity)
        assert ok is False
        assert "daily loss" in err.lower()


class TestDryRunExecution:
    def test_dry_run_returns_dry_run_status(self, engine, profitable_opportunity):
        result = engine.execute_arbitrage(profitable_opportunity)
        assert result.status == ExecutionStatus.DRY_RUN
        assert result.opportunity == profitable_opportunity
        assert result.error is None

    def test_dry_run_does_not_increment_counter(self, engine, profitable_opportunity):
        engine.execute_arbitrage(profitable_opportunity)
        assert engine._trade_count_this_hour == 0

    def test_preflight_failure_before_dry_run(self, engine, marginal_opportunity):
        result = engine.execute_arbitrage(marginal_opportunity)
        assert result.status == ExecutionStatus.PREFLIGHT_FAILED
        assert "margin" in result.error.lower()


class TestLiveExecution:
    def test_leg1_failure_aborts(self, engine, profitable_opportunity):
        engine.settings.DRY_RUN = False

        with patch.object(engine.kalshi, 'place_order', return_value=(None, "Connection refused")):
            result = engine.execute_arbitrage(profitable_opportunity)
            assert result.status == ExecutionStatus.LEG1_FAILED
            assert "Kalshi leg failed" in result.error

    def test_leg2_failure_attempts_unwind(self, engine, profitable_opportunity):
        engine.settings.DRY_RUN = False

        kalshi_result = {"order": {"order_id": "ord-123", "status": "filled"}}
        with patch.object(engine.kalshi, 'place_order', return_value=(kalshi_result, None)):
            with patch.object(engine.poly, 'place_order', return_value=(None, "Gas too high")):
                with patch.object(engine.kalshi, 'cancel_order', return_value=({"status": "cancelled"}, None)):
                    result = engine.execute_arbitrage(profitable_opportunity)
                    assert result.status == ExecutionStatus.UNWOUND
                    assert "Poly leg failed" in result.error
                    assert "Unwind: success" in result.error

    def test_both_legs_success(self, engine, profitable_opportunity):
        engine.settings.DRY_RUN = False

        kalshi_result = {"order": {"order_id": "ord-456", "status": "filled"}}
        poly_result = {"orderID": "poly-789", "status": "filled"}

        with patch.object(engine.kalshi, 'place_order', return_value=(kalshi_result, None)):
            with patch.object(engine.poly, 'place_order', return_value=(poly_result, None)):
                result = engine.execute_arbitrage(profitable_opportunity)
                assert result.status == ExecutionStatus.SUCCESS
                assert result.position_id is not None
                assert result.position_id.startswith("ARB-")
                assert engine._trade_count_this_hour == 1

    def test_successful_trade_records_positions(self, engine, profitable_opportunity):
        engine.settings.DRY_RUN = False

        kalshi_result = {"order": {"order_id": "ord-100", "status": "filled"}}
        poly_result = {"orderID": "poly-200", "status": "filled"}

        with patch.object(engine.kalshi, 'place_order', return_value=(kalshi_result, None)):
            with patch.object(engine.poly, 'place_order', return_value=(poly_result, None)):
                result = engine.execute_arbitrage(profitable_opportunity)
                assert engine.tracker.get_open_position_count() == 2
                assert engine.tracker.get_open_arbitrage_count() == 1


class TestHousekeeping:
    def test_reset_hourly_counter(self, engine):
        engine._trade_count_this_hour = 15
        engine.reset_hourly_counter()
        assert engine._trade_count_this_hour == 0

    def test_reset_daily_loss(self, engine):
        engine._daily_loss = 42.50
        engine.reset_daily_loss()
        assert engine._daily_loss == 0.0

    def test_get_status(self, engine):
        status = engine.get_status()
        assert "dry_run" in status
        assert "trades_this_hour" in status
        assert "positions" in status
        assert status["dry_run"] is True
