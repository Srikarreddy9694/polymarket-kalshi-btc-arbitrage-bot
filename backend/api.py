"""
FastAPI application — refactored to use ArbitrageEngine, platform clients, and Pydantic models.

Routes:
    GET  /arbitrage  — Main arbitrage data (compatible with existing frontend)
    GET  /health     — Health check
    GET  /config     — Current non-secret configuration
    GET  /status     — Engine, circuit breaker, and risk manager status
    GET  /positions  — Open positions summary
    GET  /stream     — SSE real-time event stream
    GET  /latency    — Execution latency statistics
    GET  /streams    — Data feed connection status
    POST /kill-switch — Activate kill switch (authenticated)
    POST /kill-switch/deactivate — Deactivate kill switch (authenticated)
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
from typing import List

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from config.settings import Settings, get_settings
from core.models import ArbitrageCheck, ArbitrageResponse, PolymarketData, KalshiData
from core.arbitrage import ArbitrageEngine
from core.fee_engine import FeeEngine
from clients.polymarket_client import PolymarketClient
from clients.kalshi_client import KalshiClient

from safety.risk_manager import RiskManager
from safety.circuit_breaker import CircuitBreaker
from safety.kill_switch import KillSwitch
from storage.database import Database
from streams.stream_manager import StreamManager
from execution.latency_tracker import LatencyTracker
from monitoring.metrics import MetricsRegistry
from monitoring.telegram_alerts import TelegramAlerts

# Also import the legacy fetchers so the old endpoint still works
# while we migrate the frontend
from fetch_current_polymarket import fetch_polymarket_data_struct
from fetch_current_kalshi import fetch_kalshi_data_struct

# ── Logging ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-25s | %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("api")

# ── Settings & dependencies ──────────────────────────────
settings = get_settings()
fee_engine = FeeEngine(settings=settings)
arb_engine = ArbitrageEngine(fee_engine=fee_engine, settings=settings)
poly_client = PolymarketClient(settings=settings)
kalshi_client = KalshiClient(settings=settings)

# ── Safety systems (Sprint 4) ────────────────────────────
risk_manager = RiskManager(settings=settings)
circuit_breaker = CircuitBreaker(settings=settings)
kill_switch = KillSwitch(settings=settings)
db = Database(db_path=settings.DB_PATH)

# ── Sprint 5: Real-time streams & latency ───────────────
stream_manager = StreamManager()
latency_tracker = LatencyTracker()

# ── Sprint 6: Monitoring & Alerting ──────────────────────
metrics_registry = MetricsRegistry()
telegram = TelegramAlerts(
    bot_token=settings.TELEGRAM_BOT_TOKEN,
    chat_id=settings.TELEGRAM_CHAT_ID,
)

# ── App ──────────────────────────────────────────────────
app = FastAPI(
    title="BTC Arbitrage Bot API",
    description="Polymarket-Kalshi BTC 1hr arbitrage scanner",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Response Models ──────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    timestamp: str
    version: str
    dry_run: bool


class ConfigResponse(BaseModel):
    dry_run: bool
    max_single_trade_usd: float
    max_total_exposure_usd: float
    max_daily_loss_usd: float
    max_trades_per_hour: int
    min_net_margin: float
    kalshi_fee_per_contract: float
    polymarket_gas_cost: float
    slippage_buffer: float
    polling_interval_sec: float


# ── Routes ───────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
def health_check():
    """Health check endpoint for monitoring."""
    return HealthResponse(
        status="ok",
        timestamp=datetime.datetime.utcnow().isoformat(),
        version="2.0.0",
        dry_run=settings.DRY_RUN,
    )


@app.get("/config", response_model=ConfigResponse)
def get_config():
    """Returns current non-secret configuration values."""
    return ConfigResponse(
        dry_run=settings.DRY_RUN,
        max_single_trade_usd=settings.MAX_SINGLE_TRADE_USD,
        max_total_exposure_usd=settings.MAX_TOTAL_EXPOSURE_USD,
        max_daily_loss_usd=settings.MAX_DAILY_LOSS_USD,
        max_trades_per_hour=settings.MAX_TRADES_PER_HOUR,
        min_net_margin=settings.MIN_NET_MARGIN,
        kalshi_fee_per_contract=settings.KALSHI_FEE_PER_CONTRACT,
        polymarket_gas_cost=settings.POLYMARKET_GAS_COST,
        slippage_buffer=settings.SLIPPAGE_BUFFER,
        polling_interval_sec=settings.POLLING_INTERVAL_SEC,
    )


@app.get("/arbitrage")
def get_arbitrage_data():
    """
    Main arbitrage endpoint — backward compatible with existing frontend.

    Uses the legacy fetchers for data (preserving existing logic), but
    delegates arbitrage detection to the new ArbitrageEngine with fee
    adjustments.
    """
    # Fetch data (legacy path — will be replaced with client calls once tested)
    poly_data, poly_err = fetch_polymarket_data_struct()
    kalshi_data, kalshi_err = fetch_kalshi_data_struct()

    response = {
        "timestamp": datetime.datetime.now().isoformat(),
        "polymarket": poly_data,
        "kalshi": kalshi_data,
        "checks": [],
        "opportunities": [],
        "errors": [],
    }

    if poly_err:
        response["errors"].append(poly_err)
        logger.warning("Polymarket fetch error: %s", poly_err)
    if kalshi_err:
        response["errors"].append(kalshi_err)
        logger.warning("Kalshi fetch error: %s", kalshi_err)

    if not poly_data or not kalshi_data:
        return response

    # Normalize to Pydantic models for engine
    try:
        poly_model = PolymarketData(
            price_to_beat=poly_data.get("price_to_beat"),
            current_price=poly_data.get("current_price"),
            prices=poly_data.get("prices", {}),
            slug=poly_data.get("slug", ""),
            target_time_utc=poly_data.get("target_time_utc"),
        )

        from core.models import KalshiMarket

        kalshi_markets = [
            KalshiMarket(**m) for m in kalshi_data.get("markets", [])
        ]
        kalshi_model = KalshiData(
            event_ticker=kalshi_data.get("event_ticker", ""),
            current_price=kalshi_data.get("current_price"),
            markets=kalshi_markets,
        )

        checks, opportunities = arb_engine.find_opportunities(poly_model, kalshi_model)

        # Serialize to dicts for backward-compat JSON response
        response["checks"] = [c.dict() for c in checks]
        response["opportunities"] = [o.dict() for o in opportunities]

        if opportunities:
            logger.info(
                "🎯 %d arbitrage opportunities found! Best net margin: $%.4f",
                len(opportunities),
                max(o.net_margin for o in opportunities),
            )

    except Exception as e:
        logger.error("Arbitrage engine error: %s", e, exc_info=True)
        response["errors"].append(f"Engine error: {str(e)}")

    return response


@app.get("/arbitrage/v2", response_model=ArbitrageResponse)
def get_arbitrage_data_v2():
    """
    V2 arbitrage endpoint — uses new client classes and returns Pydantic models.
    The frontend can migrate to this endpoint when ready.
    """
    errors: List[str] = []

    poly_data, poly_err = poly_client.fetch_data()
    if poly_err:
        errors.append(poly_err)
        logger.warning("Polymarket client error: %s", poly_err)

    kalshi_data, kalshi_err = kalshi_client.fetch_data()
    if kalshi_err:
        errors.append(kalshi_err)
        logger.warning("Kalshi client error: %s", kalshi_err)

    checks = []
    opportunities = []

    if poly_data and kalshi_data:
        try:
            checks, opportunities = arb_engine.find_opportunities(poly_data, kalshi_data)
            if opportunities:
                logger.info(
                    "🎯 V2: %d opportunities | Best: $%.4f net margin",
                    len(opportunities),
                    max(o.net_margin for o in opportunities),
                )
        except Exception as e:
            logger.error("V2 engine error: %s", e, exc_info=True)
            errors.append(f"Engine error: {str(e)}")

    return ArbitrageResponse(
        timestamp=datetime.datetime.utcnow().isoformat(),
        polymarket=poly_data,
        kalshi=kalshi_data,
        checks=checks,
        opportunities=opportunities,
        errors=errors,
    )


# ── Safety Endpoints (Sprint 4) ──────────────────────────

# Secret fields that must NEVER appear in any API response
_SECRET_FIELDS = frozenset({
    "KALSHI_API_KEY", "KALSHI_PRIVATE_KEY_PATH", "POLYMARKET_PRIVATE_KEY",
    "KILL_SWITCH_TOKEN", "password", "secret", "token", "private_key",
})


def _scrub_secrets(data: dict) -> dict:
    """Remove any keys that look like secrets from a dict."""
    return {
        k: v for k, v in data.items()
        if k.upper() not in _SECRET_FIELDS
        and not any(s in k.lower() for s in ("key", "secret", "token", "password", "private"))
    }


def _validate_bearer_token(authorization: str) -> None:
    """Validate bearer token for protected endpoints. Raises HTTPException."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization[len("Bearer "):]
    if not KillSwitch.validate_token(token, settings=settings):
        # SECURITY: do NOT reveal whether the token was wrong or missing
        raise HTTPException(status_code=403, detail="Forbidden")


