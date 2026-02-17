"""
SQLite Database — persistent storage for trades, positions, and events.

Schema:
- trades: Every executed or attempted trade
- positions: Open and closed positions
- opportunities: Every detected opportunity (executed or not)
- bot_events: Circuit breaker trips, kill switch, errors, etc.

Security:
- Database file is in a non-web-accessible directory
- No secrets are stored (API keys, private keys)
- All timestamps are UTC
- SQL injection prevented via parameterized queries only
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Default DB path — relative to backend/
DEFAULT_DB_PATH = "data/arbitrage_bot.db"

# Schema version for migrations
SCHEMA_VERSION = 1

SCHEMA_SQL = """
-- Trades table: every executed or attempted trade
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    poly_leg        TEXT NOT NULL,           -- 'Up' or 'Down'
    kalshi_leg      TEXT NOT NULL,           -- 'Yes' or 'No'
    kalshi_strike   REAL NOT NULL,
    poly_cost       REAL NOT NULL,
    kalshi_cost     REAL NOT NULL,
    total_cost      REAL NOT NULL,
    fee_adjusted_cost REAL NOT NULL DEFAULT 0.0,
    net_margin      REAL NOT NULL DEFAULT 0.0,
    size_contracts  INTEGER NOT NULL DEFAULT 1,
    poly_fill_price REAL,                   -- Actual fill (NULL if unfilled)
    kalshi_fill_price REAL,
    actual_pnl      REAL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending, filled, failed, unwound, dry_run
    error_message   TEXT,
    dry_run         INTEGER NOT NULL DEFAULT 1  -- 1=true, 0=false
);

-- Positions table: open and settled positions
CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     TEXT UNIQUE NOT NULL,    -- POS-000001
    platform        TEXT NOT NULL,           -- 'kalshi' or 'polymarket'
    side            TEXT NOT NULL,           -- 'long' or 'short'
    ticker          TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    size            INTEGER NOT NULL,
    cost_usd        REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open',  -- open, settled, unwound
    linked_position TEXT,                   -- position_id of paired leg
    arb_id          TEXT,                   -- ARB-000001
    opened_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    closed_at       TEXT
);

-- Opportunities table: every detected opportunity
CREATE TABLE IF NOT EXISTS opportunities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    kalshi_strike   REAL NOT NULL,
    poly_leg        TEXT NOT NULL,
    kalshi_leg      TEXT NOT NULL,
    poly_cost       REAL NOT NULL,
    kalshi_cost     REAL NOT NULL,
    total_cost      REAL NOT NULL,
    net_margin      REAL NOT NULL,
    was_executed    INTEGER NOT NULL DEFAULT 0,  -- 1=yes, 0=no
    skip_reason     TEXT                    -- NULL if executed, reason if skipped
);

-- Bot events table: circuit breaker, kill switch, errors, etc.
CREATE TABLE IF NOT EXISTS bot_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    event_type      TEXT NOT NULL,           -- 'circuit_breaker', 'kill_switch', 'error', 'info'
    severity        TEXT NOT NULL DEFAULT 'info',  -- 'info', 'warning', 'critical'
    details         TEXT NOT NULL
);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version         INTEGER NOT NULL,
    applied_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_platform ON positions(platform);
