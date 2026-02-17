"""
Unit tests for Database (SQLite storage layer).

Tests cover:
- Schema creation and versioning
- CRUD for trades, positions, opportunities, events
- Data integrity (NOT NULL, defaults)
- Query methods (daily PnL, open exposure)
- Statistics
"""

import os
import pytest

from storage.database import Database


@pytest.fixture
def db(tmp_path):
    """Database using temp file for isolation."""
    db_path = str(tmp_path / "test.db")
    return Database(db_path=db_path)


class TestSchemaCreation:
    def test_creates_database_file(self, db):
        assert os.path.exists(db.db_path)

    def test_schema_versioned(self, db):
        with db._connect() as conn:
            cursor = conn.execute("SELECT MAX(version) FROM schema_version")
            version = cursor.fetchone()[0]
            assert version == 1

    def test_tables_exist(self, db):
        with db._connect() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = {row[0] for row in cursor.fetchall()}
            assert "trades" in tables
            assert "positions" in tables
            assert "opportunities" in tables
            assert "bot_events" in tables
            assert "schema_version" in tables

    def test_wal_mode_enabled(self, db):
        with db._connect() as conn:
            cursor = conn.execute("PRAGMA journal_mode")
            mode = cursor.fetchone()[0]
            assert mode == "wal"

    def test_creates_data_directory(self, tmp_path):
        db_path = str(tmp_path / "subdir" / "test.db")
        db = Database(db_path=db_path)
        assert os.path.exists(db.db_path)


class TestTradesCRUD:
    def test_record_trade(self, db):
        trade_id = db.record_trade(
            poly_leg="Down",
            kalshi_leg="Yes",
            kalshi_strike=96000.0,
            poly_cost=0.38,
            kalshi_cost=0.45,
            total_cost=0.83,
            net_margin=0.12,
            status="filled",
            dry_run=False,
        )
        assert trade_id > 0

    def test_get_trades_today(self, db):
        db.record_trade("Down", "Yes", 96000, 0.38, 0.45, 0.83, status="filled")
        trades = db.get_trades_today()
        assert len(trades) >= 1
        assert trades[0]["poly_leg"] == "Down"

    def test_update_trade_status(self, db):
        trade_id = db.record_trade("Up", "No", 97000, 0.55, 0.48, 1.03)
        db.update_trade_status(trade_id, "failed", error="connection timeout")
        trades = db.get_trades_today()
        t = [t for t in trades if t["id"] == trade_id][0]
        assert t["status"] == "failed"
        assert t["error_message"] == "connection timeout"

    def test_dry_run_default(self, db):
        trade_id = db.record_trade("Down", "Yes", 96000, 0.38, 0.45, 0.83)
        trades = db.get_trades_today()
        t = [t for t in trades if t["id"] == trade_id][0]
        assert t["dry_run"] == 1  # True by default

    def test_get_daily_pnl(self, db):
        # Record trade with known PnL
        with db._connect() as conn:
            conn.execute(
                """INSERT INTO trades
                   (poly_leg, kalshi_leg, kalshi_strike, poly_cost, kalshi_cost,
                    total_cost, actual_pnl, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("Down", "Yes", 96000, 0.38, 0.45, 0.83, 0.12, "filled"),
            )
        pnl = db.get_daily_pnl()
        assert pnl == pytest.approx(0.12)


class TestPositionsCRUD:
    def test_record_position(self, db):
        db.record_position(
            position_id="POS-000001",
            platform="kalshi",
            side="long",
            ticker="KXBTCD-96000",
            entry_price=0.45,
            size=1,
            cost_usd=0.45,
        )
        positions = db.get_open_positions()
        assert len(positions) == 1
        assert positions[0]["position_id"] == "POS-000001"

    def test_close_position(self, db):
        db.record_position("POS-000002", "polymarket", "short", "poly-down", 0.38, 1, 0.38)
        db.close_position("POS-000002")
        open_positions = db.get_open_positions()
        assert len(open_positions) == 0

    def test_linked_positions(self, db):
        db.record_position("POS-000003", "kalshi", "long", "K1", 0.45, 1, 0.45)
        db.record_position(
            "POS-000004", "polymarket", "short", "P1", 0.38, 1, 0.38,
            linked_position="POS-000003",
            arb_id="ARB-000001",
        )
        positions = db.get_open_positions()
        linked = [p for p in positions if p["linked_position"]][0]
        assert linked["linked_position"] == "POS-000003"
        assert linked["arb_id"] == "ARB-000001"

    def test_total_open_exposure(self, db):
        db.record_position("POS-A", "kalshi", "long", "K", 0.45, 10, 4.50)
        db.record_position("POS-B", "polymarket", "short", "P", 0.38, 10, 3.80)
        assert db.get_total_open_exposure() == pytest.approx(8.30)

    def test_exposure_after_close(self, db):
        db.record_position("POS-C", "kalshi", "long", "K", 0.50, 1, 0.50)
        db.close_position("POS-C")
        assert db.get_total_open_exposure() == 0.0


class TestOpportunities:
    def test_record_opportunity(self, db):
        opp_id = db.record_opportunity(
            kalshi_strike=96000,
            poly_leg="Down",
            kalshi_leg="Yes",
            poly_cost=0.38,
            kalshi_cost=0.45,
            total_cost=0.83,
            net_margin=0.12,
            was_executed=True,
        )
        assert opp_id > 0

    def test_record_skipped_opportunity(self, db):
        opp_id = db.record_opportunity(
            kalshi_strike=97000,
            poly_leg="Up",
            kalshi_leg="No",
            poly_cost=0.55,
            kalshi_cost=0.48,
            total_cost=1.03,
            net_margin=-0.03,
            was_executed=False,
            skip_reason="Negative margin",
        )
        assert opp_id > 0


class TestBotEvents:
    def test_log_event(self, db):
        event_id = db.log_event("circuit_breaker", "3 consecutive failures", severity="critical")
        assert event_id > 0

    def test_get_recent_events(self, db):
        db.log_event("info", "bot started")
        db.log_event("circuit_breaker", "tripped", severity="critical")
        events = db.get_recent_events(limit=10)
        assert len(events) == 2

    def test_filter_events_by_type(self, db):
        db.log_event("info", "startup")
        db.log_event("kill_switch", "activated", severity="critical")
        db.log_event("info", "shutdown")
        events = db.get_recent_events(event_type="kill_switch")
        assert len(events) == 1
        assert events[0]["event_type"] == "kill_switch"


class TestStats:
    def test_stats_empty_db(self, db):
        stats = db.get_stats()
        assert stats["trades_total"] == 0
        assert stats["open_positions"] == 0
        assert stats["daily_pnl"] == 0.0

    def test_stats_with_data(self, db):
        db.record_trade("Down", "Yes", 96000, 0.38, 0.45, 0.83, status="filled")
        db.record_position("POS-X", "kalshi", "long", "K", 0.45, 1, 0.45)
        db.record_opportunity(96000, "Down", "Yes", 0.38, 0.45, 0.83, 0.12)

        stats = db.get_stats()
        assert stats["trades_total"] == 1
        assert stats["open_positions"] == 1
        assert stats["opportunities_today"] >= 1