@app.get("/status")
def get_status():
    """Full system status — engine, risk manager, circuit breaker."""
    return {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "dry_run": settings.DRY_RUN,
        "risk_manager": risk_manager.get_status(),
        "circuit_breaker": circuit_breaker.get_status(),
        "kill_switch": kill_switch.get_status(),
        "database": db.get_stats(),
    }


@app.get("/positions")
def get_positions():
    """Open positions across both platforms."""
    return {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "open_positions": db.get_open_positions(),
        "total_exposure": db.get_total_open_exposure(),
    }


@app.post("/kill-switch")
def activate_kill_switch(
    request: Request,
    authorization: str = Header(default=""),
):
    """
    Activate the kill switch. Requires bearer token authentication.
    
    SECURITY:
    - Token validated with constant-time comparison
    - No token values logged
    - Immediate halt of all trading
    """
    _validate_bearer_token(authorization)

    reason = "API kill switch activated"
    kill_switch.activate(reason=reason)
    risk_manager.halt(reason=reason)
    circuit_breaker.trip(reason=reason)
    db.log_event("kill_switch", reason, severity="critical")

    logger.critical("🛑 KILL SWITCH ACTIVATED via API")
    return {"status": "activated", "timestamp": datetime.datetime.utcnow().isoformat()}


