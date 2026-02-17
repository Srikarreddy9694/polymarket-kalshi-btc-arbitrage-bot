"""
Position Tracker — tracks open positions across both platforms.

Maintains an in-memory ledger of all open positions with methods to
calculate net exposure, record new positions, and close positions.
This will be backed by SQLite in Sprint 4.
"""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class Platform(str, Enum):
    KALSHI = "kalshi"
    POLYMARKET = "polymarket"


class PositionSide(str, Enum):
    LONG = "long"   # Bought a contract (yes/up)
    SHORT = "short"  # Bought opposite (no/down)


class Position(BaseModel):
    """A single open position on one platform."""
    id: str = Field(..., description="Unique position identifier")
    platform: Platform
    side: PositionSide
    ticker: str = Field("", description="Market ticker or token ID")
    entry_price: float = Field(0.0, description="Price paid per contract")
    size: int = Field(0, description="Number of contracts")
    cost_usd: float = Field(0.0, description="Total cost (entry_price * size)")
    opened_at: datetime = Field(default_factory=datetime.utcnow)
    linked_position_id: Optional[str] = Field(None, description="ID of the paired position on other platform")


class ArbitragePosition(BaseModel):
    """A paired position across both platforms (the actual arbitrage)."""
    id: str
    kalshi_position: Optional[Position] = None
    poly_position: Optional[Position] = None
    total_cost: float = Field(0.0, description="Combined cost of both legs")
    expected_payout: float = Field(1.0, description="Expected payout ($1.00 per contract)")
    expected_profit: float = Field(0.0, description="Expected profit after costs")
    status: str = Field("open", description="open, settled, failed, unwound")
    opened_at: datetime = Field(default_factory=datetime.utcnow)
    settled_at: Optional[datetime] = None


class PositionTracker:
    """
    Tracks open positions across Kalshi and Polymarket.

    Provides:
    - Net exposure calculation
    - Position recording and closing
    - Linked arbitrage position pairing
    - Summary statistics

    NOTE: Currently in-memory only. Sprint 4 adds SQLite persistence.
    """

    def __init__(self):
        self._positions: Dict[str, Position] = {}
        self._arb_positions: Dict[str, ArbitragePosition] = {}
        self._position_counter: int = 0
        self._arb_counter: int = 0
        logger.info("PositionTracker initialized (in-memory)")

    # ── Position Management ──────────────────────────────

    def open_position(
        self,
        platform: Platform,
        side: PositionSide,
        ticker: str,
        entry_price: float,
        size: int,
        linked_position_id: Optional[str] = None,
    ) -> Position:
        """Record a new open position."""
        self._position_counter += 1
        pos_id = f"POS-{self._position_counter:06d}"

        position = Position(
            id=pos_id,
            platform=platform,
            side=side,
            ticker=ticker,
            entry_price=entry_price,
            size=size,
            cost_usd=round(entry_price * size, 6),
            linked_position_id=linked_position_id,
        )
        self._positions[pos_id] = position

        logger.info(
            "📊 Position opened: %s | %s %s | %s @ $%.3f x %d = $%.2f",
            pos_id, platform.value, side.value, ticker[:20], entry_price, size, position.cost_usd,
        )
        return position

    def close_position(self, position_id: str, reason: str = "settled") -> Optional[Position]:
        """Remove a position from tracking."""
        pos = self._positions.pop(position_id, None)
        if pos:
            logger.info("📊 Position closed: %s | reason=%s", position_id, reason)
        else:
            logger.warning("Position %s not found for closing", position_id)
        return pos

    # ── Arbitrage Pair Management ────────────────────────

    def open_arbitrage(
        self,
        kalshi_position: Position,
        poly_position: Position,
        expected_profit: float,
    ) -> ArbitragePosition:
        """Record a paired arbitrage position across both platforms."""
        self._arb_counter += 1
        arb_id = f"ARB-{self._arb_counter:06d}"

        total_cost = kalshi_position.cost_usd + poly_position.cost_usd
        arb = ArbitragePosition(
            id=arb_id,
            kalshi_position=kalshi_position,
            poly_position=poly_position,
            total_cost=round(total_cost, 6),
            expected_profit=round(expected_profit, 6),
            status="open",
        )
        self._arb_positions[arb_id] = arb

        logger.info(
            "🔗 Arbitrage opened: %s | cost=$%.4f profit=$%.4f",
            arb_id, total_cost, expected_profit,
        )
        return arb

    def settle_arbitrage(self, arb_id: str, actual_pnl: Optional[float] = None) -> Optional[ArbitragePosition]:
        """Mark an arbitrage as settled."""
        arb = self._arb_positions.get(arb_id)
        if not arb:
            logger.warning("Arbitrage %s not found", arb_id)
            return None

        arb.status = "settled"
        arb.settled_at = datetime.utcnow()

        if arb.kalshi_position:
            self.close_position(arb.kalshi_position.id, reason="arb_settled")
        if arb.poly_position:
            self.close_position(arb.poly_position.id, reason="arb_settled")

        logger.info(
            "✅ Arbitrage settled: %s | P&L=$%.4f",
            arb_id, actual_pnl or arb.expected_profit,
        )
        return arb

    # ── Exposure Calculations ────────────────────────────

    def get_total_exposure(self) -> float:
        """Total USD currently at risk across all open positions."""
        return sum(p.cost_usd for p in self._positions.values())

    def get_platform_exposure(self, platform: Platform) -> float:
        """USD at risk on a specific platform."""
        return sum(
            p.cost_usd for p in self._positions.values()
            if p.platform == platform
        )

    def get_open_position_count(self) -> int:
        """Number of open individual positions."""
        return len(self._positions)

    def get_open_arbitrage_count(self) -> int:
        """Number of open arbitrage pairs."""
        return sum(1 for a in self._arb_positions.values() if a.status == "open")

    # ── Summary ──────────────────────────────────────────

    def get_summary(self) -> Dict:
        """Summary statistics for monitoring."""
        open_arbs = [a for a in self._arb_positions.values() if a.status == "open"]
        settled_arbs = [a for a in self._arb_positions.values() if a.status == "settled"]

        return {
            "open_positions": self.get_open_position_count(),
            "total_exposure_usd": round(self.get_total_exposure(), 2),
            "kalshi_exposure_usd": round(self.get_platform_exposure(Platform.KALSHI), 2),
            "polymarket_exposure_usd": round(self.get_platform_exposure(Platform.POLYMARKET), 2),
            "open_arbitrages": len(open_arbs),
            "settled_arbitrages": len(settled_arbs),
            "total_expected_profit": round(
                sum(a.expected_profit for a in open_arbs), 4
            ),
        }

    def get_all_positions(self) -> List[Position]:
        """All open positions."""
        return list(self._positions.values())

    def get_all_arbitrages(self) -> List[ArbitragePosition]:
        """All arbitrage pairs (open and settled)."""
        return list(self._arb_positions.values())
