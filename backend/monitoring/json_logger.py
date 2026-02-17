"""
Structured JSON Logger — production-grade logging for CloudWatch/Datadog/ELK.

Replaces the default text formatter with structured JSON lines.
Each log entry includes: timestamp, level, logger, message, and any extra fields.

Security: Secrets are scrubbed from log output via a filter.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from typing import Any, Dict, Optional

# Patterns to scrub from log messages
_SECRET_PATTERNS = re.compile(
    r"(api[_-]?key|private[_-]?key|secret|token|password|authorization)"
    r"\s*[=:]\s*\S+",
    re.IGNORECASE,
)


class SecretsScrubFilter(logging.Filter):
    """Filter that redacts secrets from log messages before they are emitted."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _SECRET_PATTERNS.sub(
                lambda m: m.group().split("=")[0] + "=[REDACTED]"
                if "=" in m.group()
                else m.group().split(":")[0] + ":[REDACTED]",
                record.msg,
            )
        return True


class JSONFormatter(logging.Formatter):
    """
    Formats log records as JSON lines (one JSON object per line).

    Compatible with CloudWatch Logs, Datadog, ELK, and Grafana Loki.
    """

    def __init__(self, service_name: str = "arb-bot", environment: str = "production"):
        super().__init__()
        self.service_name = service_name
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self.service_name,
            "environment": self.environment,
        }

        # Add source location for errors/warnings
        if record.levelno >= logging.WARNING:
            log_entry["source"] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            }

        # Add exception info
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else "Unknown",
                "message": str(record.exc_info[1]),
            }

        # Add any extra fields the caller passed
        for key in ("trade_id", "platform", "latency_ms", "event_type", "margin", "pnl"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val

        return json.dumps(log_entry, default=str)


def setup_json_logging(
    service_name: str = "arb-bot",
    environment: str = "production",
    level: int = logging.INFO,
) -> logging.Logger:
    """
    Configure the root logger with JSON formatting and secrets scrubbing.

    Call this once at application startup to switch all loggers to JSON output.

    Returns the root logger for convenience.
    """
    root = logging.getLogger()

    # Remove existing handlers
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    # JSON handler to stdout
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter(service_name=service_name, environment=environment))
    handler.addFilter(SecretsScrubFilter())

    root.addHandler(handler)
    root.setLevel(level)

    return root


def get_trade_logger() -> logging.Logger:
    """Get a logger pre-configured for trade events."""
    return logging.getLogger("arb-bot.trades")


def get_system_logger() -> logging.Logger:
    """Get a logger pre-configured for system events."""
    return logging.getLogger("arb-bot.system")
