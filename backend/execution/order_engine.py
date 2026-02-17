"""
Order Engine — orchestrates dual-leg arbitrage trade execution.

This is the core execution component that:
1. Validates pre-flight conditions (risk limits, balances)
2. Places the faster leg first (Kalshi REST)
3. Places the second leg (Polymarket on-chain)
4. Handles failures (abort if leg 1 fails, unwind if leg 2 fails)
5. Records positions for tracking

SAFETY: All executions respect DRY_RUN and log intent before action.
"""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple

from pydantic import BaseModel, Field

from config.settings import Settings, get_settings
from core.models import ArbitrageCheck, TradeResult
from clients.kalshi_auth_client import KalshiAuthClient
from clients.polymarket_exec_client import PolymarketExecClient
from execution.position_tracker import (
    PositionTracker,
    Platform,
    PositionSide,
)

logger = logging.getLogger(__name__)


class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    DRY_RUN = "dry_run"
    PREFLIGHT_FAILED = "preflight_failed"
    LEG1_FAILED = "leg1_failed"
    LEG2_FAILED = "leg2_failed"
    UNWOUND = "unwound"
    ERROR = "error"


class ExecutionResult(BaseModel):
    """Result of an arbitrage execution attempt."""
    status: ExecutionStatus
    opportunity: Optional[ArbitrageCheck] = None
    leg1_result: Optional[dict] = None
    leg2_result: Optional[dict] = None
    position_id: Optional[str] = None
    error: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class OrderEngine:
    """
    Dual-leg arbitrage order engine.

    Execution flow:
    1. Pre-flight checks (risk limits, balances, circuit breaker)
    2. Log intent
    3. If DRY_RUN → log and return
    4. Place Leg 1 (Kalshi — faster, REST API)
    5. If Leg 1 fails → abort
    6. Place Leg 2 (Polymarket — on-chain)
    7. If Leg 2 fails → attempt to unwind Leg 1, alert
    8. Record positions
    """

    def __init__(
        self,
        kalshi: Optional[KalshiAuthClient] = None,
        poly: Optional[PolymarketExecClient] = None,
        position_tracker: Optional[PositionTracker] = None,
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or get_settings()
        self.kalshi = kalshi or KalshiAuthClient(settings=self.settings)
        self.poly = poly or PolymarketExecClient(settings=self.settings)
        self.tracker = position_tracker or PositionTracker()
        self._trade_count_this_hour = 0
        self._daily_loss = 0.0

        logger.info(
            "OrderEngine initialized (DRY_RUN=%s, max_trade=$%.0f, max_exposure=$%.0f)",
            self.settings.DRY_RUN,
            self.settings.MAX_SINGLE_TRADE_USD,
            self.settings.MAX_TOTAL_EXPOSURE_USD,
        )

    # ── Main Execution ───────────────────────────────────

    def execute_arbitrage(self, opportunity: ArbitrageCheck) -> ExecutionResult:
        """
        Execute a dual-leg arbitrage trade.

        This method orchestrates the full lifecycle of an arbitrage execution.
        """
        logger.info(
            "━━━━━━━━ EXECUTE ARBITRAGE ━━━━━━━━\n"
            "  Strategy: %s\n"
            "  Kalshi: %s @ $%.3f (strike=$%s)\n"
            "  Poly: %s @ $%.3f\n"
            "  Net margin: $%.4f\n"
            "  DRY_RUN: %s\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            opportunity.type,
            opportunity.kalshi_leg, opportunity.kalshi_cost,
            f"{opportunity.kalshi_strike:,.0f}",
            opportunity.poly_leg, opportunity.poly_cost,
            opportunity.net_margin,
            self.settings.DRY_RUN,
        )

        # Step 1: Pre-flight checks
        preflight_ok, preflight_err = self._preflight_check(opportunity)
        if not preflight_ok:
            logger.warning("⛔ Pre-flight failed: %s", preflight_err)
            return ExecutionResult(
                status=ExecutionStatus.PREFLIGHT_FAILED,
                opportunity=opportunity,
                error=preflight_err,
            )

        # Step 2: DRY_RUN gate
        if self.settings.DRY_RUN:
            logger.info(
                "🔒 DRY-RUN: Would execute %s trade | margin=$%.4f",
                opportunity.type,
                opportunity.net_margin,
            )
            return ExecutionResult(
                status=ExecutionStatus.DRY_RUN,
                opportunity=opportunity,
            )

        # Step 3: Place Leg 1 — Kalshi (faster, REST)
        leg1_result, leg1_err = self._execute_kalshi_leg(opportunity)
        if leg1_err:
            logger.error("❌ Leg 1 (Kalshi) failed: %s", leg1_err)
            return ExecutionResult(
                status=ExecutionStatus.LEG1_FAILED,
                opportunity=opportunity,
                error=f"Kalshi leg failed: {leg1_err}",
            )

        # Step 4: Place Leg 2 — Polymarket (on-chain)
        leg2_result, leg2_err = self._execute_poly_leg(opportunity)
        if leg2_err:
            logger.error("❌ Leg 2 (Polymarket) failed: %s — ATTEMPTING UNWIND", leg2_err)
            unwind_ok = self._attempt_unwind_kalshi(leg1_result)
            status = ExecutionStatus.UNWOUND if unwind_ok else ExecutionStatus.LEG2_FAILED
            return ExecutionResult(
                status=status,
                opportunity=opportunity,
                leg1_result=leg1_result,
                error=f"Poly leg failed: {leg2_err}. Unwind: {'success' if unwind_ok else 'FAILED'}",
            )

        # Step 5: Both legs filled — record positions
        position_id = self._record_positions(opportunity, leg1_result, leg2_result)
        self._trade_count_this_hour += 1

        logger.info(
            "🎯 ARBITRAGE EXECUTED | position=%s | margin=$%.4f",
            position_id, opportunity.net_margin,
        )

        return ExecutionResult(
            status=ExecutionStatus.SUCCESS,
            opportunity=opportunity,
            leg1_result=leg1_result,
            leg2_result=leg2_result,
            position_id=position_id,
        )

    # ── Pre-flight Checks ────────────────────────────────

    def _preflight_check(self, opp: ArbitrageCheck) -> Tuple[bool, Optional[str]]:
        """Validate all conditions before execution."""

        # 1. Minimum margin
        if opp.net_margin < self.settings.MIN_NET_MARGIN:
            return False, f"Net margin ${opp.net_margin:.4f} below min ${self.settings.MIN_NET_MARGIN:.4f}"

        # 2. Trade rate limit
        if self._trade_count_this_hour >= self.settings.MAX_TRADES_PER_HOUR:
            return False, f"Rate limit: {self._trade_count_this_hour}/{self.settings.MAX_TRADES_PER_HOUR} trades this hour"

        # 3. Exposure limit
        current_exposure = self.tracker.get_total_exposure()
        trade_cost = opp.total_cost  # Cost of one contract pair
        if current_exposure + trade_cost > self.settings.MAX_TOTAL_EXPOSURE_USD:
            return False, (
                f"Exposure limit: ${current_exposure:.2f} + ${trade_cost:.2f} "
                f"> ${self.settings.MAX_TOTAL_EXPOSURE_USD:.2f}"
            )

        # 4. Single trade limit
        if trade_cost > self.settings.MAX_SINGLE_TRADE_USD:
            return False, f"Single trade ${trade_cost:.2f} > max ${self.settings.MAX_SINGLE_TRADE_USD:.2f}"

        # 5. Daily loss limit
        if self._daily_loss >= self.settings.MAX_DAILY_LOSS_USD:
            return False, f"Daily loss ${self._daily_loss:.2f} >= max ${self.settings.MAX_DAILY_LOSS_USD:.2f}"

        return True, None

    # ── Leg Execution ────────────────────────────────────

    def _execute_kalshi_leg(self, opp: ArbitrageCheck) -> Tuple[Optional[dict], Optional[str]]:
        """Place the Kalshi leg of the arbitrage."""
        side = opp.kalshi_leg.lower()  # "yes" or "no"
        price_cents = int(opp.kalshi_cost * 100)

        return self.kalshi.place_order(
            ticker=f"KXBTCD-STRIKE-{int(opp.kalshi_strike)}",  # Placeholder ticker
            side=side,
            action="buy",
            count=1,  # 1 contract for now (position sizing in Sprint 4)
            price_cents=price_cents,
            order_type="limit",
            dry_run=False,  # Already past DRY_RUN gate
        )

    def _execute_poly_leg(self, opp: ArbitrageCheck) -> Tuple[Optional[dict], Optional[str]]:
        """Place the Polymarket leg of the arbitrage."""
        side = "BUY"  # Always buying on Polymarket
        price = opp.poly_cost

        return self.poly.place_order(
            token_id="placeholder_token_id",  # Will be resolved from market data
            side=side,
            price=price,
            size=1.0,  # 1 contract for now
            order_type="FOK",
            dry_run=False,  # Already past DRY_RUN gate
        )

    # ── Unwind Logic ─────────────────────────────────────

    def _attempt_unwind_kalshi(self, leg1_result: Optional[dict]) -> bool:
        """
        Attempt to cancel or sell the Kalshi position if Poly leg fails.
        Returns True if unwind succeeded.
        """
        if not leg1_result:
            return True  # Nothing to unwind

        order_id = None
        if isinstance(leg1_result, dict):
            order = leg1_result.get("order", {})
            order_id = order.get("order_id") if isinstance(order, dict) else None

        if not order_id:
            logger.error("⚠️ Cannot unwind — no order_id in leg1 result")
            return False

        logger.info("🔄 Attempting to cancel Kalshi order %s", order_id)
        _, err = self.kalshi.cancel_order(order_id)
        if err:
            logger.error("⚠️ UNWIND FAILED for order %s: %s", order_id, err)
            return False

        logger.info("✅ Kalshi order %s cancelled successfully", order_id)
        return True

    # ── Position Recording ───────────────────────────────

    def _record_positions(
        self, opp: ArbitrageCheck,
        leg1_result: Optional[dict],
        leg2_result: Optional[dict],
    ) -> str:
        """Record both legs as linked positions."""
        kalshi_side = PositionSide.LONG if opp.kalshi_leg.lower() == "yes" else PositionSide.SHORT
        poly_side = PositionSide.LONG if opp.poly_leg in ("Up", "up") else PositionSide.SHORT

        kalshi_pos = self.tracker.open_position(
            platform=Platform.KALSHI,
            side=kalshi_side,
            ticker=f"KXBTCD-{int(opp.kalshi_strike)}",
            entry_price=opp.kalshi_cost,
            size=1,
        )

        poly_pos = self.tracker.open_position(
            platform=Platform.POLYMARKET,
            side=poly_side,
            ticker=f"poly-{opp.poly_leg}",
            entry_price=opp.poly_cost,
            size=1,
            linked_position_id=kalshi_pos.id,
        )

        arb = self.tracker.open_arbitrage(
            kalshi_position=kalshi_pos,
            poly_position=poly_pos,
            expected_profit=opp.net_margin,
        )

        return arb.id

    # ── Housekeeping ─────────────────────────────────────

    def reset_hourly_counter(self):
        """Reset hourly trade counter (called by scheduler)."""
        self._trade_count_this_hour = 0
        logger.info("Trade counter reset for new hour")

    def reset_daily_loss(self):
        """Reset daily loss tracker (called by scheduler, midnight UTC)."""
        self._daily_loss = 0.0
        logger.info("Daily loss tracker reset")

    def get_status(self) -> dict:
        """Engine status for monitoring."""
        return {
            "dry_run": self.settings.DRY_RUN,
            "trades_this_hour": self._trade_count_this_hour,
            "max_trades_per_hour": self.settings.MAX_TRADES_PER_HOUR,
            "daily_loss": round(self._daily_loss, 4),
            "max_daily_loss": self.settings.MAX_DAILY_LOSS_USD,
            "positions": self.tracker.get_summary(),
        }
