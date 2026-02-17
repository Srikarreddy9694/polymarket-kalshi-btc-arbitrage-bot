"""
Stream Manager — orchestrates all data feeds and emits unified events.

Central hub that manages: Binance WS, Polymarket WS, and Kalshi polling.
Aggregates data from all sources and provides a single event stream.

Security: No secrets exposed. All status methods are audit-safe.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Dict, List, Optional

from streams.binance_ws import BinanceWebSocket
from streams.polymarket_ws import PolymarketWebSocket
from streams.kalshi_ws import KalshiPollingFeed

logger = logging.getLogger(__name__)


class StreamEvent:
    """A unified event from any data feed."""

    __slots__ = ("source", "event_type", "data", "timestamp")

    def __init__(self, source: str, event_type: str, data: dict, timestamp: float = 0.0):
        self.source = source
        self.event_type = event_type
        self.data = data
        self.timestamp = timestamp or time.time()

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "event_type": self.event_type,
            "data": self.data,
            "timestamp": self.timestamp,
        }


class StreamManager:
    """
    Orchestrates all real-time data feeds.

    Produces a unified stream of events that the arbitrage engine
    and SSE endpoint can consume.
    """

    def __init__(
        self,
        binance: Optional[BinanceWebSocket] = None,
        polymarket: Optional[PolymarketWebSocket] = None,
        kalshi: Optional[KalshiPollingFeed] = None,
    ):
        self.binance = binance or BinanceWebSocket()
        self.polymarket = polymarket or PolymarketWebSocket()
        self.kalshi = kalshi or KalshiPollingFeed()

        # Event queue for SSE consumers
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers: List[asyncio.Queue] = []
        self._running: bool = False
        self._event_count: int = 0

        # Wire up callbacks
        self.binance.add_callback(self._on_binance_price)
        self.polymarket.add_callback(self._on_polymarket_book)
        self.kalshi.add_callback(self._on_kalshi_data)

    # ── Public Interface ─────────────────────────────────

    def subscribe(self) -> asyncio.Queue:
        """Create a new subscriber queue for SSE. Returns a Queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(q)
        logger.info("New stream subscriber (total=%d)", len(self._subscribers))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a subscriber queue."""
        if q in self._subscribers:
            self._subscribers.remove(q)
            logger.info("Stream subscriber removed (total=%d)", len(self._subscribers))

    async def start(self) -> None:
        """Start all data feeds concurrently."""
        self._running = True
        logger.info("Starting StreamManager — all feeds")

        tasks = [
            asyncio.create_task(self.binance.start(), name="binance_ws"),
            asyncio.create_task(self.polymarket.start(), name="polymarket_ws"),
            asyncio.create_task(self.kalshi.start(), name="kalshi_poll"),
        ]

        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            logger.info("StreamManager cancelled")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop all data feeds."""
        self._running = False
        await self.binance.stop()
        await self.polymarket.stop()
        await self.kalshi.stop()
        logger.info("StreamManager stopped")

    # ── Callbacks ────────────────────────────────────────

    def _on_binance_price(self, price: float, timestamp: float) -> None:
        """Callback from Binance WS."""
        event = StreamEvent(
            source="binance",
            event_type="price",
            data={"price": price, "symbol": "BTCUSDT"},
            timestamp=timestamp,
        )
        self._emit(event)

    def _on_polymarket_book(self, token_id: str, book_data: dict) -> None:
        """Callback from Polymarket WS."""
        event = StreamEvent(
            source="polymarket",
            event_type="book_update",
            data={"token_id": token_id, **book_data},
        )
        self._emit(event)

    def _on_kalshi_data(self, data: dict) -> None:
        """Callback from Kalshi polling."""
        event = StreamEvent(
            source="kalshi",
            event_type="market_data",
            data=data,
        )
        self._emit(event)

    def _emit(self, event: StreamEvent) -> None:
        """Emit event to all subscribers (non-blocking)."""
        self._event_count += 1
        event_dict = event.to_dict()

        dead_queues = []
        for q in self._subscribers:
            try:
                q.put_nowait(event_dict)
            except asyncio.QueueFull:
                dead_queues.append(q)
                logger.warning("Subscriber queue full — dropping")

        # Clean up dead queues
        for q in dead_queues:
            self._subscribers.remove(q)

    # ── Status ───────────────────────────────────────────

    def get_status(self) -> dict:
        """Aggregated feed status. No secrets."""
        return {
            "running": self._running,
            "subscribers": len(self._subscribers),
            "total_events": self._event_count,
            "binance": self.binance.get_status(),
            "polymarket": self.polymarket.get_status(),
            "kalshi": self.kalshi.get_status(),
        }

    @property
    def is_all_connected(self) -> bool:
        return (
            self.binance.is_connected
            and self.polymarket.is_connected
            and self.kalshi.is_running
        )
