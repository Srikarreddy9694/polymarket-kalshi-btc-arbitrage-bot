"""
Unit tests for Paper Trading (Sprint 6).

Tests cover:
- PaperTrader initialization (forces DRY_RUN=True)
- Report generation
- Analyzer logic (Go/No-Go decisions)
"""

import json
import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from scripts.paper_trade import PaperTrader
from scripts.analyze_paper import analyze
from config.settings import Settings


class TestPaperTraderInit:
    def test_forces_dry_run(self):
        settings = Settings(DRY_RUN=False)
        trader = PaperTrader(settings=settings)
        assert trader.settings.DRY_RUN is True

    def test_initial_stats(self):
        settings = Settings(DRY_RUN=True)
        trader = PaperTrader(settings=settings)
        assert trader.opportunities_found == 0
        assert trader.profitable_opportunities == 0
        assert trader.total_simulated_pnl == 0.0


class TestReportGeneration:
    def test_empty_report(self):
        settings = Settings(DRY_RUN=True)
        trader = PaperTrader(settings=settings)
        trader.start_time = 1000.0

        with patch("time.time", return_value=4600.0):  # 1 hour
            report = trader._generate_report()

        assert report["duration_hours"] == pytest.approx(1.0, abs=0.01)
        assert report["total_scans"] == 0
        assert report["profitable_opportunities"] == 0
        assert report["simulated_pnl_usd"] == 0.0
        assert report["hit_rate_pct"] == 0.0

    def test_report_with_data(self):
        settings = Settings(DRY_RUN=True)
        trader = PaperTrader(settings=settings)
        trader.start_time = 1000.0
        trader.opportunities_found = 100
        trader.profitable_opportunities = 10
        trader.total_simulated_pnl = 0.50
        trader.margins = [0.05, 0.03, 0.02, 0.10, 0.05, 0.04, 0.03, 0.06, 0.07, 0.05]

        with patch("time.time", return_value=4600.0):
            report = trader._generate_report()

        assert report["hit_rate_pct"] == 10.0
        assert report["simulated_pnl_usd"] == 0.50
        assert report["avg_margin_usd"] == pytest.approx(0.05)
        assert report["max_margin_usd"] == 0.10
        assert report["min_margin_usd"] == 0.02


class TestPaperTraderStop:
    def test_stop(self):
        settings = Settings(DRY_RUN=True)
        trader = PaperTrader(settings=settings)
        trader._running = True
        trader.stop()
        assert trader._running is False


class TestAnalyzer:
    def _make_db_mock(self, events):
        db = MagicMock()
        db.get_events.return_value = events
        return db

    def test_no_data(self):
        db = self._make_db_mock([])
        report = analyze(db)
        assert report["recommendation"] == "NO-GO"

    def test_profitable_data(self):
        events = []
        for i in range(20):
            events.append({
                "details": json.dumps({
                    "strategy": "poly_up_kalshi_yes",
                    "net_margin": 0.05 if i % 3 == 0 else -0.01,
                }),
                "created_at": f"2026-02-16T{i:02d}:00:00Z",
            })
        db = self._make_db_mock(events)
        report = analyze(db)

        assert "scans" in report
        assert "pnl" in report
        assert "go_no_go" in report
        assert report["scans"]["total"] == 20

    def test_go_decision(self):
        events = []
        for i in range(100):
            events.append({
                "details": json.dumps({
                    "strategy": "poly_up_kalshi_yes",
                    "net_margin": 0.10,  # All very profitable
                }),
                "created_at": f"2026-02-16T12:00:00Z",
            })
        db = self._make_db_mock(events)
        report = analyze(db)
        assert "GO" in report["go_no_go"]["recommendation"]

    def test_no_go_low_margin(self):
        events = []
        for i in range(100):
            events.append({
                "details": json.dumps({
                    "strategy": "poly_up_kalshi_yes",
                    "net_margin": 0.001,  # Below threshold
                }),
                "created_at": f"2026-02-16T12:00:00Z",
            })
        db = self._make_db_mock(events)
        report = analyze(db)
        # avg_margin $0.001 < $0.005 threshold → NO-GO
        assert "NO-GO" in report["go_no_go"]["recommendation"]


class TestDockerfileExists:
    """Basic sanity checks — no Docker daemon required."""

    def test_dockerfile_exists(self):
        dockerfile = Path(__file__).resolve().parent.parent / "Dockerfile"
        assert dockerfile.exists(), "Dockerfile should exist"

    def test_dockerfile_has_healthcheck(self):
        dockerfile = Path(__file__).resolve().parent.parent / "Dockerfile"
        content = dockerfile.read_text()
        assert "HEALTHCHECK" in content

    def test_dockerfile_nonroot_user(self):
        dockerfile = Path(__file__).resolve().parent.parent / "Dockerfile"
        content = dockerfile.read_text()
        assert "USER" in content
        assert "botuser" in content

    def test_dockerfile_dry_run_default(self):
        dockerfile = Path(__file__).resolve().parent.parent / "Dockerfile"
        content = dockerfile.read_text()
        assert "DRY_RUN=True" in content
