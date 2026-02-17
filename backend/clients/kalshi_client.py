"""
Kalshi data client — fetches market data and parses strike prices.

This client handles:
- Market listing via Kalshi public API
- Strike price parsing from subtitles
- Returning structured KalshiData with KalshiMarket models
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple

import requests

from clients.base import BaseClient
from clients.binance_client import BinanceClient
from core.models import KalshiData, KalshiMarket
from config.settings import Settings

logger = logging.getLogger(__name__)


def parse_strike(subtitle: str) -> float:
    """
    Parse strike price from Kalshi subtitle.
    Format: "$96,250 or above" → 96250.0
    """
    match = re.search(r'\$([\d,]+)', subtitle)
    if match:
        return float(match.group(1).replace(',', ''))
    return 0.0


class KalshiClient(BaseClient):
    """
    Fetches Kalshi market data:
    - Market listings by event ticker
    - Strike price parsing
    - BTC price from Binance for context
    """

    def __init__(self, settings: Optional[Settings] = None, binance: Optional[BinanceClient] = None):
        super().__init__(settings)
        self.binance = binance or BinanceClient(self.settings)

    # --- Public API ---

    def fetch_data(self) -> Tuple[Optional[KalshiData], Optional[str]]:
        """Fetch Kalshi data for the current hour's market."""
        try:
            from get_current_markets import get_current_market_urls

            market_info = get_current_market_urls()
            kalshi_url = market_info["kalshi"]
            event_ticker = kalshi_url.split("/")[-1].upper()

            return self.fetch_by_event(event_ticker)

        except Exception as e:
            self.logger.error("Failed to fetch Kalshi data: %s", e, exc_info=True)
            return None, str(e)

    def fetch_by_event(self, event_ticker: str) -> Tuple[Optional[KalshiData], Optional[str]]:
        """Fetch Kalshi data for a specific event ticker."""
        try:
            current_price, _ = self.binance.get_current_price()
            raw_markets, err = self._get_markets(event_ticker)

            if err:
                return None, f"Kalshi Error: {err}"

            if not raw_markets:
                return KalshiData(
                    event_ticker=event_ticker,
                    current_price=current_price,
                    markets=[],
                ), None

            markets = self._parse_markets(raw_markets)
            markets.sort(key=lambda m: m.strike)

            data = KalshiData(
                event_ticker=event_ticker,
                current_price=current_price,
                markets=markets,
            )

            self.logger.info(
                "Kalshi %s: %d markets, price range $%s–$%s",
                event_ticker,
                len(markets),
                f"{markets[0].strike:,.0f}" if markets else "?",
                f"{markets[-1].strike:,.0f}" if markets else "?",
            )
            return data, None

        except Exception as e:
            self.logger.error("fetch_by_event failed: %s", e, exc_info=True)
            return None, str(e)

    # --- Internal ---

    def _get_markets(self, event_ticker: str) -> Tuple[Optional[List[dict]], Optional[str]]:
        """Fetch raw market list from Kalshi API."""
        try:
            data = self._get(
                self.settings.KALSHI_API_URL,
                params={"limit": 100, "event_ticker": event_ticker},
            )
            return data.get("markets", []), None

        except requests.RequestException as e:
            self.logger.error("Kalshi API error: %s", e)
            return None, str(e)

    def _parse_markets(self, raw_markets: List[dict]) -> List[KalshiMarket]:
        """Parse raw API response into KalshiMarket models."""
        markets = []
        for m in raw_markets:
            subtitle = m.get("subtitle", "")
            strike = parse_strike(subtitle)
            if strike <= 0:
                self.logger.debug("Skipping market with unparseable strike: %s", subtitle)
                continue

            market = KalshiMarket(
                strike=strike,
                yes_bid=m.get("yes_bid", 0) or 0,
                yes_ask=m.get("yes_ask", 0) or 0,
                no_bid=m.get("no_bid", 0) or 0,
                no_ask=m.get("no_ask", 0) or 0,
                subtitle=subtitle,
            )
            markets.append(market)

        return markets
