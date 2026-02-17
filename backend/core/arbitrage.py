"""
Arbitrage detection engine.

Scans Polymarket and Kalshi markets to find fee-adjusted
arbitrage opportunities in Bitcoin 1-Hour binary options.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from core.models import PolymarketData, KalshiData, KalshiMarket, ArbitrageCheck
from core.fee_engine import FeeEngine
from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class ArbitrageEngine:
    """Core engine for detecting cross-platform arbitrage."""

    def __init__(
        self,
        fee_engine: Optional[FeeEngine] = None,
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or get_settings()
        self.fee_engine = fee_engine or FeeEngine(self.settings)

    def find_opportunities(
        self, poly_data: PolymarketData, kalshi_data: KalshiData
    ) -> Tuple[List[ArbitrageCheck], List[ArbitrageCheck]]:
        """
        Scans all strategy pairs and returns (all_checks, opportunities).

        Returns:
            all_checks: Every strategy pair evaluated
            opportunities: Only fee-adjusted profitable pairs
        """
        poly_strike = poly_data.price_to_beat
        if poly_strike is None:
            logger.warning("Polymarket strike price is None, skipping scan")
            return [], []

        poly_up_cost = poly_data.prices.get("Up", 0.0)
        poly_down_cost = poly_data.prices.get("Down", 0.0)

        # Select markets around the Polymarket strike
        kalshi_markets = sorted(kalshi_data.markets, key=lambda x: x.strike)
        selected = self._select_nearby_markets(kalshi_markets, poly_strike, radius=4)

        all_checks: List[ArbitrageCheck] = []
        opportunities: List[ArbitrageCheck] = []

        for km in selected:
            kalshi_strike = km.strike
            kalshi_yes_cost = km.yes_ask / 100.0
            kalshi_no_cost = km.no_ask / 100.0

            if poly_strike > kalshi_strike:
                checks = [self._build_check(
                    kalshi_strike, kalshi_yes_cost, kalshi_no_cost,
                    type_str="Poly > Kalshi",
                    poly_leg="Down", kalshi_leg="Yes",
                    poly_cost=poly_down_cost, kalshi_cost=kalshi_yes_cost,
                )]
            elif poly_strike < kalshi_strike:
                checks = [self._build_check(
                    kalshi_strike, kalshi_yes_cost, kalshi_no_cost,
                    type_str="Poly < Kalshi",
                    poly_leg="Up", kalshi_leg="No",
                    poly_cost=poly_up_cost, kalshi_cost=kalshi_no_cost,
                )]
            else:
                # Equal strikes — check both strategies
                checks = [
                    self._build_check(
                        kalshi_strike, kalshi_yes_cost, kalshi_no_cost,
                        type_str="Equal",
                        poly_leg="Down", kalshi_leg="Yes",
                        poly_cost=poly_down_cost, kalshi_cost=kalshi_yes_cost,
                    ),
                    self._build_check(
                        kalshi_strike, kalshi_yes_cost, kalshi_no_cost,
                        type_str="Equal",
                        poly_leg="Up", kalshi_leg="No",
                        poly_cost=poly_up_cost, kalshi_cost=kalshi_no_cost,
                    ),
                ]

            for check in checks:
                all_checks.append(check)
                if check.is_arbitrage:
                    opportunities.append(check)
                    logger.info(
                        "Arbitrage found: %s | Net margin: $%.4f | Total cost: $%.4f",
                        check.type, check.net_margin, check.fee_adjusted_cost,
                    )

        return all_checks, opportunities

    def _build_check(
        self,
        kalshi_strike: float,
        kalshi_yes_cost: float,
        kalshi_no_cost: float,
        type_str: str,
        poly_leg: str,
        kalshi_leg: str,
        poly_cost: float,
        kalshi_cost: float,
    ) -> ArbitrageCheck:
        """Builds a single ArbitrageCheck with fee calculations."""
        raw_total = poly_cost + kalshi_cost
        fee_adjusted = self.fee_engine.fee_adjusted_cost(raw_total)
        raw_margin = 1.00 - raw_total
        net = self.fee_engine.net_margin(raw_total)
        is_arb = self.fee_engine.is_profitable(raw_total)

        return ArbitrageCheck(
            kalshi_strike=kalshi_strike,
            kalshi_yes=kalshi_yes_cost,
            kalshi_no=kalshi_no_cost,
            type=type_str,
            poly_leg=poly_leg,
            kalshi_leg=kalshi_leg,
            poly_cost=poly_cost,
            kalshi_cost=kalshi_cost,
            total_cost=raw_total,
            fee_adjusted_cost=fee_adjusted,
            is_arbitrage=is_arb,
            margin=raw_margin,
            net_margin=net,
        )

    @staticmethod
    def _select_nearby_markets(
        sorted_markets: List[KalshiMarket], poly_strike: float, radius: int = 4
    ) -> List[KalshiMarket]:
        """Selects markets within ±radius of the closest to poly_strike."""
        if not sorted_markets:
            return []

        closest_idx = 0
        min_diff = float("inf")
        for i, m in enumerate(sorted_markets):
            diff = abs(m.strike - poly_strike)
            if diff < min_diff:
                min_diff = diff
                closest_idx = i

        start = max(0, closest_idx - radius)
        end = min(len(sorted_markets), closest_idx + radius + 1)
        return sorted_markets[start:end]
