"""
Telegram Alert Bot — sends trade notifications and alerts.

Alert levels:
  - INFO:  Trade executed, daily summary
  - WARNING: Circuit breaker tripped, high latency
  - CRITICAL: Kill switch activated, daily loss exceeded

Security: No secrets logged. Bot token and chat_id loaded from settings only.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Telegram API base
_TG_API = "https://api.telegram.org"


class TelegramAlerts:
    """
    Sends formatted alerts to a Telegram chat.

    Pass bot_token="" and chat_id="" to disable (no-op mode).
    All methods are safe to call even when disabled — they just log locally.
    """

    def __init__(self, bot_token: str = "", chat_id: str = ""):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)
        self._send_count: int = 0
        self._error_count: int = 0
        self._last_send: float = 0.0
        self._rate_limit_sec: float = 1.0  # Telegram rate limit: ~30 msg/sec per chat

        if self._enabled:
            logger.info("Telegram alerts enabled (chat_id=%s***)", chat_id[:4] if len(chat_id) > 4 else "***")
        else:
            logger.info("Telegram alerts disabled (no bot_token/chat_id)")

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message to the configured Telegram chat."""
        if not self._enabled:
            logger.debug("Telegram disabled — dropped message: %s", text[:60])
            return False

        # Rate limiting
        now = time.time()
        if now - self._last_send < self._rate_limit_sec:
            await asyncio.sleep(self._rate_limit_sec - (now - self._last_send))

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{_TG_API}/bot{self.bot_token}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": True,
                    },
                )
                response.raise_for_status()
                self._send_count += 1
                self._last_send = time.time()
                return True

        except Exception as e:
            self._error_count += 1
            logger.warning("Telegram send failed: %s", str(e)[:80])
            return False

    # ── Convenience Methods ──────────────────────────────

    async def alert_trade(
        self,
        trade_id: str,
        platform: str,
        side: str,
        cost_usd: float,
        pnl: float,
        dry_run: bool = True,
    ) -> bool:
        """Send a trade alert."""
        mode = "🧪 DRY-RUN" if dry_run else "🔴 LIVE"
        emoji = "💰" if pnl > 0 else "📉" if pnl < 0 else "➡️"
        text = (
            f"{mode} — Trade Executed\n"
            f"───────────────\n"
            f"📋 <b>ID:</b> {trade_id}\n"
            f"🏦 <b>Platform:</b> {platform}\n"
            f"📊 <b>Side:</b> {side}\n"
            f"💵 <b>Cost:</b> ${cost_usd:.2f}\n"
            f"{emoji} <b>P&L:</b> ${pnl:+.4f}\n"
        )
        return await self.send_message(text)

    async def alert_circuit_breaker(self, state: str, reason: str) -> bool:
        """Send a circuit breaker state change alert."""
        emoji = "🔴" if state == "open" else "🟡" if state == "half_open" else "🟢"
        text = (
            f"⚡ Circuit Breaker — {state.upper()}\n"
            f"───────────────\n"
            f"{emoji} <b>State:</b> {state}\n"
            f"📝 <b>Reason:</b> {reason}\n"
        )
        return await self.send_message(text)

    async def alert_kill_switch(self, active: bool, reason: str) -> bool:
        """Send a kill switch alert."""
        status = "🛑 ACTIVATED" if active else "▶️ DEACTIVATED"
        text = (
            f"🚨 Kill Switch — {status}\n"
            f"───────────────\n"
            f"📝 <b>Reason:</b> {reason}\n"
        )
        return await self.send_message(text)

    async def alert_daily_summary(
        self,
        daily_pnl: float,
        trades_count: int,
        exposure: float,
        opportunities: int,
    ) -> bool:
        """Send the daily P&L summary."""
        emoji = "💰" if daily_pnl > 0 else "📉" if daily_pnl < 0 else "➡️"
        text = (
            f"📊 Daily Summary\n"
            f"═══════════════\n"
            f"{emoji} <b>P&L:</b> ${daily_pnl:+.4f}\n"
            f"📈 <b>Trades:</b> {trades_count}\n"
            f"💼 <b>Exposure:</b> ${exposure:.2f}\n"
            f"🔍 <b>Opportunities:</b> {opportunities}\n"
        )
        return await self.send_message(text)

    async def alert_high_latency(self, latency_ms: float, target_ms: float = 500) -> bool:
        """Alert when execution latency exceeds target."""
        text = (
            f"⏱ High Latency Warning\n"
            f"───────────────\n"
            f"🐌 <b>Measured:</b> {latency_ms:.0f}ms\n"
            f"🎯 <b>Target:</b> {target_ms:.0f}ms\n"
        )
        return await self.send_message(text)

    # ── Status ───────────────────────────────────────────

    def get_status(self) -> dict:
        """Status for monitoring. No secrets (token/chat_id excluded)."""
        return {
            "enabled": self._enabled,
            "messages_sent": self._send_count,
            "errors": self._error_count,
            "last_send": self._last_send if self._last_send > 0 else None,
        }
