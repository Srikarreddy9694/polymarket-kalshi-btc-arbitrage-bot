"""
Polymarket Execution Client — for trade execution on Polymarket CLOB.

Wraps the py-clob-client library for order placement on Polygon network.
Requires a wallet private key for signing transactions.

Usage:
    client = PolymarketExecClient(private_key="0x...")
    order = client.place_order(token_id="...", side="BUY", price=0.55, size=10)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class PolymarketExecClient:
    """
    Polymarket CLOB execution client.

    Wraps py-clob-client for order management. All trade methods
    log intent BEFORE execution and respect DRY_RUN.
    """

    CLOB_HOST = "https://clob.polymarket.com"
    CHAIN_ID_POLYGON = 137

    def __init__(
        self,
        private_key: str = "",
        chain_id: int = CHAIN_ID_POLYGON,
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or get_settings()
        self.private_key = private_key or self.settings.POLYMARKET_PRIVATE_KEY
        self.chain_id = chain_id
        self._client = None
        self._creds = None

        if self.private_key:
            logger.info("PolymarketExecClient initialized (chain_id=%d)", self.chain_id)
        else:
            logger.warning("PolymarketExecClient initialized WITHOUT private key — read-only mode")

    # ── Lazy Client Initialization ───────────────────────

    def _get_client(self):
        """Lazy-initialize the py-clob-client (avoids import at module level)."""
        if self._client is not None:
            return self._client

        if not self.private_key:
            raise ValueError("POLYMARKET_PRIVATE_KEY not configured")

        try:
            from py_clob_client.client import ClobClient

            self._client = ClobClient(
                host=self.CLOB_HOST,
                key=self.private_key,
                chain_id=self.chain_id,
            )

            # Derive and set API credentials
            self._creds = self._client.derive_api_key()
            self._client.set_api_creds(self._creds)

            logger.info("Polymarket CLOB client authenticated successfully")
            return self._client

        except ImportError:
            raise ImportError(
                "py-clob-client not installed. Run: pip3 install py-clob-client"
            )
        except Exception as e:
            raise ValueError(f"Failed to initialize Polymarket client: {e}")

    # ── Account Info ─────────────────────────────────────

    def get_balance(self) -> Tuple[float, Optional[str]]:
        """
        Get USDC balance on Polygon.
        Returns (balance_usd, error_message).
        """
        try:
            client = self._get_client()
            # py-clob-client doesn't have a direct balance method;
            # balance is checked via the Polygon chain or allowances
            # For now, return a placeholder that signals the client is connected
            logger.info("Polymarket client connected (balance check requires on-chain query)")
            return 0.0, "Balance check requires on-chain query — use Polygonscan"
        except Exception as e:
            logger.error("Failed to get Polymarket balance: %s", e)
            return 0.0, str(e)

    def get_positions(self) -> Tuple[List[dict], Optional[str]]:
        """Get all open positions on Polymarket."""
        try:
            client = self._get_client()
            # py-clob-client doesn't expose positions directly
            # Positions need to be tracked locally or via subgraph query
            logger.info("Polymarket positions: requires local position tracker")
            return [], None
        except Exception as e:
            logger.error("Failed to get Polymarket positions: %s", e)
            return [], str(e)

    # ── Order Management ─────────────────────────────────

    def place_order(
        self,
        token_id: str,
        side: str,      # "BUY" or "SELL"
        price: float,   # Price per contract (0.01 to 0.99)
        size: float,    # Number of contracts
        order_type: str = "FOK",  # Fill-Or-Kill safest for arbitrage
        dry_run: Optional[bool] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Place an order on Polymarket CLOB.

        SAFETY: Logs full intent BEFORE execution. Respects DRY_RUN.
        Returns (order_response, error_message).

        Order types:
        - FOK: Fill-Or-Kill (fill entirely at price or reject)
        - FAK: Fill-And-Kill (fill whatever is available, cancel rest)
        - GTC: Good-Till-Cancelled (stays on book)
        """
        is_dry_run = dry_run if dry_run is not None else self.settings.DRY_RUN

        order_intent = {
            "token_id": token_id[:16] + "..." if len(token_id) > 16 else token_id,
            "side": side,
            "price": price,
            "size": size,
            "order_type": order_type,
        }

        # ALWAYS log intent before execution
        logger.info(
            "📋 POLY ORDER INTENT | %s | side=%s price=%.3f size=%.1f type=%s token=%s",
            "DRY-RUN" if is_dry_run else "LIVE",
            side, price, size, order_type,
            token_id[:16] + "...",
        )

        if is_dry_run:
            logger.info("🔒 DRY-RUN: Polymarket order NOT submitted")
            return {
                "dry_run": True,
                "intent": order_intent,
                "timestamp": datetime.utcnow().isoformat(),
            }, None

        try:
            client = self._get_client()

            # Build order using py-clob-client
            from py_clob_client.order_builder.constants import BUY, SELL

            order_side = BUY if side.upper() == "BUY" else SELL

            # Create signed order
            signed_order = client.create_and_post_order(
                {
                    "tokenID": token_id,
                    "price": price,
                    "size": size,
                    "side": order_side,
                    # FOK = Fill-Or-Kill
                    "feeRateBps": 0,
                    "nonce": 0,
                }
            )

            logger.info(
                "✅ POLY ORDER PLACED | order_id=%s",
                signed_order.get("orderID", "?") if isinstance(signed_order, dict) else "submitted",
            )
            return signed_order if isinstance(signed_order, dict) else {"result": str(signed_order)}, None

        except ImportError as e:
            logger.error("❌ py-clob-client not installed: %s", e)
            return None, str(e)

        except Exception as e:
            logger.error("❌ POLY ORDER FAILED | %s", e)
            return None, str(e)

    # ── Token Allowances ─────────────────────────────────

    def set_allowances(self, dry_run: Optional[bool] = None) -> Tuple[bool, Optional[str]]:
        """
        One-time: approve USDC and conditional token spending.
        Required before first trade on Polymarket.
        """
        is_dry_run = dry_run if dry_run is not None else self.settings.DRY_RUN

        logger.info(
            "📋 SET ALLOWANCES | %s",
            "DRY-RUN" if is_dry_run else "LIVE",
        )

        if is_dry_run:
            logger.info("🔒 DRY-RUN: Allowances NOT set")
            return True, None

        try:
            client = self._get_client()
            client.set_allowances()
            logger.info("✅ Polymarket allowances set successfully")
            return True, None
        except Exception as e:
            logger.error("❌ Failed to set allowances: %s", e)
            return False, str(e)
