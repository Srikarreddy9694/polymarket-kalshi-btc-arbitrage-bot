"""
Unit tests for the ArbitrageEngine.
"""

import pytest
from core.models import PolymarketData, KalshiMarket, KalshiData
from core.arbitrage import ArbitrageEngine
from core.fee_engine import FeeEngine
from config.settings import Settings


class TestArbitrageDetection:
    """Tests for basic arbitrage detection logic."""

    def test_poly_greater_than_kalshi_selects_down_and_yes(self, arb_engine, sample_poly_data, sample_kalshi_data):
        """When Poly strike > Kalshi strike, strategy should be Poly Down + Kalshi Yes."""
        checks, _ = arb_engine.find_opportunities(sample_poly_data, sample_kalshi_data)

        # Find check for strike $95,500 (Poly $96k > Kalshi $95.5k)
        check = next(c for c in checks if c.kalshi_strike == 95500.0)
        assert check.poly_leg == "Down"
        assert check.kalshi_leg == "Yes"
        assert check.type == "Poly > Kalshi"

    def test_poly_less_than_kalshi_selects_up_and_no(self, arb_engine, sample_poly_data, sample_kalshi_data):
        """When Poly strike < Kalshi strike, strategy should be Poly Up + Kalshi No."""
        checks, _ = arb_engine.find_opportunities(sample_poly_data, sample_kalshi_data)

        # Find check for strike $96,500 (Poly $96k < Kalshi $96.5k)
        check = next(c for c in checks if c.kalshi_strike == 96500.0)
        assert check.poly_leg == "Up"
        assert check.kalshi_leg == "No"
        assert check.type == "Poly < Kalshi"

    def test_equal_strikes_checks_both_strategies(self, arb_engine, sample_poly_data, sample_kalshi_data):
        """When strikes are equal, both strategies should be checked."""
        checks, _ = arb_engine.find_opportunities(sample_poly_data, sample_kalshi_data)

        # Find checks for strike $96,000 (equal to Poly)
        equal_checks = [c for c in checks if c.kalshi_strike == 96000.0]
        assert len(equal_checks) == 2

        legs = {(c.poly_leg, c.kalshi_leg) for c in equal_checks}
        assert ("Down", "Yes") in legs
        assert ("Up", "No") in legs

    def test_no_opportunities_when_markets_expensive(self, arb_engine, sample_poly_data, sample_kalshi_data):
        """With realistic prices, most strategy pairs should NOT be arbitrage."""
        _, opportunities = arb_engine.find_opportunities(sample_poly_data, sample_kalshi_data)

        # With Up=0.55, Down=0.45 and typical Kalshi prices, no arb should exist
        # because total costs will be > $1.00 after fees
        assert len(opportunities) == 0

    def test_arbitrage_detected_when_total_under_threshold(self, arb_engine, arb_opportunity_poly_data, arb_opportunity_kalshi_data):
        """Detects arbitrage when total cost is well under $1.00 even after fees."""
        _, opportunities = arb_engine.find_opportunities(
            arb_opportunity_poly_data, arb_opportunity_kalshi_data
        )

        assert len(opportunities) >= 1
        opp = opportunities[0]
        assert opp.is_arbitrage is True
        assert opp.net_margin > 0
        assert opp.total_cost < 1.00
        assert opp.fee_adjusted_cost < 1.00

    def test_none_strike_returns_empty(self, arb_engine, sample_kalshi_data):
        """If Polymarket strike is None, return empty results."""
        poly = PolymarketData(
            price_to_beat=None,
            current_price=96000.0,
            prices={"Up": 0.55, "Down": 0.45},
            slug="test",
        )
        checks, opps = arb_engine.find_opportunities(poly, sample_kalshi_data)
        assert checks == []
        assert opps == []

    def test_empty_kalshi_markets_returns_empty(self, arb_engine, sample_poly_data):
        """If no Kalshi markets, return empty results."""
        kalshi = KalshiData(event_ticker="TEST", current_price=96000.0, markets=[])
        checks, opps = arb_engine.find_opportunities(sample_poly_data, kalshi)
        assert checks == []
        assert opps == []


class TestMarketSelection:
    """Tests for the market selection (nearby markets) logic."""

    def test_selects_nearby_markets(self, arb_engine, sample_poly_data, sample_kalshi_data):
        """Should select markets within ±4 of the closest strike."""
        checks, _ = arb_engine.find_opportunities(sample_poly_data, sample_kalshi_data)
        # With 7 markets and poly_strike at $96k, all 7 should be within range
        assert len(checks) >= 5  # At least 5 checks (equal generates 2)

    def test_single_market_works(self, arb_engine, sample_poly_data):
        """Engine should work with just a single Kalshi market."""
        kalshi = KalshiData(
            event_ticker="TEST",
            current_price=96000.0,
            markets=[
                KalshiMarket(strike=95000.0, yes_bid=75, yes_ask=78, no_bid=20, no_ask=22, subtitle="$95,000 or above"),
            ],
        )
        checks, _ = arb_engine.find_opportunities(sample_poly_data, kalshi)
        assert len(checks) == 1


class TestCostCalculation:
    """Tests for cost and margin calculations."""

    def test_total_cost_is_sum_of_legs(self, arb_engine, sample_poly_data, sample_kalshi_data):
        """Total cost should equal poly_cost + kalshi_cost."""
        checks, _ = arb_engine.find_opportunities(sample_poly_data, sample_kalshi_data)
        for check in checks:
            assert abs(check.total_cost - (check.poly_cost + check.kalshi_cost)) < 1e-10

    def test_raw_margin_equals_one_minus_total(self, arb_engine, sample_poly_data, sample_kalshi_data):
        """Raw margin should equal 1.00 - total_cost."""
        checks, _ = arb_engine.find_opportunities(sample_poly_data, sample_kalshi_data)
        for check in checks:
            assert abs(check.margin - (1.00 - check.total_cost)) < 1e-10

    def test_fee_adjusted_cost_greater_than_raw(self, arb_engine, sample_poly_data, sample_kalshi_data):
        """Fee-adjusted cost should always be greater than raw total cost."""
        checks, _ = arb_engine.find_opportunities(sample_poly_data, sample_kalshi_data)
        for check in checks:
            assert check.fee_adjusted_cost > check.total_cost

    def test_net_margin_less_than_raw_margin(self, arb_engine, sample_poly_data, sample_kalshi_data):
        """Net margin should always be less than raw margin (fees reduce profit)."""
        checks, _ = arb_engine.find_opportunities(sample_poly_data, sample_kalshi_data)
        for check in checks:
            assert check.net_margin < check.margin

    def test_exact_one_dollar_cost_is_not_arbitrage(self, arb_engine):
        """Total cost of exactly $1.00 should NOT be flagged as arbitrage."""
        poly = PolymarketData(
            price_to_beat=96000.0,
            current_price=96000.0,
            prices={"Up": 0.50, "Down": 0.50},
            slug="test",
        )
        kalshi = KalshiData(
            event_ticker="TEST",
            current_price=96000.0,
            markets=[
                KalshiMarket(strike=95000.0, yes_bid=48, yes_ask=50, no_bid=48, no_ask=50, subtitle="$95,000 or above"),
            ],
        )
        _, opps = arb_engine.find_opportunities(poly, kalshi)
        assert len(opps) == 0
