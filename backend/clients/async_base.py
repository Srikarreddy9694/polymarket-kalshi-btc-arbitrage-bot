"""
Async Base Client — shared HTTP foundation for all async platform clients.

Uses httpx.AsyncClient for non-blocking HTTP with:
- Connection pooling (keep-alive)
- Automatic retries with backoff
- Request/response timing
- Clean shutdown

Security: No secrets logged. Connection pool shared to reduce latency.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

import httpx

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class AsyncBaseClient:
    """
    Shared async HTTP client with connection pooling.

    All platform-specific async clients inherit from this.
    Pre-authenticated sessions with keep-alive reduce latency.
    """

    def __init__(
        self,
        base_url: str = "",
        timeout: float = 10.0,
        max_retries: int = 2,
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or get_settings()
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries

        # Metrics
        self._request_count: int = 0
        self._error_count: int = 0
        self._total_latency_ms: float = 0.0

        # Client (lazy init)
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy-initialize the HTTP client with connection pooling."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout),
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                ),
                http2=False,  # Most exchange APIs don't support HTTP/2
            )
        return self._client

    async def get(self, path: str, params: Optional[Dict] = None, **kwargs: Any) -> httpx.Response:
        """Async GET with timing and retry."""
        return await self._request("GET", path, params=params, **kwargs)

    async def post(self, path: str, json: Optional[Dict] = None, **kwargs: Any) -> httpx.Response:
        """Async POST with timing and retry."""
        return await self._request("POST", path, json=json, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        """Async DELETE with timing and retry."""
        return await self._request("DELETE", path, **kwargs)

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Execute request with retry and latency tracking."""
        client = await self._get_client()
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                start = time.time()
                response = await client.request(method, path, **kwargs)
                elapsed_ms = (time.time() - start) * 1000

                self._request_count += 1
                self._total_latency_ms += elapsed_ms

                return response

            except httpx.RequestError as e:
                last_error = e
                self._error_count += 1
                if attempt < self.max_retries:
                    delay = (2 ** attempt) * 0.1  # 0.1s, 0.2s, 0.4s
                    logger.warning(
                        "Request %s %s failed (attempt %d/%d): %s — retrying in %.1fs",
                        method, path, attempt + 1, self.max_retries + 1,
                        str(e)[:60], delay,
                    )
                    await asyncio.sleep(delay)

        raise last_error  # type: ignore[misc]

    async def close(self) -> None:
        """Close the HTTP client and release connections."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def avg_latency_ms(self) -> Optional[float]:
        """Average request latency in milliseconds."""
        if self._request_count == 0:
            return None
        return round(self._total_latency_ms / self._request_count, 1)

    def get_status(self) -> dict:
        """Status for monitoring. No secrets."""
        return {
            "base_url": self.base_url,
            "request_count": self._request_count,
            "error_count": self._error_count,
            "avg_latency_ms": self.avg_latency_ms,
            "client_initialized": self._client is not None,
        }


class AsyncBinanceClient(AsyncBaseClient):
    """
    Async Binance REST client for supplementary requests.

    The primary BTC price comes from BinanceWebSocket.
    This client handles REST fallback and historical data.
    """

    def __init__(self, settings: Optional[Settings] = None):
        super().__init__(
            base_url="https://api.binance.com",
            timeout=5.0,
            settings=settings,
        )

    async def get_price(self, symbol: str = "BTCUSDT") -> Optional[float]:
        """Get current price via REST (fallback for WebSocket)."""
        try:
            response = await self.get(
                "/api/v3/ticker/price",
                params={"symbol": symbol},
            )
            response.raise_for_status()
            return float(response.json()["price"])
        except Exception as e:
            logger.warning("Async Binance price fetch failed: %s", str(e)[:80])
            return None

    async def get_klines(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "1h",
        limit: int = 24,
    ) -> list:
        """Get recent kline/candlestick data."""
        try:
            response = await self.get(
                "/api/v3/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning("Async Binance klines fetch failed: %s", str(e)[:80])
            return []
