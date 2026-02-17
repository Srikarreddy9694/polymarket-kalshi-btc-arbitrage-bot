"""
Pydantic data models for the arbitrage bot.

These models provide type safety, validation, and serialization
for all data flowing through the system.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# --- Market Data Models ---

class PolymarketData(BaseModel):
    """Normalized data from Polymarket for one hourly BTC market."""
    price_to_beat: Optional[float] = Field(None, description="Binance open price for the hour (strike)")
    current_price: Optional[float] = Field(None, description="Current BTC price from Binance")
    prices: Dict[str, float] = Field(default_factory=dict, description="Contract prices, e.g. {'Up': 0.60, 'Down': 0.40}")
    slug: str = Field("", description="Polymarket event slug")
    target_time_utc: Optional[datetime] = Field(None, description="Market expiry time in UTC")


class KalshiMarket(BaseModel):
    """A single Kalshi binary option contract at a specific strike."""
    strike: float = Field(..., description="Strike price in USD")
    yes_bid: int = Field(0, description="Best bid for Yes in cents")
    yes_ask: int = Field(0, description="Best ask for Yes in cents")
    no_bid: int = Field(0, description="Best bid for No in cents")
    no_ask: int = Field(0, description="Best ask for No in cents")
    subtitle: str = Field("", description="Human-readable market description")


class KalshiData(BaseModel):
    """Collection of Kalshi binary option markets for an event."""
    event_ticker: str = Field("", description="Kalshi event ticker")
    current_price: Optional[float] = Field(None, description="Current BTC price from Binance")
    markets: List[KalshiMarket] = Field(default_factory=list)


# --- Arbitrage Models ---

class ArbitrageCheck(BaseModel):
    """Result of checking a single arbitrage strategy pair."""
    kalshi_strike: float
    kalshi_yes: float = Field(0.0, description="Kalshi Yes cost in dollars")
    kalshi_no: float = Field(0.0, description="Kalshi No cost in dollars")
    type: str = Field("", description="Comparison type: 'Poly > Kalshi', 'Poly < Kalshi', 'Equal'")
    poly_leg: str = Field("", description="Which Polymarket contract to buy: 'Up' or 'Down'")
    kalshi_leg: str = Field("", description="Which Kalshi contract to buy: 'Yes' or 'No'")
    poly_cost: float = Field(0.0, description="Cost of Polymarket leg in dollars")
    kalshi_cost: float = Field(0.0, description="Cost of Kalshi leg in dollars")
    total_cost: float = Field(0.0, description="Raw total cost (before fees)")
    fee_adjusted_cost: float = Field(0.0, description="Total cost including fees and slippage")
    is_arbitrage: bool = Field(False, description="True if fee-adjusted cost < $1.00")
    margin: float = Field(0.0, description="Raw margin before fees")
    net_margin: float = Field(0.0, description="Net margin after fees and slippage")


class ArbitrageResponse(BaseModel):
    """Full API response for the /arbitrage endpoint."""
    timestamp: str
    polymarket: Optional[PolymarketData] = None
    kalshi: Optional[KalshiData] = None
    checks: List[ArbitrageCheck] = Field(default_factory=list)
    opportunities: List[ArbitrageCheck] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


# --- Trade Execution Models (Sprint 3) ---

class TradeIntent(BaseModel):
    """A trade that the engine intends to execute."""
    opportunity: ArbitrageCheck
    size_contracts: int = Field(1, description="Number of contracts to trade")
    size_usd: float = Field(0.0, description="Total USD value of the trade")
    dry_run: bool = Field(True, description="If True, log but don't execute")


class TradeResult(BaseModel):
    """Result of an executed or attempted trade."""
    intent: TradeIntent
    success: bool = False
    poly_order_id: Optional[str] = None
    kalshi_order_id: Optional[str] = None
    poly_fill_price: Optional[float] = None
    kalshi_fill_price: Optional[float] = None
    actual_total_cost: Optional[float] = None
    actual_margin: Optional[float] = None
    error: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