CREATE INDEX IF NOT EXISTS idx_opportunities_timestamp ON opportunities(timestamp);
CREATE INDEX IF NOT EXISTS idx_bot_events_type ON bot_events(event_type);
CREATE INDEX IF NOT EXISTS idx_bot_events_timestamp ON bot_events(timestamp);
"""


class Database:
    """
    SQLite database for persistent trade and event storage.

    SECURITY:
    - All queries use parameterized statements (no string formatting)
    - No secrets are stored in the database
    - Database file path is configurable (default: data/arbitrage_bot.db)
    - WAL mode for concurrent read/write safety
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._ensure_directory()
        self._init_db()
        logger.info("Database initialized at %s (schema v%d)", self.db_path, SCHEMA_VERSION)

    def _ensure_directory(self) -> None:
        """Create the data directory if it doesn't exist."""
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

    def _init_db(self) -> None:
        """Create tables and apply schema."""
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)

            # Check and record schema version
            cursor = conn.execute("SELECT MAX(version) FROM schema_version")
            row = cursor.fetchone()
            current = row[0] if row[0] is not None else 0

            if current < SCHEMA_VERSION:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )

    @contextmanager
    def _connect(self):
        """Context manager for database connections with WAL mode."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Trades ───────────────────────────────────────────

    def record_trade(
        self,
        poly_leg: str,
        kalshi_leg: str,
        kalshi_strike: float,
        poly_cost: float,
        kalshi_cost: float,
        total_cost: float,
        fee_adjusted_cost: float = 0.0,
        net_margin: float = 0.0,
        size_contracts: int = 1,
        status: str = "pending",
        dry_run: bool = True,
        error_message: Optional[str] = None,
    ) -> int:
        """Record a trade attempt. Returns the trade ID."""
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO trades
                   (poly_leg, kalshi_leg, kalshi_strike, poly_cost, kalshi_cost,
                    total_cost, fee_adjusted_cost, net_margin, size_contracts,
                    status, dry_run, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (poly_leg, kalshi_leg, kalshi_strike, poly_cost, kalshi_cost,
                 total_cost, fee_adjusted_cost, net_margin, size_contracts,
                 status, 1 if dry_run else 0, error_message),
            )
            trade_id = cursor.lastrowid
            logger.debug("Trade recorded: id=%d status=%s", trade_id, status)
            return trade_id

    def update_trade_status(self, trade_id: int, status: str, error: Optional[str] = None) -> None:
        """Update a trade's status."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE trades SET status = ?, error_message = ? WHERE id = ?",
                (status, error, trade_id),
            )

    def get_trades_today(self) -> List[dict]:
        """Get all trades from today (UTC)."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM trades WHERE timestamp >= ? ORDER BY timestamp DESC",
                (today,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_daily_pnl(self) -> float:
        """Sum of actual_pnl for today's trades."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT COALESCE(SUM(actual_pnl), 0.0) FROM trades WHERE timestamp >= ? AND actual_pnl IS NOT NULL",
                (today,),
            )
            return cursor.fetchone()[0]

    # ── Positions ────────────────────────────────────────

    def record_position(
        self,
        position_id: str,
        platform: str,
        side: str,
        ticker: str,
        entry_price: float,
        size: int,
        cost_usd: float,
        linked_position: Optional[str] = None,
        arb_id: Optional[str] = None,
    ) -> None:
        """Record a new open position."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO positions
                   (position_id, platform, side, ticker, entry_price, size,
                    cost_usd, linked_position, arb_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (position_id, platform, side, ticker, entry_price, size,
                 cost_usd, linked_position, arb_id),
            )

    def close_position(self, position_id: str, status: str = "settled") -> None:
        """Mark a position as closed."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE positions SET status = ?, closed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE position_id = ?",
                (status, position_id),
            )

    def get_open_positions(self) -> List[dict]:
        """Get all open positions."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM positions WHERE status = 'open' ORDER BY opened_at DESC",
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_total_open_exposure(self) -> float:
        """Total USD in open positions."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0.0) FROM positions WHERE status = 'open'",
            )
            return cursor.fetchone()[0]

    # ── Opportunities ────────────────────────────────────

    def record_opportunity(
        self,
        kalshi_strike: float,
        poly_leg: str,
        kalshi_leg: str,
        poly_cost: float,
        kalshi_cost: float,
        total_cost: float,
        net_margin: float,
        was_executed: bool = False,
        skip_reason: Optional[str] = None,
    ) -> int:
        """Record a detected opportunity. Returns the ID."""
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO opportunities
                   (kalshi_strike, poly_leg, kalshi_leg, poly_cost, kalshi_cost,
                    total_cost, net_margin, was_executed, skip_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (kalshi_strike, poly_leg, kalshi_leg, poly_cost, kalshi_cost,
                 total_cost, net_margin, 1 if was_executed else 0, skip_reason),
            )
            return cursor.lastrowid

    # ── Bot Events ───────────────────────────────────────

    def log_event(self, event_type: str, details: str, severity: str = "info") -> int:
        """
        Log a bot event to the database.
        SECURITY: caller must ensure no secrets in details string.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO bot_events (event_type, details, severity) VALUES (?, ?, ?)",
                (event_type, details, severity),
            )
            return cursor.lastrowid

    def get_recent_events(self, limit: int = 50, event_type: Optional[str] = None) -> List[dict]:
        """Get recent bot events, optionally filtered by type."""
        with self._connect() as conn:
            if event_type:
                cursor = conn.execute(
                    "SELECT * FROM bot_events WHERE event_type = ? ORDER BY timestamp DESC LIMIT ?",
                    (event_type, limit),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM bot_events ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                )
            return [dict(row) for row in cursor.fetchall()]

    def get_events(self, event_type: Optional[str] = None, days: int = 0) -> List[dict]:
        """
        Get bot events, optionally filtered by type and time window.

        Args:
            event_type: Filter by event type (e.g. 'paper_opportunity')
            days: Only return events from the last N days (0 = all time)

        Returns events in chronological order (oldest first).
        """
        with self._connect() as conn:
            conditions = []
            params: list = []

            if event_type:
                conditions.append("event_type = ?")
                params.append(event_type)

            if days > 0:
                cutoff = datetime.utcnow().strftime("%Y-%m-%d")
                conditions.append("timestamp >= date(?, ?)")
                params.extend([cutoff, f"-{days} days"])

            where_clause = " AND ".join(conditions) if conditions else "1=1"
            sql = f"SELECT * FROM bot_events WHERE {where_clause} ORDER BY timestamp ASC"

            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]


    def get_stats(self) -> dict:
        """
        Database statistics for monitoring.
        SECURITY: returns counts and aggregates only, never raw data.
        """
        with self._connect() as conn:
            trades_total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            trades_today = len(self.get_trades_today())
            open_positions = conn.execute(
                "SELECT COUNT(*) FROM positions WHERE status = 'open'"
            ).fetchone()[0]
            opportunities_today = conn.execute(
                "SELECT COUNT(*) FROM opportunities WHERE timestamp >= ?",
                (datetime.utcnow().strftime("%Y-%m-%d"),),
            ).fetchone()[0]
            daily_pnl = self.get_daily_pnl()

            return {
                "trades_total": trades_total,
                "trades_today": trades_today,
                "open_positions": open_positions,
                "total_open_exposure": round(self.get_total_open_exposure(), 2),
                "opportunities_today": opportunities_today,
                "daily_pnl": round(daily_pnl, 4),
            }
