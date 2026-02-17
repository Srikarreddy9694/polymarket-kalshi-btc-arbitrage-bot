#!/usr/bin/env python3
"""
Paper Trading Orchestrator — runs the bot in DRY_RUN mode and logs every opportunity.

Usage:
    python scripts/paper_trade.py            # Run for default 24 hours
    python scripts/paper_trade.py --hours 168  # Run for 1 week

This script:
1. Forces DRY_RUN=True (safety override)
2. Polls for arbitrage opportunities continuously
3. Logs every opportunity to the database with full details
4. Sends periodic summaries via Telegram (if configured)
5. Produces a paper trading report at the end

Security: Forces DRY_RUN=True regardless of .env setting.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

# Add parent to path so imports work when run from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Force DRY_RUN before importing settings
os.environ["DRY_RUN"] = "True"

from config.settings import Settings
from core.arbitrage import ArbitrageEngine
from core.fee_engine import FeeEngine
from clients.polymarket_client import PolymarketClient
from clients.kalshi_client import KalshiClient
from storage.database import Database
from monitoring.telegram_alerts import TelegramAlerts

logger = logging.getLogger("paper_trade")


class PaperTrader:
    """
    Paper trading orchestrator — logs opportunities without executing trades.
    """

    def __init__(self, settings: Settings):
        # SAFETY: Force dry run
        settings.DRY_RUN = True

        self.settings = settings
        self.fee_engine = FeeEngine(settings=settings)
        self.arb_engine = ArbitrageEngine(fee_engine=self.fee_engine, settings=settings)
        self.poly_client = PolymarketClient(settings=settings)
        self.kalshi_client = KalshiClient(settings=settings)
        self.db = Database(db_path=settings.DB_PATH)
        self.telegram = TelegramAlerts(
            bot_token=settings.TELEGRAM_BOT_TOKEN,
            chat_id=settings.TELEGRAM_CHAT_ID,
        )

        # Stats
        self.opportunities_found: int = 0
        self.profitable_opportunities: int = 0
        self.total_simulated_pnl: float = 0.0
        self.margins: list = []
        self.start_time: float = 0.0
        self._running: bool = False

    async def run(self, duration_hours: float = 24.0) -> dict:
        """Run paper trading for the specified duration."""
        self.start_time = time.time()
        end_time = self.start_time + (duration_hours * 3600)
        self._running = True

        logger.info(
            "📄 Paper trading started (DRY_RUN=%s, duration=%.1fh, poll=%.1fs)",
            self.settings.DRY_RUN, duration_hours, self.settings.POLLING_INTERVAL_SEC,
        )

        await self.telegram.send_message(
            f"📄 <b>Paper Trading Started</b>\n"
            f"Duration: {duration_hours:.0f}h\n"
            f"Poll interval: {self.settings.POLLING_INTERVAL_SEC}s"
        )

        try:
            while self._running and time.time() < end_time:
                await self._scan_cycle()
                await asyncio.sleep(self.settings.POLLING_INTERVAL_SEC)

        except asyncio.CancelledError:
            logger.info("Paper trading cancelled")
        finally:
            self._running = False

        report = self._generate_report()
        await self._send_final_report(report)
        return report

    async def _scan_cycle(self) -> None:
        """One scan cycle: fetch data, check for arbitrage, log results."""
        try:
            poly_data = self.poly_client.get_btc_market_data()
            kalshi_data = self.kalshi_client.get_btc_market_data()

            if not poly_data or not kalshi_data:
                return

            result = self.arb_engine.check_arbitrage(poly_data, kalshi_data)

            self.opportunities_found += 1

            if result.net_margin > 0:
                self.profitable_opportunities += 1
                self.total_simulated_pnl += result.net_margin
                self.margins.append(result.net_margin)

                # Log to database
                self.db.log_event(
                    event_type="paper_opportunity",
                    details=json.dumps({
                        "strategy": result.strategy_type,
                        "gross_margin": result.gross_margin,
                        "net_margin": result.net_margin,
                        "fees": result.total_fees,
                        "poly_yes": poly_data.get("yes_price"),
                        "kalshi_yes": kalshi_data.get("yes_price"),
                    }),
                    severity="info",
                )

                logger.info(
                    "💡 Opportunity #%d: strategy=%s net_margin=$%.4f gross=$%.4f",
                    self.profitable_opportunities,
                    result.strategy_type,
                    result.net_margin,
                    result.gross_margin,
                )

        except Exception as e:
            logger.warning("Scan cycle error: %s", str(e)[:80])

    def _generate_report(self) -> dict:
        """Generate the final paper trading report."""
        elapsed = time.time() - self.start_time
        elapsed_hours = elapsed / 3600

        avg_margin = (
            sum(self.margins) / len(self.margins) if self.margins else 0.0
        )
        max_margin = max(self.margins) if self.margins else 0.0
        min_margin = min(self.margins) if self.margins else 0.0

        return {
            "duration_hours": round(elapsed_hours, 2),
            "total_scans": self.opportunities_found,
            "profitable_opportunities": self.profitable_opportunities,
            "hit_rate_pct": round(
                (self.profitable_opportunities / max(self.opportunities_found, 1)) * 100, 2
            ),
            "simulated_pnl_usd": round(self.total_simulated_pnl, 4),
            "avg_margin_usd": round(avg_margin, 4),
            "max_margin_usd": round(max_margin, 4),
            "min_margin_usd": round(min_margin, 4),
            "scans_per_hour": round(self.opportunities_found / max(elapsed_hours, 0.01), 1),
        }

    async def _send_final_report(self, report: dict) -> None:
        """Send the final report via Telegram and log it."""
        logger.info("📊 Paper Trading Report: %s", json.dumps(report, indent=2))

        await self.telegram.send_message(
            f"📊 <b>Paper Trading Complete</b>\n"
            f"═══════════════\n"
            f"⏱ Duration: {report['duration_hours']:.1f}h\n"
            f"🔍 Scans: {report['total_scans']}\n"
            f"💡 Profitable: {report['profitable_opportunities']}\n"
            f"📈 Hit Rate: {report['hit_rate_pct']:.1f}%\n"
            f"💰 Sim P&L: ${report['simulated_pnl_usd']:+.4f}\n"
            f"📊 Avg Margin: ${report['avg_margin_usd']:.4f}\n"
        )

    def stop(self) -> None:
        """Stop the paper trading loop."""
        self._running = False


def main():
    parser = argparse.ArgumentParser(description="Paper Trading Orchestrator")
    parser.add_argument("--hours", type=float, default=24.0, help="Duration in hours (default: 24)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)-20s | %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # Override DRY_RUN in settings (belt and suspenders)
    settings = Settings(DRY_RUN=True)
    trader = PaperTrader(settings=settings)

    # Graceful shutdown on Ctrl+C
    def handle_signal(sig, frame):
        logger.info("Received signal %s — stopping paper trading", sig)
        trader.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    report = asyncio.run(trader.run(duration_hours=args.hours))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
