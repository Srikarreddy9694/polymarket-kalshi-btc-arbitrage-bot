"""
Application settings loaded from environment variables and .env file.

Usage:
    from config import get_settings
    settings = get_settings()
"""

from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central configuration for the arbitrage bot."""

    # --- API URLs ---
    POLYMARKET_GAMMA_URL: str = "https://gamma-api.polymarket.com/events"
    POLYMARKET_CLOB_URL: str = "https://clob.polymarket.com/book"
    KALSHI_API_URL: str = "https://api.elections.kalshi.com/trade-api/v2/markets"
    BINANCE_PRICE_URL: str = "https://api.binance.com/api/v3/ticker/price"
    BINANCE_KLINES_URL: str = "https://api.binance.com/api/v3/klines"
    BINANCE_SYMBOL: str = "BTCUSDT"

    # --- Trading Credentials (loaded from .env) ---
    KALSHI_API_KEY: str = ""
    KALSHI_PRIVATE_KEY_PATH: str = ""
    POLYMARKET_PRIVATE_KEY: str = ""

    # --- Security ---
    KILL_SWITCH_TOKEN: str = ""  # Bearer token for kill switch API (fail-closed if empty)
    DB_PATH: str = "data/arbitrage_bot.db"

    # --- Monitoring & Alerts (Sprint 6) ---
    TELEGRAM_BOT_TOKEN: str = ""  # Telegram bot token (leave empty to disable)
    TELEGRAM_CHAT_ID: str = ""   # Telegram chat ID for alerts
    LOG_FORMAT: str = "text"      # "text" or "json" (use "json" in production)
    ENVIRONMENT: str = "development"  # "development", "staging", "production"

    # --- Trading Parameters ---
    DRY_RUN: bool = True  # SAFE DEFAULT — no live trades
    MAX_SINGLE_TRADE_USD: float = 50.0
    MAX_TOTAL_EXPOSURE_USD: float = 500.0
    MAX_DAILY_LOSS_USD: float = 100.0
    MAX_TRADES_PER_HOUR: int = 20
    MIN_NET_MARGIN: float = 0.02  # Minimum $0.02 profit after fees

    # --- Fee Configuration ---
    KALSHI_FEE_PER_CONTRACT: float = 0.03  # Worst-case winning fee
    POLYMARKET_GAS_COST: float = 0.002  # Approximate gas per trade
    SLIPPAGE_BUFFER: float = 0.005  # Safety margin (0.5%)

    # --- Server ---
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000
    CORS_ORIGINS: List[str] = ["http://localhost:3000"]
    POLLING_INTERVAL_SEC: float = 1.0

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


@lru_cache
def get_settings() -> Settings:
    """Returns cached settings instance (singleton)."""
    return Settings()