@app.post("/kill-switch/deactivate")
def deactivate_kill_switch(
    request: Request,
    authorization: str = Header(default=""),
):
    """
    Deactivate the kill switch. Requires bearer token authentication.
    """
    _validate_bearer_token(authorization)

    kill_switch.deactivate(reason="API deactivation")
    risk_manager.resume(reason="kill switch deactivated")
    circuit_breaker.reset()
    db.log_event("kill_switch", "deactivated via API", severity="info")

    logger.info("▶️ Kill switch deactivated via API")
    return {"status": "deactivated", "timestamp": datetime.datetime.utcnow().isoformat()}


# ── Sprint 5: SSE + Latency + Streams Endpoints ────────

@app.get("/stream")
async def stream_events(request: Request):
    """
    Server-Sent Events endpoint for real-time data.

    The frontend connects here to receive live price updates,
    order book changes, and arbitrage opportunities as they happen.
    """
    subscriber_queue = stream_manager.subscribe()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        subscriber_queue.get(), timeout=30.0,
                    )
                    yield {
                        "event": event.get("event_type", "update"),
                        "data": json.dumps(event),
                    }
                except asyncio.TimeoutError:
                    # Send keepalive ping every 30 seconds
                    yield {"event": "ping", "data": "{}"}
        finally:
            stream_manager.unsubscribe(subscriber_queue)

    return EventSourceResponse(event_generator())


@app.get("/latency")
def get_latency():
    """Execution latency statistics (P50/P95/P99)."""
    return {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        **latency_tracker.get_status(),
        "recent": latency_tracker.get_recent(n=5),
    }


@app.get("/streams")
def get_streams_status():
    """Data feed connection status."""
    return {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        **stream_manager.get_status(),
    }


# ── Sprint 6: Monitoring Endpoints ──────────────────────

@app.get("/metrics")
def prometheus_metrics():
    """Prometheus metrics endpoint for scraping."""
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(
        content=metrics_registry.render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/alerts")
def get_alerts_status():
    """Telegram alerts status."""
    return {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        **telegram.get_status(),
    }


# ── Entry point ──────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    # Switch to JSON logging in production
    if settings.LOG_FORMAT == "json":
        from monitoring.json_logger import setup_json_logging
        setup_json_logging(environment=settings.ENVIRONMENT)

    logger.info("Starting API server on %s:%d (DRY_RUN=%s)", settings.API_HOST, settings.API_PORT, settings.DRY_RUN)
    uvicorn.run(app, host=settings.API_HOST, port=settings.API_PORT)
