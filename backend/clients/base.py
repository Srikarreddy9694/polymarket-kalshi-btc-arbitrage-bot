"""
Abstract base class for platform data clients.

All platform clients inherit from this to ensure a consistent interface.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional, Tuple

import requests

from config.settings import Settings, get_settings


class BaseClient(ABC):
    """Abstract base for all API clients."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.session = requests.Session()
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def fetch_data(self) -> Tuple[Any, Optional[str]]:
        """Fetch market data. Returns (data, error_message)."""
        ...

    def _get(self, url: str, params: Optional[dict] = None, timeout: int = 10) -> dict:
        """
        Shared GET request with error handling, timeout, and logging.
        Raises requests.RequestException on failure.
        """
        self.logger.debug("GET %s params=%s", url, params)
        response = self.session.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()
