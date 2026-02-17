"""
Unit tests for monitoring package (Sprint 6).

Tests cover:
- JSON logger formatting & secrets scrubbing
- Prometheus metrics (Counter, Gauge, Histogram, Registry)
- Telegram alerts (no-op mode, message formatting, status)
- /metrics and /alerts API endpoints
"""

import json
import logging
import time
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from monitoring.json_logger import JSONFormatter, SecretsScrubFilter, setup_json_logging
from monitoring.metrics import Counter, Gauge, Histogram, MetricsRegistry
from monitoring.telegram_alerts import TelegramAlerts


# ── JSON Logger ──────────────────────────────────────────

class TestJSONFormatter:
    def test_basic_format(self):
        fmt = JSONFormatter(service_name="test-bot", environment="test")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="Hello world", args=(), exc_info=None,
        )
        output = fmt.format(record)
        data = json.loads(output)
        assert data["message"] == "Hello world"
        assert data["service"] == "test-bot"
        assert data["environment"] == "test"
        assert data["level"] == "INFO"

    def test_error_includes_source(self):
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="/app/engine.py",
            lineno=42, msg="Something broke", args=(), exc_info=None,
        )
        data = json.loads(fmt.format(record))
        assert "source" in data
        assert data["source"]["line"] == 42

    def test_info_no_source(self):
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="Normal log", args=(), exc_info=None,
        )
        data = json.loads(fmt.format(record))
        assert "source" not in data

    def test_extra_trade_fields(self):
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="Trade done", args=(), exc_info=None,
        )
        record.trade_id = "t-123"
        record.platform = "kalshi"
        record.latency_ms = 250.5
        data = json.loads(fmt.format(record))
        assert data["trade_id"] == "t-123"
        assert data["platform"] == "kalshi"
        assert data["latency_ms"] == 250.5


class TestSecretsScrubFilter:
    def test_scrubs_api_key(self):
        f = SecretsScrubFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="Using api_key=sk-secret-123 for auth", args=(), exc_info=None,
        )
        f.filter(record)
        assert "sk-secret-123" not in record.msg
        assert "[REDACTED]" in record.msg

    def test_scrubs_private_key(self):
        f = SecretsScrubFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="private_key=abc123 loaded", args=(), exc_info=None,
        )
        f.filter(record)
        assert "abc123" not in record.msg

    def test_passes_clean_message(self):
        f = SecretsScrubFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="Trade executed successfully", args=(), exc_info=None,
        )
        result = f.filter(record)
        assert result is True
        assert record.msg == "Trade executed successfully"


class TestSetupJsonLogging:
    def test_setup_returns_root_logger(self):
        root = setup_json_logging(service_name="test", level=logging.DEBUG)
        assert root is logging.getLogger()
        # Cleanup: remove handlers so we don't affect other tests
        for h in root.handlers[:]:
            root.removeHandler(h)


# ── Prometheus Metrics ───────────────────────────────────

class TestCounter:
    def test_inc(self):
        c = Counter("test_total", "Test counter")
        c.inc()
        c.inc(3)
        assert c.get() == 4

    def test_labeled_inc(self):
        c = Counter("test_labeled", "Labeled counter")
        c.inc(platform="kalshi")
        c.inc(2, platform="polymarket")
        assert c.get(platform="kalshi") == 1
        assert c.get(platform="polymarket") == 2

    def test_render(self):
        c = Counter("test_render", "Render test")
        c.inc(5)
        output = c.render()
        assert "# HELP test_render Render test" in output
        assert "# TYPE test_render counter" in output
        assert "test_render 5" in output


class TestGauge:
    def test_set(self):
        g = Gauge("test_gauge", "Test gauge")
        g.set(42.5)
        assert g.get() == 42.5

    def test_inc_dec(self):
        g = Gauge("test_incdec", "Inc/dec test")
        g.inc(10)
        g.dec(3)
        assert g.get() == 7

    def test_labeled_set(self):
        g = Gauge("test_labeled_gauge", "Labeled gauge")
        g.set(1, feed="binance")
        g.set(0, feed="polymarket")
        assert g.get(feed="binance") == 1
        assert g.get(feed="polymarket") == 0

    def test_render(self):
        g = Gauge("test_g_render", "Test gauge render")
        g.set(100)
        output = g.render()
        assert "test_g_render 100" in output


