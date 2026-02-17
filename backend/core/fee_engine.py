"""
Fee calculation engine for cross-platform arbitrage.

Accounts for:
- Kalshi trading fees (winning trades only)
- Polymarket gas costs (on-chain settlement)
- Configurable slippage buffer
"""

from __future__ import annotations

from typing import Optional

from config.settings import Settings, get_settings


class FeeEngine:
    """Calculates real trading costs per platform."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()

    def kalshi_fee(self, is_winning: bool = True) -> float:
        """
        Kalshi fee on a single contract.
        - No fee on losing trades.
        - Winning trades: configurable fee per contract.
        """
        if not is_winning:
            return 0.0
        return self.settings.KALSHI_FEE_PER_CONTRACT

    def polymarket_fee(self) -> float:
        """
        Polymarket cost overhead per trade.
        - No explicit trading fee.
        - Gas cost for on-chain settlement (estimate).
        """
        return self.settings.POLYMARKET_GAS_COST

    def worst_case_fees(self) -> float:
        """
        Returns worst-case total fees for a dual-leg arbitrage trade.
        Assumes the winning platform charges fees.
        """
        return max(
            self.kalshi_fee(is_winning=True),
            self.polymarket_fee()
        ) + self.settings.SLIPPAGE_BUFFER

    def fee_adjusted_cost(self, raw_total_cost: float) -> float:
        """
        Returns the fee-adjusted total cost of a trade.
        If this is < 1.00, it's a real arbitrage after fees.
        """
        return raw_total_cost + self.worst_case_fees()

    def net_margin(self, raw_total_cost: float) -> float:
        """
        Returns net profit margin after fees and slippage.
        Positive = profitable, negative = loss.
        """
        return 1.00 - self.fee_adjusted_cost(raw_total_cost)

    def is_profitable(self, raw_total_cost: float) -> bool:
        """Returns True if the trade is profitable after all costs."""
        return self.net_margin(raw_total_cost) >= self.settings.MIN_NET_MARGIN
