"""
Binance WebSocket — real-time BTC price feed.

Connects to Binance's public WebSocket stream for BTCUSDT ticker data.
Provides sub-second price updates with automatic reconnection.

Security: No API keys required (public stream). No secrets logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Binance public WebSocket endpoint
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@ticker"


class BinanceWebSocket:
    """
    Real-time BTC price via Binance WebSocket.

    Streams: btcusdt@ticker (24hr rolling ticker with current price)

    Features:
    - Auto-reconnect with exponential backoff
    - Callback system for price updates
    - Staleness detection
    - Clean shutdown
    """

    def __init__(
        self,
        url: str = BINANCE_WS_URL,
        max_reconnect_delay: float = 60.0,
        on_price: Optional[Callable[[float, float], None]] = None,
    ):
        self.url = url
        self.max_reconnect_delay = max_reconnect_delay
        self._on_price = on_price

        # State
        self._current_price: Optional[float] = None
        self._last_update: float = 0.0
        self._connected: bool = False
        self._running: bool = False
        self._reconnect_delay: float = 1.0
        self._message_count: int = 0
        self._callbacks: List[Callable] = []

        if on_price:
            self._callbacks.append(on_price)

    # ── Public Interface ─────────────────────────────────

    def add_callback(self, callback: Callable[[float, float], None]) -> None:
        """Register a callback: callback(price, timestamp)."""
        self._callbacks.append(callback)

    @property
    def price(self) -> Optional[float]:
        return self._current_price

    @property
    def last_update(self) -> float:
        return self._last_update

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def age_seconds(self) -> float:
        """How old is the latest price data."""
        if self._last_update == 0:
            return float("inf")
        return time.time() - self._last_update

    @property
    def message_count(self) -> int:
        return self._message_count

    async def start(self) -> None:
        """Start the WebSocket connection with auto-reconnect."""
        self._running = True
        logger.info("Starting Binance WS feed: %s", self.url)

        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                logger.info("Binance WS cancelled")
                break
            except Exception as e:
                self._connected = False
                if not self._running:
                    break
                logger.warning(
                    "Binance WS disconnected: %s — reconnecting in %.0fs",
                    str(e)[:100], self._reconnect_delay,
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self.max_reconnect_delay,
                )

    async def stop(self) -> None:
        """Gracefully stop the WebSocket connection."""
        self._running = False
        self._connected = False
        logger.info("Binance WS feed stopped")

    # ── Internal ─────────────────────────────────────────

    async def _connect_and_listen(self) -> None:
        """Connect to Binance WS and process messages."""
        import websockets

        async with websockets.connect(self.url, ping_interval=20) as ws:
            self._connected = True
            self._reconnect_delay = 1.0  # Reset backoff on successful connect
            logger.info("🟢 Binance WS connected")

            async for raw_msg in ws:
                if not self._running:
                    break
                self._process_message(raw_msg)

    def _process_message(self, raw: str) -> None:
        """Parse a Binance ticker message and update state."""
        try:
            data = json.loads(raw)
            price = float(data.get("c", 0))  # 'c' = last price
            if price <= 0:
                return

            self._current_price = price
            self._last_update = time.time()
            self._message_count += 1

            # Fire callbacks
            for cb in self._callbacks:
                try:
                    cb(price, self._last_update)
                except Exception as e:
                    logger.error("Callback error: %s", e)

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("Bad Binance WS message: %s", str(e)[:80])

    def get_status(self) -> dict:
        """Status for monitoring. No secrets."""
        return {
            "connected": self._connected,
            "price": self._current_price,
            "last_update": self._last_update,
            "age_seconds": round(self.age_seconds, 1) if self._last_update > 0 else None,
            "message_count": self._message_count,
            "url": self.url,
        }
