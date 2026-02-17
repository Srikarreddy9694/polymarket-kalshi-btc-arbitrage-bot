"""
Unit tests for RiskManager.

Tests cover:
- All 6 risk gates
- Trade recording and PnL tracking
- Halt/resume functionality
- Daily reset
- Status reporting (no secrets leakage)
"""

import time
import pytest

from safety.risk_manager import RiskManager
from config.settings import Settings


@pytest.fixture
def rm(test_settings):
    """RiskManager with test settings."""
    return RiskManager(settings=test_settings)


class TestRiskGates:
    def test_valid_trade_passes_all_gates(self, rm):
        ok, reason = rm.check_trade_allowed(net_margin=0.10, trade_cost_usd=10.0)
        assert ok is True
        assert reason == "approved"

    def test_gate0_halt_blocks_trade(self, rm):
        rm.halt("test halt")
        ok, reason = rm.check_trade_allowed(net_margin=0.10, trade_cost_usd=10.0)
        assert ok is False
        assert "halted" in reason.lower()

    def test_gate1_min_margin(self, rm):
        ok, reason = rm.check_trade_allowed(net_margin=0.001, trade_cost_usd=10.0)
        assert ok is False
        assert "margin" in reason.lower()

    def test_gate1_exact_threshold_passes(self, rm):
        ok, _ = rm.check_trade_allowed(
            net_margin=rm.settings.MIN_NET_MARGIN,
            trade_cost_usd=10.0,
        )
        assert ok is True

    def test_gate2_single_trade_limit(self, rm):
        ok, reason = rm.check_trade_allowed(
            net_margin=0.10,
            trade_cost_usd=rm.settings.MAX_SINGLE_TRADE_USD + 1,
        )
        assert ok is False
        assert "trade" in reason.lower()

    def test_gate3_exposure_limit(self, rm):
        ok, reason = rm.check_trade_allowed(
            net_margin=0.10,
            trade_cost_usd=10.0,
            current_exposure=rm.settings.MAX_TOTAL_EXPOSURE_USD,
        )
        assert ok is False
        assert "exposure" in reason.lower()

    def test_gate4_daily_loss_limit(self, rm):
        rm._daily_pnl = -rm.settings.MAX_DAILY_LOSS_USD
        ok, reason = rm.check_trade_allowed(net_margin=0.10, trade_cost_usd=10.0)
        assert ok is False
        assert "daily loss" in reason.lower()

    def test_gate5_rate_limit(self, rm):
        # Fill up the rate limit
        now = time.time()
        for i in range(rm.settings.MAX_TRADES_PER_HOUR):
            rm._trade_timestamps.append(now - i)

        ok, reason = rm.check_trade_allowed(net_margin=0.10, trade_cost_usd=10.0)
        assert ok is False
        assert "rate limit" in reason.lower()

    def test_rate_limit_expires_after_1_hour(self, rm):
        # Add timestamps from 2 hours ago (should be cleaned)
        old_time = time.time() - 7200
        for i in range(rm.settings.MAX_TRADES_PER_HOUR):
            rm._trade_timestamps.append(old_time)

        ok, _ = rm.check_trade_allowed(net_margin=0.10, trade_cost_usd=10.0)
        assert ok is True


class TestTradeRecording:
    def test_record_trade_updates_pnl(self, rm):
        rm.record_trade(pnl=0.05, cost_usd=10.0)
        assert rm.get_daily_pnl() == pytest.approx(0.05)

    def test_record_trade_updates_exposure(self, rm):
        rm.record_trade(pnl=0.05, cost_usd=10.0)
        assert rm.get_total_exposure() == pytest.approx(10.0)

    def test_record_trade_increments_rate(self, rm):
        rm.record_trade(pnl=0.05, cost_usd=10.0)
        assert rm.get_trades_this_hour() == 1

    def test_close_position_reduces_exposure(self, rm):
        rm.record_trade(pnl=0.05, cost_usd=10.0)
        rm.close_position(cost_usd=10.0)
        assert rm.get_total_exposure() == 0.0

    def test_close_position_doesnt_go_negative(self, rm):
        rm.close_position(cost_usd=100.0)
        assert rm.get_total_exposure() == 0.0


class TestHaltResume:
    def test_halt(self, rm):
        rm.halt("circuit breaker")
        assert rm.is_halted is True
        assert rm.halt_reason == "circuit breaker"

    def test_resume(self, rm):
        rm.halt("test")
        rm.resume("all clear")
        assert rm.is_halted is False
        assert rm.halt_reason == ""


class TestResets:
    def test_reset_daily(self, rm):
        rm.record_trade(pnl=-5.0, cost_usd=10.0)
        rm.reset_daily()
        assert rm.get_daily_pnl() == 0.0
        assert rm._trade_count_today == 0


class TestStatus:
    def test_get_status_structure(self, rm):
        status = rm.get_status()
        assert "is_halted" in status
        assert "daily_pnl" in status
        assert "limits" in status
        assert "max_single_trade" in status["limits"]

    def test_status_excludes_secrets(self, rm):
        """SECURITY: status must never contain API keys, tokens, or paths."""
        status = rm.get_status()
        status_str = str(status).lower()
        assert "api_key" not in status_str
        assert "private_key" not in status_str
        assert "token" not in status_str
        assert "password" not in status_str
