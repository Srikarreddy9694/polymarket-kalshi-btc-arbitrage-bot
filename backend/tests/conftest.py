"""
Pytest configuration and shared fixtures.
"""

import pytest
from core.models import PolymarketData, KalshiMarket, KalshiData, ArbitrageCheck
from core.fee_engine import FeeEngine
from core.arbitrage import ArbitrageEngine
from config.settings import Settings


@pytest.fixture
def test_settings():
    """Settings optimized for testing — deterministic fee values."""
    return Settings(
        DRY_RUN=True,
        KALSHI_FEE_PER_CONTRACT=0.03,
        POLYMARKET_GAS_COST=0.002,
        SLIPPAGE_BUFFER=0.005,
        MIN_NET_MARGIN=0.02,
    )


@pytest.fixture
def fee_engine(test_settings):
    return FeeEngine(settings=test_settings)


@pytest.fixture
def arb_engine(fee_engine, test_settings):
    return ArbitrageEngine(fee_engine=fee_engine, settings=test_settings)


@pytest.fixture
def sample_poly_data():
    """Sample Polymarket data with strike at $96,000."""
    return PolymarketData(
        price_to_beat=96000.0,
        current_price=96050.0,
        prices={"Up": 0.55, "Down": 0.45},
        slug="bitcoin-up-or-down-february-16-3pm-et",
    )


@pytest.fixture
def sample_kalshi_data():
    """Sample Kalshi data with markets around $96,000."""
    return KalshiData(
        event_ticker="KXBTCD-26FEB1621",
        current_price=96050.0,
        markets=[
            KalshiMarket(strike=94000.0, yes_bid=90, yes_ask=92, no_bid=6, no_ask=8, subtitle="$94,000 or above"),
            KalshiMarket(strike=95000.0, yes_bid=75, yes_ask=78, no_bid=20, no_ask=22, subtitle="$95,000 or above"),
            KalshiMarket(strike=95500.0, yes_bid=65, yes_ask=68, no_bid=30, no_ask=32, subtitle="$95,500 or above"),
            KalshiMarket(strike=96000.0, yes_bid=50, yes_ask=53, no_bid=45, no_ask=47, subtitle="$96,000 or above"),
            KalshiMarket(strike=96500.0, yes_bid=35, yes_ask=38, no_bid=60, no_ask=62, subtitle="$96,500 or above"),
            KalshiMarket(strike=97000.0, yes_bid=20, yes_ask=23, no_bid=75, no_ask=77, subtitle="$97,000 or above"),
            KalshiMarket(strike=98000.0, yes_bid=8, yes_ask=10, no_bid=88, no_ask=90, subtitle="$98,000 or above"),
        ],
    )


@pytest.fixture
def arb_opportunity_poly_data():
    """Poly data designed to create an arbitrage opportunity when paired with cheap Kalshi."""
    return PolymarketData(
        price_to_beat=96000.0,
        current_price=96050.0,
        prices={"Up": 0.40, "Down": 0.35},
        slug="bitcoin-up-or-down-february-16-3pm-et",
    )


@pytest.fixture
def arb_opportunity_kalshi_data():
    """Kalshi data designed to create an arbitrage at $95,500 strike (poly > kalshi)."""
    return KalshiData(
        event_ticker="KXBTCD-26FEB1621",
        current_price=96050.0,
        markets=[
            KalshiMarket(strike=95500.0, yes_bid=53, yes_ask=55, no_bid=43, no_ask=45, subtitle="$95,500 or above"),
        ],
    )
