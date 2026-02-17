"""
Kalshi Authenticated Client — for trade execution on Kalshi.

Implements RSA-PSS request signing as required by Kalshi's API v2.
Supports sandbox (demo.kalshi.com) and production environments.

Usage:
    client = KalshiAuthClient(
        api_key="your-key",
        private_key_path="/path/to/key.pem",
        base_url="https://demo.kalshi.com/trade-api/v2",
    )
    balance = client.get_balance()
    order = client.place_order(ticker="KXBTCD-...", side="yes", ...)
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class KalshiAuthClient:
    """
    Authenticated Kalshi API client with RSA-PSS request signing.

    All trade methods log intent BEFORE execution and respect DRY_RUN.
    """

    # Kalshi API endpoints (relative paths)
    ORDERS_PATH = "/portfolio/orders"
    BALANCE_PATH = "/portfolio/balance"
    POSITIONS_PATH = "/portfolio/positions"
    MARKETS_PATH = "/markets"

    def __init__(
        self,
        api_key: str = "",
        private_key_path: str = "",
        base_url: str = "https://demo.kalshi.com/trade-api/v2",
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or get_settings()
        self.api_key = api_key or self.settings.KALSHI_API_KEY
        self.private_key_path = private_key_path or self.settings.KALSHI_PRIVATE_KEY_PATH
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self._private_key = None

        if self.api_key:
            logger.info("KalshiAuthClient initialized (base=%s)", self.base_url)
        else:
            logger.warning("KalshiAuthClient initialized WITHOUT API key — read-only mode")

    # ── Authentication ───────────────────────────────────

    def _load_private_key(self):
        """Load RSA private key from PEM file (lazy-loaded)."""
        if self._private_key is not None:
            return self._private_key

        if not self.private_key_path:
            raise ValueError("KALSHI_PRIVATE_KEY_PATH not configured")

        try:
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            from cryptography.hazmat.backends import default_backend

            with open(self.private_key_path, "rb") as f:
                self._private_key = load_pem_private_key(f.read(), password=None, backend=default_backend())
            logger.info("Kalshi RSA private key loaded from %s", self.private_key_path)
            return self._private_key

        except FileNotFoundError:
            raise ValueError(f"Kalshi private key file not found: {self.private_key_path}")
        except Exception as e:
            raise ValueError(f"Failed to load Kalshi private key: {e}")

    def _sign_request(self, method: str, path: str, timestamp_ms: str) -> str:
        """
        Generate RSA-PSS signature for Kalshi API authentication.

        Kalshi requires signing: timestamp_ms + method + path
        """
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        key = self._load_private_key()
        message = f"{timestamp_ms}{method}{path}".encode("utf-8")

        signature = key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )

        return base64.b64encode(signature).decode("utf-8")

    def _auth_headers(self, method: str, path: str) -> Dict[str, str]:
        """Build authenticated request headers."""
        timestamp_ms = str(int(time.time() * 1000))
        signature = self._sign_request(method, path, timestamp_ms)

        return {
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _authenticated_request(
        self, method: str, path: str, body: Optional[dict] = None, params: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Make an authenticated request to Kalshi API."""
        url = f"{self.base_url}{path}"
        headers = self._auth_headers(method.upper(), path)

        logger.debug("Kalshi %s %s", method.upper(), path)

        response = self.session.request(
            method=method.upper(),
            url=url,
            headers=headers,
            json=body,
            params=params,
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    # ── Account Info ─────────────────────────────────────

    def get_balance(self) -> Tuple[float, Optional[str]]:
        """
        Get account balance in USD.
        Returns (balance_usd, error_message).
        """
        try:
            data = self._authenticated_request("GET", self.BALANCE_PATH)
            # Kalshi returns balance in cents
            balance_cents = data.get("balance", 0)
            balance_usd = balance_cents / 100.0
            logger.info("Kalshi balance: $%.2f", balance_usd)
            return balance_usd, None
        except Exception as e:
            logger.error("Failed to get Kalshi balance: %s", e)
            return 0.0, str(e)

    def get_positions(self) -> Tuple[List[dict], Optional[str]]:
        """
        Get all open positions.
        Returns (positions_list, error_message).
        """
        try:
            data = self._authenticated_request("GET", self.POSITIONS_PATH)
            positions = data.get("market_positions", [])
            logger.info("Kalshi positions: %d open", len(positions))
            return positions, None
        except Exception as e:
            logger.error("Failed to get Kalshi positions: %s", e)
            return [], str(e)

    # ── Order Management ─────────────────────────────────

    def place_order(
        self,
        ticker: str,
        side: str,        # "yes" or "no"
        action: str,      # "buy" or "sell"
        count: int,       # Number of contracts
        price_cents: int,  # Price in cents (1-99)
        order_type: str = "limit",
        dry_run: Optional[bool] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Place an order on Kalshi.

        SAFETY: Logs full intent BEFORE execution. Respects DRY_RUN.
        Returns (order_response, error_message).
        """
        is_dry_run = dry_run if dry_run is not None else self.settings.DRY_RUN

        order_intent = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "type": order_type,
            "yes_price" if side == "yes" else "no_price": price_cents,
        }

        # ALWAYS log intent before execution
        logger.info(
            "📋 ORDER INTENT | %s | ticker=%s side=%s action=%s count=%d price=%dc type=%s",
            "DRY-RUN" if is_dry_run else "LIVE",
            ticker, side, action, count, price_cents, order_type,
        )

        if is_dry_run:
            logger.info("🔒 DRY-RUN: Order NOT submitted")
            return {
                "dry_run": True,
                "intent": order_intent,
                "timestamp": datetime.utcnow().isoformat(),
            }, None

        try:
            body = {
                "ticker": ticker,
                "action": action,
                "side": side,
                "count": count,
                "type": order_type,
            }
            if side == "yes":
                body["yes_price"] = price_cents
            else:
                body["no_price"] = price_cents

            result = self._authenticated_request("POST", self.ORDERS_PATH, body=body)
            order = result.get("order", {})

            logger.info(
                "✅ ORDER PLACED | order_id=%s status=%s",
                order.get("order_id", "?"),
                order.get("status", "?"),
            )
            return result, None

        except requests.HTTPError as e:
            error_detail = ""
            try:
                error_detail = e.response.json().get("message", str(e))
            except Exception:
                error_detail = str(e)
            logger.error("❌ ORDER FAILED | %s | %s", ticker, error_detail)
            return None, error_detail

        except Exception as e:
            logger.error("❌ ORDER ERROR | %s | %s", ticker, e)
            return None, str(e)

    def cancel_order(self, order_id: str) -> Tuple[Optional[dict], Optional[str]]:
        """Cancel a pending order."""
        try:
            path = f"{self.ORDERS_PATH}/{order_id}"
            result = self._authenticated_request("DELETE", path)
            logger.info("🗑️ ORDER CANCELLED | order_id=%s", order_id)
            return result, None
        except Exception as e:
            logger.error("Failed to cancel order %s: %s", order_id, e)
            return None, str(e)

    def get_order(self, order_id: str) -> Tuple[Optional[dict], Optional[str]]:
        """Get order status by ID."""
        try:
            path = f"{self.ORDERS_PATH}/{order_id}"
            result = self._authenticated_request("GET", path)
            return result.get("order", {}), None
        except Exception as e:
            logger.error("Failed to get order %s: %s", order_id, e)
            return None, str(e)
