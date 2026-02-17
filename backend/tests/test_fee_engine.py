"""
Unit tests for the FeeEngine.
"""

import pytest
from core.fee_engine import FeeEngine
from config.settings import Settings


class TestKalshiFees:
    """Tests for Kalshi fee calculations."""

    def test_winning_trade_has_fee(self, fee_engine):
        fee = fee_engine.kalshi_fee(is_winning=True)
        assert fee == 0.03

    def test_losing_trade_has_no_fee(self, fee_engine):
        fee = fee_engine.kalshi_fee(is_winning=False)
        assert fee == 0.0


class TestPolymarketFees:
    """Tests for Polymarket gas cost calculations."""

    def test_gas_cost_positive(self, fee_engine):
        fee = fee_engine.polymarket_fee()
        assert fee > 0
        assert fee == 0.002


class TestWorstCaseFees:
    """Tests for worst-case fee calculation."""

    def test_worst_case_includes_slippage(self, fee_engine):
        wc = fee_engine.worst_case_fees()
        # Should be max(kalshi=0.03, poly=0.002) + slippage(0.005) = 0.035
        assert abs(wc - 0.035) < 1e-10

    def test_custom_settings(self):
        settings = Settings(
            KALSHI_FEE_PER_CONTRACT=0.05,
            POLYMARKET_GAS_COST=0.01,
            SLIPPAGE_BUFFER=0.01,
        )
        engine = FeeEngine(settings=settings)
        wc = engine.worst_case_fees()
        # max(0.05, 0.01) + 0.01 = 0.06
        assert abs(wc - 0.06) < 1e-10


class TestFeeAdjustedCost:
    """Tests for fee-adjusted cost and profitability."""

    def test_fee_adjusted_higher_than_raw(self, fee_engine):
        raw = 0.90
        adjusted = fee_engine.fee_adjusted_cost(raw)
        assert adjusted > raw

    def test_fee_adjusted_correct_value(self, fee_engine):
        raw = 0.90
        adjusted = fee_engine.fee_adjusted_cost(raw)
        # 0.90 + 0.035 = 0.935
        assert abs(adjusted - 0.935) < 1e-10

    def test_net_margin_positive_when_cheap(self, fee_engine):
        raw = 0.90
        margin = fee_engine.net_margin(raw)
        # 1.00 - 0.935 = 0.065
        assert abs(margin - 0.065) < 1e-10
        assert margin > 0

    def test_net_margin_negative_when_expensive(self, fee_engine):
        raw = 0.99
        margin = fee_engine.net_margin(raw)
        assert margin < 0

    def test_is_profitable_above_threshold(self, fee_engine):
        # Threshold is 0.02 in test settings
        # Need net margin >= 0.02
        # net_margin = 1.00 - raw - 0.035 >= 0.02
        # raw <= 0.945
        assert fee_engine.is_profitable(0.90) is True  # margin = 0.065
        assert fee_engine.is_profitable(0.945) is True  # margin = 0.02

    def test_is_not_profitable_below_threshold(self, fee_engine):
        assert fee_engine.is_profitable(0.95) is False  # margin = 0.015
        assert fee_engine.is_profitable(0.99) is False  # margin = -0.025

    def test_exact_threshold_is_profitable(self, fee_engine):
        # 1.00 - raw - 0.035 = 0.02 → raw = 0.945
        assert fee_engine.is_profitable(0.945) is True

    def test_just_below_threshold_is_not_profitable(self, fee_engine):
        # 1.00 - 0.946 - 0.035 = 0.019 < 0.02
        assert fee_engine.is_profitable(0.946) is False
