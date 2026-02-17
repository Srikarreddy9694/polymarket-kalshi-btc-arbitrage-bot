"""
Shared Binance client for BTC price data.

Previously duplicated across fetch_current_polymarket.py,
fetch_current_kalshi.py, and fetch_data.py.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Tuple

import requests

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class BinanceClient:
    """Fetches BTC price data from Binance public API."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.session = requests.Session()

    def get_current_price(self) -> Tuple[Optional[float], Optional[str]]:
        """Returns the current BTCUSDT price."""
        try:
            response = self.session.get(
                self.settings.BINANCE_PRICE_URL,
                params={"symbol": self.settings.BINANCE_SYMBOL},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            price = float(data["price"])
            logger.debug("Binance current price: $%.2f", price)
            return price, None
        except requests.RequestException as e:
            logger.error("Binance current price fetch failed: %s", e)
            return None, str(e)

    def get_open_price(self, target_time_utc: datetime) -> Tuple[Optional[float], Optional[str]]:
        """Returns the open price for the 1h candle starting at target_time_utc."""
        try:
            timestamp_ms = int(target_time_utc.timestamp() * 1000)
            response = self.session.get(
                self.settings.BINANCE_KLINES_URL,
                params={
                    "symbol": self.settings.BINANCE_SYMBOL,
                    "interval": "1h",
                    "startTime": timestamp_ms,
                    "limit": 1,
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            if not data:
                return None, "Candle not found yet"

            open_price = float(data[0][1])
            logger.debug("Binance open price at %s: $%.2f", target_time_utc, open_price)
            return open_price, None
        except requests.RequestException as e:
            logger.error("Binance open price fetch failed: %s", e)
            return None, str(e)
