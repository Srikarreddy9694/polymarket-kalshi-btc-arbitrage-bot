"""
Kill Switch — emergency stop for all trading activity.

Three activation methods:
1. File-based: presence of KILL_SWITCH file in project root
2. API: POST /kill-switch with valid auth token
3. Programmatic: call activate() directly

On activation:
- All trading immediately halts
- Risk manager is notified
- Circuit breaker is tripped
- Event is logged with timestamp and reason

Security:
- Kill switch API requires a bearer token (KILL_SWITCH_TOKEN in .env)
- Token is never logged or exposed in any response
- File-based switch works even if the API is unreachable
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class KillSwitch:
    """
    Emergency stop for all trading.

    Checks both file-based and programmatic activation.
    File-based switch is the ultimate fallback — works even if
    the process is unresponsive to API calls.
    """

    # Default kill switch file location (project root)
    DEFAULT_KILL_FILE = "KILL_SWITCH"

    def __init__(
        self,
        kill_file_path: Optional[str] = None,
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or get_settings()
        self._kill_file = Path(kill_file_path or self.DEFAULT_KILL_FILE)
        self._is_active: bool = False
        self._activated_at: Optional[datetime] = None
        self._reason: str = ""

        # Check for pre-existing kill switch file on startup
        if self._kill_file.exists():
            self._is_active = True
            self._reason = "kill switch file found on startup"
            self._activated_at = datetime.utcnow()
            logger.critical(
                "🛑 KILL SWITCH ACTIVE ON STARTUP: file '%s' exists",
                self._kill_file,
            )
        else:
            logger.info("KillSwitch initialized (file='%s')", self._kill_file)

    # ── Activation ───────────────────────────────────────

    def activate(self, reason: str = "manual") -> None:
        """
        Activate the kill switch. Creates the kill file as a persistent marker.
        """
        self._is_active = True
        self._activated_at = datetime.utcnow()
        self._reason = reason

        # Create kill file so it persists across restarts
        try:
            self._kill_file.write_text(
                f"KILL SWITCH ACTIVATED\n"
                f"Time: {self._activated_at.isoformat()}\n"
                f"Reason: {reason}\n"
            )
        except OSError as e:
            logger.error("Failed to create kill switch file: %s", e)

        logger.critical(
            "🛑 KILL SWITCH ACTIVATED | reason: %s | time: %s",
            reason, self._activated_at.isoformat(),
        )

    def deactivate(self, reason: str = "manual") -> None:
        """
        Deactivate the kill switch. Removes the kill file.
        """
        self._is_active = False
        self._activated_at = None
        self._reason = ""

        # Remove kill file
        try:
            if self._kill_file.exists():
                self._kill_file.unlink()
        except OSError as e:
            logger.error("Failed to remove kill switch file: %s", e)

        logger.info("▶️ KILL SWITCH DEACTIVATED | reason: %s", reason)

    # ── Status ───────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        """
        Check if kill switch is active.
        Also checks the file system for the kill file (fallback).
        """
        if self._kill_file.exists():
            if not self._is_active:
                self._is_active = True
                self._reason = "kill switch file detected"
                self._activated_at = datetime.utcnow()
                logger.critical("🛑 Kill switch file detected at runtime!")
            return True
        return self._is_active

    def get_status(self) -> dict:
        """
        Return kill switch status for monitoring.
        SECURITY: never exposes tokens, keys, or file system paths.
        """
        return {
            "is_active": self.is_active,
            "reason": self._reason if self._is_active else None,
            "activated_at": self._activated_at.isoformat() if self._activated_at else None,
            "kill_file_exists": self._kill_file.exists(),
        }

    # ── API Authentication ───────────────────────────────

    @staticmethod
    def validate_token(provided_token: str, settings: Optional[Settings] = None) -> bool:
        """
        Validate a kill switch API token.

        SECURITY:
        - Uses constant-time comparison to prevent timing attacks
        - Never logs the token value
        - Returns False if no token is configured (fail-closed)
        """
        s = settings or get_settings()
        expected = getattr(s, "KILL_SWITCH_TOKEN", "")

        if not expected:
            logger.warning("Kill switch token not configured — rejecting request")
            return False

        if not provided_token:
            return False

        # Constant-time comparison to prevent timing attacks
        import hmac
        return hmac.compare_digest(provided_token, expected)
