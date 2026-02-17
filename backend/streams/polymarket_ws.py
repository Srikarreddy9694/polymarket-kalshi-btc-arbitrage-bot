"""
Polymarket WebSocket — real-time CLOB order book updates.

Connects to Polymarket's CLOB WebSocket for live order book changes.
Maintains an in-memory order book and triggers callbacks on updates.

Security: No API keys required for public market data streams.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Polymarket CLOB WebSocket endpoint
POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class PolymarketWebSocket:
    """
    Real-time Polymarket CLOB order book via WebSocket.

    Subscribes to specific market condition IDs and receives
    order book snapshots and deltas.

    Features:
    - Subscribe to multiple markets
    - Maintains latest best bid/ask
    - Auto-reconnect with exponential backoff
    - Callback system for book updates
    """

    def __init__(
        self,
        url: str = POLYMARKET_WS_URL,
        max_reconnect_delay: float = 60.0,
    ):
        self.url = url
        self.max_reconnect_delay = max_reconnect_delay

        # State
        self._books: Dict[str, dict] = {}  # token_id → {best_bid, best_ask, ...}
        self._subscribed_markets: List[str] = []
        self._connected: bool = False
        self._running: bool = False
        self._reconnect_delay: float = 1.0
        self._last_update: float = 0.0
        self._message_count: int = 0
        self._callbacks: List[Callable] = []

    # ── Public Interface ─────────────────────────────────

    def subscribe(self, token_id: str) -> None:
        """Add a market token ID to subscribe to."""
        if token_id not in self._subscribed_markets:
            self._subscribed_markets.append(token_id)

    def add_callback(self, callback: Callable[[str, dict], None]) -> None:
        """Register a callback: callback(token_id, book_data)."""
        self._callbacks.append(callback)

    def get_book(self, token_id: str) -> Optional[dict]:
        """Get the latest order book for a token."""
        return self._books.get(token_id)

    def get_best_bid(self, token_id: str) -> Optional[float]:
        book = self._books.get(token_id)
        return book.get("best_bid") if book else None

    def get_best_ask(self, token_id: str) -> Optional[float]:
        book = self._books.get(token_id)
        return book.get("best_ask") if book else None

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def last_update(self) -> float:
        return self._last_update

    @property
    def age_seconds(self) -> float:
        if self._last_update == 0:
            return float("inf")
        return time.time() - self._last_update

    @property
    def message_count(self) -> int:
        return self._message_count

    async def start(self) -> None:
        """Start the WebSocket connection with auto-reconnect."""
        self._running = True
        logger.info("Starting Polymarket WS feed: %s", self.url)

        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                logger.info("Polymarket WS cancelled")
                break
            except Exception as e:
                self._connected = False
                if not self._running:
                    break
                logger.warning(
                    "Polymarket WS disconnected: %s — reconnecting in %.0fs",
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
        logger.info("Polymarket WS feed stopped")

    # ── Internal ─────────────────────────────────────────

    async def _connect_and_listen(self) -> None:
        """Connect to Polymarket WS, subscribe, and process messages."""
        import websockets

        async with websockets.connect(self.url, ping_interval=30) as ws:
            self._connected = True
            self._reconnect_delay = 1.0
            logger.info("🟢 Polymarket WS connected")

            # Subscribe to markets
            for token_id in self._subscribed_markets:
                sub_msg = json.dumps({
                    "type": "subscribe",
                    "channel": "book",
                    "market": token_id,
                })
                await ws.send(sub_msg)
                logger.info("Subscribed to Polymarket market: %s", token_id[:16] + "...")

            async for raw_msg in ws:
                if not self._running:
                    break
                self._process_message(raw_msg)

    def _process_message(self, raw: str) -> None:
        """Parse a Polymarket CLOB message and update order book state."""
        try:
            data = json.loads(raw)
            msg_type = data.get("type", "")

            if msg_type in ("book_snapshot", "book_update", "book"):
                token_id = data.get("market", data.get("asset_id", ""))
                if not token_id:
                    return

                # Extract best bid/ask from various message formats
                book_data = {
                    "best_bid": self._extract_best_bid(data),
                    "best_ask": self._extract_best_ask(data),
                    "timestamp": time.time(),
                    "raw_type": msg_type,
                }

                self._books[token_id] = book_data
                self._last_update = time.time()
                self._message_count += 1

                # Fire callbacks
                for cb in self._callbacks:
                    try:
                        cb(token_id, book_data)
                    except Exception as e:
                        logger.error("Polymarket callback error: %s", e)

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("Bad Polymarket WS message: %s", str(e)[:80])

    def _extract_best_bid(self, data: dict) -> Optional[float]:
        """Extract best bid price from message."""
        bids = data.get("bids", [])
        if bids and isinstance(bids, list):
            try:
                # Bids sorted descending — first is best
                return float(bids[0].get("price", bids[0]) if isinstance(bids[0], dict) else bids[0])
            except (ValueError, IndexError, TypeError):
                pass
        return None

    def _extract_best_ask(self, data: dict) -> Optional[float]:
        """Extract best ask price from message."""
        asks = data.get("asks", [])
        if asks and isinstance(asks, list):
            try:
                # Asks sorted ascending — first is best
                return float(asks[0].get("price", asks[0]) if isinstance(asks[0], dict) else asks[0])
            except (ValueError, IndexError, TypeError):
                pass
        return None

    def get_status(self) -> dict:
        """Status for monitoring. No secrets."""
        return {
            "connected": self._connected,
            "subscribed_markets": len(self._subscribed_markets),
            "books_cached": len(self._books),
            "last_update": self._last_update,
            "age_seconds": round(self.age_seconds, 1) if self._last_update > 0 else None,
            "message_count": self._message_count,
        }
