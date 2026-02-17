"""
Polymarket data client — fetches market data and order book depth.

This client handles:
- Event lookup via Gamma API
- Order book retrieval via CLOB API
- Order book depth analysis (fillable amount at target price)
"""

from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional, Tuple

import requests

from clients.base import BaseClient
from clients.binance_client import BinanceClient
from core.models import PolymarketData
from config.settings import Settings

logger = logging.getLogger(__name__)


class OrderBookLevel:
    """A single price level in the order book."""

    __slots__ = ("price", "size")

    def __init__(self, price: float, size: float):
        self.price = price
        self.size = size

    def __repr__(self) -> str:
        return f"OrderBookLevel(price={self.price}, size={self.size})"


class OrderBook:
    """Full order book for a Polymarket token."""

    def __init__(self, bids: List[OrderBookLevel], asks: List[OrderBookLevel]):
        self.bids = sorted(bids, key=lambda x: x.price, reverse=True)  # highest first
        self.asks = sorted(asks, key=lambda x: x.price)  # lowest first

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 0.0

    @property
    def spread(self) -> float:
        if self.best_ask > 0 and self.best_bid > 0:
            return self.best_ask - self.best_bid
        return 0.0

    @property
    def mid_price(self) -> float:
        if self.best_ask > 0 and self.best_bid > 0:
            return (self.best_ask + self.best_bid) / 2.0
        return self.best_ask or self.best_bid

    def fillable_amount(self, side: str, max_price: float, max_usd: float) -> Tuple[float, float]:
        """
        Calculate how many contracts can be filled at or below max_price.

        Args:
            side: "BUY" — walk the asks; "SELL" — walk the bids
            max_price: Maximum price willing to pay (for BUY) or minimum (for SELL)
            max_usd: Maximum USD to spend

        Returns:
            (total_contracts, total_cost)
        """
        levels = self.asks if side == "BUY" else self.bids
        total_contracts = 0.0
        total_cost = 0.0

        for level in levels:
            if side == "BUY" and level.price > max_price:
                break
            if side == "SELL" and level.price < max_price:
                break

            remaining_budget = max_usd - total_cost
            if remaining_budget <= 0:
                break

            affordable_contracts = remaining_budget / level.price
            fill = min(level.size, affordable_contracts)
            total_contracts += fill
            total_cost += fill * level.price

        return total_contracts, total_cost

    def total_ask_liquidity(self, max_price: float = 1.0) -> float:
        """Total contracts available for buying at or below max_price."""
        return sum(l.size for l in self.asks if l.price <= max_price)

    def total_bid_liquidity(self, min_price: float = 0.0) -> float:
        """Total contracts available for selling at or above min_price."""
        return sum(l.size for l in self.bids if l.price >= min_price)


class PolymarketClient(BaseClient):
    """
    Fetches Polymarket data:
    - Event details from Gamma API
    - Order book from CLOB API
    - BTC price from Binance
    """

    def __init__(self, settings: Optional[Settings] = None, binance: Optional[BinanceClient] = None):
        super().__init__(settings)
        self.binance = binance or BinanceClient(self.settings)

    # --- Public API ---

    def fetch_data(self) -> Tuple[Optional[PolymarketData], Optional[str]]:
        """Fetch Polymarket data for the current hour's market."""
        try:
            from get_current_markets import get_current_market_urls

            market_info = get_current_market_urls()
            polymarket_url = market_info["polymarket"]
            target_time_utc = market_info["target_time_utc"]

            slug = polymarket_url.split("/")[-1]
            return self.fetch_by_slug(slug, target_time_utc)

        except Exception as e:
            self.logger.error("Failed to fetch Polymarket data: %s", e, exc_info=True)
            return None, str(e)

    def fetch_by_slug(self, slug: str, target_time_utc=None) -> Tuple[Optional[PolymarketData], Optional[str]]:
        """Fetch Polymarket data for a specific slug."""
        try:
            prices, orderbooks, err = self._get_market_prices(slug)
            if err:
                return None, f"Polymarket Error: {err}"

            current_price, _ = self.binance.get_current_price()
            price_to_beat = None
            if target_time_utc:
                price_to_beat, _ = self.binance.get_open_price(target_time_utc)

            data = PolymarketData(
                price_to_beat=price_to_beat,
                current_price=current_price,
                prices=prices,
                slug=slug,
                target_time_utc=target_time_utc,
            )
            return data, None

        except Exception as e:
            self.logger.error("fetch_by_slug failed: %s", e, exc_info=True)
            return None, str(e)

    def get_order_book(self, token_id: str) -> Optional[OrderBook]:
        """
        Fetch the full order book for a Polymarket token.
        Returns an OrderBook object with depth analysis capabilities.
        """
        try:
            data = self._get(
                self.settings.POLYMARKET_CLOB_URL,
                params={"token_id": token_id},
            )

            bids = [
                OrderBookLevel(price=float(b["price"]), size=float(b["size"]))
                for b in data.get("bids", [])
            ]
            asks = [
                OrderBookLevel(price=float(a["price"]), size=float(a["size"]))
                for a in data.get("asks", [])
            ]

            book = OrderBook(bids=bids, asks=asks)
            self.logger.debug(
                "Order book for %s: bid=%.3f ask=%.3f spread=%.4f depth=%d/%d",
                token_id[:8], book.best_bid, book.best_ask, book.spread,
                len(bids), len(asks),
            )
            return book

        except Exception as e:
            self.logger.error("Order book fetch failed for %s: %s", token_id, e)
            return None

    # --- Internal ---

    def _get_market_prices(self, slug: str) -> Tuple[Dict[str, float], Dict[str, Optional[OrderBook]], Optional[str]]:
        """
        Fetch event, extract token IDs, retrieve order books, return best ask prices.
        Also returns the full order books for depth analysis.
        """
        try:
            data = self._get(
                self.settings.POLYMARKET_GAMMA_URL,
                params={"slug": slug},
            )

            if not data:
                return {}, {}, "Event not found"

            event = data[0]
            markets = event.get("markets", [])
            if not markets:
                return {}, {}, "Markets not found in event"

            market = markets[0]
            clob_token_ids = json.loads(market.get("clobTokenIds", "[]"))
            outcomes = json.loads(market.get("outcomes", "[]"))

            if len(clob_token_ids) != 2:
                return {}, {}, "Unexpected number of tokens"

            prices = {}
            orderbooks = {}

            for outcome, token_id in zip(outcomes, clob_token_ids):
                book = self.get_order_book(token_id)
                orderbooks[outcome] = book
                prices[outcome] = book.best_ask if book else 0.0

            return prices, orderbooks, None

        except requests.RequestException as e:
            self.logger.error("Polymarket API error: %s", e)
            return {}, {}, str(e)
