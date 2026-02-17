"""
Kalshi Polling Feed — optimized REST polling for Kalshi market data.

Kalshi does not provide a public WebSocket API for market data,
so we use optimized REST polling with configurable intervals.

Features:
- Async HTTP client for non-blocking polls
- Configurable poll interval
- Smart caching (skip poll if data is fresh)
- Callback system matching WebSocket clients
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Dict, List, Optional

import httpx

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class KalshiPollingFeed:
    """
    Optimized REST polling for Kalshi market data.

    Since Kalshi doesn't offer public WebSockets, we poll their REST API
    at configurable intervals with smart caching.

    Security: Uses public market data endpoints only. No auth required.
    """

    def __init__(
        self,
        poll_interval: float = 2.0,
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or get_settings()
        self.poll_interval = poll_interval
        self._api_url = self.settings.KALSHI_API_URL

        # State
        self._latest_data: Optional[dict] = None
        self._last_poll: float = 0.0
        self._running: bool = False
        self._poll_count: int = 0
        self._error_count: int = 0
        self._callbacks: List[Callable] = []
        self._client: Optional[httpx.AsyncClient] = None

    # ── Public Interface ─────────────────────────────────

    def add_callback(self, callback: Callable[[dict], None]) -> None:
        """Register a callback: callback(market_data)."""
        self._callbacks.append(callback)

    @property
    def latest_data(self) -> Optional[dict]:
        return self._latest_data

    @property
    def last_poll(self) -> float:
        return self._last_poll

    @property
    def age_seconds(self) -> float:
        if self._last_poll == 0:
            return float("inf")
        return time.time() - self._last_poll

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Start polling loop."""
        self._running = True
        self._client = httpx.AsyncClient(timeout=10.0)
        logger.info(
            "Starting Kalshi polling feed: interval=%.1fs url=%s",
            self.poll_interval, self._api_url,
        )

        try:
            while self._running:
                await self._poll()
                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            logger.info("Kalshi polling cancelled")
        finally:
            await self._cleanup()

    async def stop(self) -> None:
        """Stop polling loop."""
        self._running = False
        logger.info("Kalshi polling feed stopped")

    async def poll_once(self) -> Optional[dict]:
        """Execute a single poll (for testing)."""
        if not self._client:
            self._client = httpx.AsyncClient(timeout=10.0)
        await self._poll()
        return self._latest_data

    # ── Internal ─────────────────────────────────────────

    async def _poll(self) -> None:
        """Execute one poll cycle."""
        try:
            response = await self._client.get(
                self._api_url,
                params={"status": "open", "series_ticker": "KXBTCD"},
            )
            response.raise_for_status()
            data = response.json()

            self._latest_data = data
            self._last_poll = time.time()
            self._poll_count += 1

            # Fire callbacks
            for cb in self._callbacks:
                try:
                    cb(data)
                except Exception as e:
                    logger.error("Kalshi poll callback error: %s", e)

        except httpx.HTTPStatusError as e:
            self._error_count += 1
            logger.warning("Kalshi poll HTTP error %d: %s", e.response.status_code, str(e)[:80])
        except httpx.RequestError as e:
            self._error_count += 1
            logger.warning("Kalshi poll request error: %s", str(e)[:80])
        except Exception as e:
            self._error_count += 1
            logger.error("Kalshi poll unexpected error: %s", str(e)[:80])

    async def _cleanup(self) -> None:
        """Clean up HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def get_status(self) -> dict:
        """Status for monitoring. No secrets."""
        return {
            "running": self._running,
            "poll_interval": self.poll_interval,
            "last_poll": self._last_poll,
            "age_seconds": round(self.age_seconds, 1) if self._last_poll > 0 else None,
            "poll_count": self._poll_count,
            "error_count": self._error_count,
            "has_data": self._latest_data is not None,
        }