class TestHistogram:
    def test_observe(self):
        h = Histogram("test_hist", "Test histogram", buckets=(100, 500, 1000))
        h.observe(50)
        h.observe(250)
        h.observe(750)
        output = h.render()
        assert "test_hist_count 3" in output

    def test_render_format(self):
        h = Histogram("latency", "Latency", buckets=(100, 500))
        h.observe(50)
        output = h.render()
        assert "# TYPE latency histogram" in output
        assert 'latency_bucket{le="100"}' in output
        assert 'latency_bucket{le="+Inf"}' in output
        assert "latency_sum" in output
        assert "latency_count" in output


class TestMetricsRegistry:
    def test_render_output(self):
        reg = MetricsRegistry()
        reg.trades_total.inc(5)
        reg.daily_pnl.set(1.23)
        output = reg.render()
        assert "arb_trades_total 5" in output
        assert "arb_daily_pnl_usd 1.23" in output
        assert "arb_uptime_seconds" in output

    def test_get_status(self):
        reg = MetricsRegistry()
        status = reg.get_status()
        assert "trades_total" in status
        assert "daily_pnl" in status
        assert "uptime_seconds" in status

    def test_no_secrets_in_output(self):
        reg = MetricsRegistry()
        output = reg.render().lower()
        assert "api_key" not in output
        assert "token" not in output
        assert "private_key" not in output


# ── Telegram Alerts ──────────────────────────────────────

class TestTelegramAlerts:
    def test_disabled_by_default(self):
        tg = TelegramAlerts()
        assert tg._enabled is False
        assert tg.get_status()["enabled"] is False

    def test_enabled_with_credentials(self):
        tg = TelegramAlerts(bot_token="test-token", chat_id="12345")
        assert tg._enabled is True

    @pytest.mark.asyncio
    async def test_disabled_send_returns_false(self):
        tg = TelegramAlerts()
        result = await tg.send_message("test")
        assert result is False

    @pytest.mark.asyncio
    async def test_trade_alert_disabled(self):
        tg = TelegramAlerts()
        result = await tg.alert_trade("t1", "kalshi", "buy", 10.0, 0.05)
        assert result is False

    @pytest.mark.asyncio
    async def test_circuit_breaker_alert_disabled(self):
        tg = TelegramAlerts()
        result = await tg.alert_circuit_breaker("open", "test")
        assert result is False

    @pytest.mark.asyncio
    async def test_kill_switch_alert_disabled(self):
        tg = TelegramAlerts()
        result = await tg.alert_kill_switch(True, "test")
        assert result is False

    @pytest.mark.asyncio
    async def test_daily_summary_disabled(self):
        tg = TelegramAlerts()
        result = await tg.alert_daily_summary(1.5, 10, 50.0, 100)
        assert result is False

    @pytest.mark.asyncio
    async def test_high_latency_disabled(self):
        tg = TelegramAlerts()
        result = await tg.alert_high_latency(800.0)
        assert result is False

    def test_status_no_secrets(self):
        tg = TelegramAlerts(bot_token="real-token", chat_id="12345")
        status = tg.get_status()
        status_str = str(status).lower()
        assert "real-token" not in status_str
        assert "bot_token" not in status_str
        assert "enabled" in status


# ── API Endpoints ────────────────────────────────────────

class TestMonitoringEndpoints:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from api import app
        return TestClient(app)

    def test_metrics_endpoint(self, client):
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        body = response.text
        assert "arb_trades_total" in body
        assert "arb_uptime_seconds" in body

    def test_metrics_no_secrets(self, client):
        body = client.get("/metrics").text.lower()
        assert "api_key" not in body
        assert "private_key" not in body
        assert "token" not in body

    def test_alerts_endpoint(self, client):
        response = client.get("/alerts")
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "messages_sent" in data
        assert "timestamp" in data

    def test_alerts_no_secrets(self, client):
        body = client.get("/alerts").text.lower()
        assert "bot_token" not in body
        assert "private_key" not in body
