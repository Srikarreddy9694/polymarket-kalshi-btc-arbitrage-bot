"""
Prometheus Metrics — exposes /metrics endpoint for scraping.

Tracks key business and system metrics:
- Trade counts (by platform, outcome)
- Latency histograms
- Active positions / exposure
- Circuit breaker state
- Feed connection status
- P&L tracking

No external dependencies — uses a simple text-based Prometheus format.
"""

from __future__ import annotations

import time
import threading
from collections import defaultdict
from typing import Dict, Optional


class Counter:
    """Thread-safe counter metric."""

    def __init__(self, name: str, help_text: str):
        self.name = name
        self.help = help_text
        self._value: float = 0.0
        self._labels: Dict[tuple, float] = defaultdict(float)
        self._lock = threading.Lock()

    def inc(self, value: float = 1.0, **labels: str) -> None:
        with self._lock:
            if labels:
                key = tuple(sorted(labels.items()))
                self._labels[key] += value
            else:
                self._value += value

    def get(self, **labels: str) -> float:
        with self._lock:
            if labels:
                key = tuple(sorted(labels.items()))
                return self._labels.get(key, 0.0)
            return self._value

    def render(self) -> str:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        with self._lock:
            if self._labels:
                for key, val in sorted(self._labels.items()):
                    label_str = ",".join(f'{k}="{v}"' for k, v in key)
                    lines.append(f"{self.name}{{{label_str}}} {val}")
            else:
                lines.append(f"{self.name} {self._value}")
        return "\n".join(lines)


class Gauge:
    """Thread-safe gauge metric (can go up and down)."""

    def __init__(self, name: str, help_text: str):
        self.name = name
        self.help = help_text
        self._value: float = 0.0
        self._labels: Dict[tuple, float] = defaultdict(float)
        self._lock = threading.Lock()

    def set(self, value: float, **labels: str) -> None:
        with self._lock:
            if labels:
                key = tuple(sorted(labels.items()))
                self._labels[key] = value
            else:
                self._value = value

    def inc(self, value: float = 1.0, **labels: str) -> None:
        with self._lock:
            if labels:
                key = tuple(sorted(labels.items()))
                self._labels[key] += value
            else:
                self._value += value

    def dec(self, value: float = 1.0, **labels: str) -> None:
        self.inc(-value, **labels)

    def get(self, **labels: str) -> float:
        with self._lock:
            if labels:
                key = tuple(sorted(labels.items()))
                return self._labels.get(key, 0.0)
            return self._value

    def render(self) -> str:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} gauge"]
        with self._lock:
            if self._labels:
                for key, val in sorted(self._labels.items()):
                    label_str = ",".join(f'{k}="{v}"' for k, v in key)
                    lines.append(f"{self.name}{{{label_str}}} {val}")
            else:
                lines.append(f"{self.name} {self._value}")
        return "\n".join(lines)


class Histogram:
    """Simple histogram with configurable buckets."""

    def __init__(self, name: str, help_text: str, buckets: tuple = (50, 100, 200, 500, 1000, 5000)):
        self.name = name
        self.help = help_text
        self.buckets = sorted(buckets)
        self._counts: Dict[float, int] = {b: 0 for b in self.buckets}
        self._counts[float("inf")] = 0
        self._sum: float = 0.0
        self._count: int = 0
        self._lock = threading.Lock()

    def observe(self, value: float) -> None:
        with self._lock:
            self._sum += value
            self._count += 1
            for b in self.buckets:
                if value <= b:
                    self._counts[b] += 1
            self._counts[float("inf")] += 1

    def render(self) -> str:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        with self._lock:
            cumulative = 0
            for b in self.buckets:
                cumulative += self._counts[b]
                lines.append(f'{self.name}_bucket{{le="{b}"}} {cumulative}')
            cumulative += self._counts[float("inf")] - sum(
                self._counts[b] for b in self.buckets
            )
            lines.append(f'{self.name}_bucket{{le="+Inf"}} {self._counts[float("inf")]}')
            lines.append(f"{self.name}_sum {self._sum}")
            lines.append(f"{self.name}_count {self._count}")
        return "\n".join(lines)


class MetricsRegistry:
    """
    Central metrics registry for the arbitrage bot.

    Provides pre-defined metrics and renders Prometheus text format.
    """

    def __init__(self):
        # ── Trade Metrics ─────────────────────────────
        self.trades_total = Counter(
            "arb_trades_total",
            "Total trades executed",
        )
        self.trades_pnl = Counter(
            "arb_trades_pnl_usd",
            "Cumulative realized P&L in USD",
        )
        self.trade_errors = Counter(
            "arb_trade_errors_total",
            "Total trade execution errors",
        )

        # ── Latency ───────────────────────────────────
        self.execution_latency = Histogram(
            "arb_execution_latency_ms",
            "Trade execution latency in milliseconds",
            buckets=(50, 100, 200, 300, 500, 1000, 2000, 5000),
        )

        # ── Position / Exposure ───────────────────────
        self.open_positions = Gauge(
            "arb_open_positions",
            "Number of open positions",
        )
        self.total_exposure = Gauge(
            "arb_total_exposure_usd",
            "Total open exposure in USD",
        )
        self.daily_pnl = Gauge(
            "arb_daily_pnl_usd",
            "Daily P&L in USD",
        )

        # ── Feed Status ──────────────────────────────
        self.feed_connected = Gauge(
            "arb_feed_connected",
            "Data feed connection status (1=connected, 0=disconnected)",
        )
        self.feed_messages = Counter(
            "arb_feed_messages_total",
            "Total messages received from data feeds",
        )

        # ── Safety ───────────────────────────────────
        self.circuit_breaker_state = Gauge(
            "arb_circuit_breaker_state",
            "Circuit breaker state (0=closed, 1=open, 2=half_open)",
        )
        self.kill_switch_active = Gauge(
            "arb_kill_switch_active",
            "Kill switch status (1=active, 0=inactive)",
        )

        # ── System ───────────────────────────────────
        self.uptime_seconds = Gauge(
            "arb_uptime_seconds",
            "Bot uptime in seconds",
        )
        self._start_time = time.time()

        self._all_metrics = [
            self.trades_total, self.trades_pnl, self.trade_errors,
            self.execution_latency,
            self.open_positions, self.total_exposure, self.daily_pnl,
            self.feed_connected, self.feed_messages,
            self.circuit_breaker_state, self.kill_switch_active,
            self.uptime_seconds,
        ]

    def render(self) -> str:
        """Render all metrics in Prometheus text exposition format."""
        self.uptime_seconds.set(time.time() - self._start_time)
        sections = [m.render() for m in self._all_metrics]
        return "\n\n".join(sections) + "\n"

    def get_status(self) -> dict:
        """Summary status (no secrets)."""
        return {
            "trades_total": self.trades_total.get(),
            "daily_pnl": self.daily_pnl.get(),
            "open_positions": self.open_positions.get(),
            "total_exposure": self.total_exposure.get(),
            "uptime_seconds": round(time.time() - self._start_time, 1),
        }
