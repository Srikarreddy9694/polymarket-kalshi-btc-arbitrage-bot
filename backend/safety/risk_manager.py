"""
Risk Manager — enforces all trading limits and safety checks.

This is the gatekeeper for trade execution. Every trade must pass
ALL checks before the OrderEngine can proceed.

Security: No secrets are logged. All decisions are auditable.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Optional, Tuple

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# Sentinel: never log raw settings or credentials
_REDACTED = "***REDACTED***"


class RiskManager:
    """
    Enforces trading limits and safety checks.

    6 gates, ALL must pass:
    1. Minimum net margin
    2. Max single trade size
    3. Max total exposure
    4. Max daily loss
    5. Max trades per hour (rate limit)
    6. Circuit breaker status (checked externally via is_halted flag)
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()

        # Counters (in-memory, will be backed by DB in storage)
        self._trade_timestamps: deque = deque()  # timestamps for rate limiting
        self._daily_pnl: float = 0.0
        self._total_exposure: float = 0.0
        self._trade_count_today: int = 0
        self._is_halted: bool = False  # Set by circuit breaker / kill switch
        self._halt_reason: str = ""

        logger.info(
            "RiskManager initialized: max_trade=$%.0f max_exposure=$%.0f "
            "max_daily_loss=$%.0f max_trades/hr=%d min_margin=$%.4f",
            self.settings.MAX_SINGLE_TRADE_USD,
            self.settings.MAX_TOTAL_EXPOSURE_USD,
            self.settings.MAX_DAILY_LOSS_USD,
            self.settings.MAX_TRADES_PER_HOUR,
            self.settings.MIN_NET_MARGIN,
        )

    # ── Trade Validation ─────────────────────────────────

    def check_trade_allowed(
        self, net_margin: float, trade_cost_usd: float, current_exposure: float = 0.0,
    ) -> Tuple[bool, str]:
        """
        Returns (allowed, reason).

        EVERY check must pass. On first failure, returns (False, reason).
        All checks are logged for audit trail.
        """
        # Gate 0: Kill switch / halt
        if self._is_halted:
            reason = f"Trading halted: {self._halt_reason}"
            logger.warning("⛔ RISK GATE 0 FAILED: %s", reason)
            return False, reason

        # Gate 1: Minimum margin
        if net_margin < self.settings.MIN_NET_MARGIN:
            reason = f"Net margin ${net_margin:.4f} < min ${self.settings.MIN_NET_MARGIN:.4f}"
            logger.info("⛔ RISK GATE 1 FAILED: %s", reason)
            return False, reason

        # Gate 2: Max single trade
        if trade_cost_usd > self.settings.MAX_SINGLE_TRADE_USD:
            reason = f"Trade ${trade_cost_usd:.2f} > max ${self.settings.MAX_SINGLE_TRADE_USD:.2f}"
            logger.info("⛔ RISK GATE 2 FAILED: %s", reason)
            return False, reason

        # Gate 3: Max total exposure
        projected_exposure = current_exposure + trade_cost_usd
        if projected_exposure > self.settings.MAX_TOTAL_EXPOSURE_USD:
            reason = (
                f"Exposure ${current_exposure:.2f} + ${trade_cost_usd:.2f} = "
                f"${projected_exposure:.2f} > max ${self.settings.MAX_TOTAL_EXPOSURE_USD:.2f}"
            )
            logger.info("⛔ RISK GATE 3 FAILED: %s", reason)
            return False, reason

        # Gate 4: Daily loss limit
        if self._daily_pnl <= -self.settings.MAX_DAILY_LOSS_USD:
            reason = f"Daily loss ${abs(self._daily_pnl):.2f} >= max ${self.settings.MAX_DAILY_LOSS_USD:.2f}"
            logger.warning("⛔ RISK GATE 4 FAILED: %s", reason)
            return False, reason

        # Gate 5: Rate limit (trades per hour)
        self._clean_old_timestamps()
        if len(self._trade_timestamps) >= self.settings.MAX_TRADES_PER_HOUR:
            reason = f"Rate limit: {len(self._trade_timestamps)}/{self.settings.MAX_TRADES_PER_HOUR} trades/hr"
            logger.warning("⛔ RISK GATE 5 FAILED: %s", reason)
            return False, reason

        logger.debug("✅ All 6 risk gates passed for trade of $%.2f", trade_cost_usd)
        return True, "approved"

    # ── State Management ─────────────────────────────────

    def record_trade(self, pnl: float, cost_usd: float) -> None:
        """Record a completed trade for risk tracking."""
        self._trade_timestamps.append(time.time())
        self._daily_pnl += pnl
        self._total_exposure += cost_usd
        self._trade_count_today += 1

        logger.info(
            "📊 Trade recorded: PnL=$%.4f | Daily PnL=$%.4f | Exposure=$%.2f | Trades=%d",
            pnl, self._daily_pnl, self._total_exposure, self._trade_count_today,
        )

    def close_position(self, cost_usd: float) -> None:
        """Reduce exposure when a position is settled."""
        self._total_exposure = max(0.0, self._total_exposure - cost_usd)

    def halt(self, reason: str) -> None:
        """Halt all trading. Called by circuit breaker or kill switch."""
        self._is_halted = True
        self._halt_reason = reason
        logger.critical("🛑 TRADING HALTED: %s", reason)

    def resume(self, reason: str = "manual") -> None:
        """Resume trading after halt."""
        self._is_halted = False
        self._halt_reason = ""
        logger.info("▶️ TRADING RESUMED: %s", reason)

    # ── Queries ──────────────────────────────────────────

    @property
    def is_halted(self) -> bool:
        return self._is_halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    def get_daily_pnl(self) -> float:
        return round(self._daily_pnl, 6)

    def get_total_exposure(self) -> float:
        return round(self._total_exposure, 2)

    def get_trades_this_hour(self) -> int:
        self._clean_old_timestamps()
        return len(self._trade_timestamps)

    def get_status(self) -> dict:
        """
        Return full risk manager status for monitoring.
        SECURITY: never includes API keys, credentials, or sensitive config paths.
        """
        return {
            "is_halted": self._is_halted,
            "halt_reason": self._halt_reason if self._is_halted else None,
            "daily_pnl": round(self._daily_pnl, 4),
            "total_exposure": round(self._total_exposure, 2),
            "trades_today": self._trade_count_today,
            "trades_this_hour": self.get_trades_this_hour(),
            "limits": {
                "max_single_trade": self.settings.MAX_SINGLE_TRADE_USD,
                "max_exposure": self.settings.MAX_TOTAL_EXPOSURE_USD,
                "max_daily_loss": self.settings.MAX_DAILY_LOSS_USD,
                "max_trades_per_hour": self.settings.MAX_TRADES_PER_HOUR,
                "min_net_margin": self.settings.MIN_NET_MARGIN,
            },
        }

    # ── Resets ───────────────────────────────────────────

    def reset_daily(self) -> None:
        """Reset daily counters. Called at midnight UTC."""
        self._daily_pnl = 0.0
        self._trade_count_today = 0
        logger.info("📅 Daily risk counters reset")

    # ── Internal ─────────────────────────────────────────

    def _clean_old_timestamps(self) -> None:
        """Remove timestamps older than 1 hour for rate limiting."""
        cutoff = time.time() - 3600
        while self._trade_timestamps and self._trade_timestamps[0] < cutoff:
            self._trade_timestamps.popleft()
