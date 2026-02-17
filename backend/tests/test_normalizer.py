"""
Unit tests for URL generation and strike parsing (normalizer logic).
"""

import datetime
import pytz
import pytest

# Import from the existing modules (they still work as-is)
import sys
import os

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from find_new_market import generate_slug, generate_market_url
from find_new_kalshi_market import generate_kalshi_slug, generate_kalshi_url
from fetch_current_kalshi import parse_strike


class TestPolymarketSlugGeneration:
    """Tests for Polymarket URL/slug generation."""

    def test_standard_slug_format(self):
        et_tz = pytz.timezone("US/Eastern")
        t = et_tz.localize(datetime.datetime(2025, 11, 26, 13, 0, 0))
        slug = generate_slug(t)
        assert slug == "bitcoin-up-or-down-november-26-1pm-et"

    def test_midnight_slug(self):
        et_tz = pytz.timezone("US/Eastern")
        t = et_tz.localize(datetime.datetime(2025, 12, 1, 0, 0, 0))
        slug = generate_slug(t)
        assert slug == "bitcoin-up-or-down-december-1-12am-et"

    def test_noon_slug(self):
        et_tz = pytz.timezone("US/Eastern")
        t = et_tz.localize(datetime.datetime(2025, 12, 25, 12, 0, 0))
        slug = generate_slug(t)
        assert slug == "bitcoin-up-or-down-december-25-12pm-et"

    def test_utc_time_converts_to_et(self):
        # 19:00 UTC = 14:00 ET (during EST, UTC-5)
        t = datetime.datetime(2025, 11, 26, 19, 0, 0, tzinfo=pytz.utc)
        slug = generate_slug(t)
        assert "2pm" in slug

    def test_url_includes_base(self):
        et_tz = pytz.timezone("US/Eastern")
        t = et_tz.localize(datetime.datetime(2025, 11, 26, 13, 0, 0))
        url = generate_market_url(t)
        assert url.startswith("https://polymarket.com/event/")
        assert "bitcoin-up-or-down" in url


class TestKalshiSlugGeneration:
    """Tests for Kalshi URL/slug generation."""

    def test_standard_slug_format(self):
        et_tz = pytz.timezone("US/Eastern")
        t = et_tz.localize(datetime.datetime(2025, 11, 26, 14, 0, 0))
        slug = generate_kalshi_slug(t)
        assert slug == "kxbtcd-25nov2614"

    def test_midnight_slug(self):
        et_tz = pytz.timezone("US/Eastern")
        t = et_tz.localize(datetime.datetime(2025, 12, 1, 0, 0, 0))
        slug = generate_kalshi_slug(t)
        assert slug == "kxbtcd-25dec0100"

    def test_utc_time_converts_to_et(self):
        t = datetime.datetime(2025, 11, 26, 19, 0, 0, tzinfo=pytz.utc)
        slug = generate_kalshi_slug(t)
        # 19 UTC = 14 ET
        assert slug == "kxbtcd-25nov2614"

    def test_url_includes_base(self):
        et_tz = pytz.timezone("US/Eastern")
        t = et_tz.localize(datetime.datetime(2025, 11, 26, 14, 0, 0))
        url = generate_kalshi_url(t)
        assert url.startswith("https://kalshi.com/markets/kxbtcd/")


class TestStrikeParsing:
    """Tests for Kalshi strike price parsing from subtitle."""

    def test_standard_format(self):
        assert parse_strike("$96,250 or above") == 96250.0

    def test_no_comma(self):
        assert parse_strike("$500 or above") == 500.0

    def test_large_number(self):
        assert parse_strike("$100,000 or above") == 100000.0

    def test_no_dollar_sign(self):
        assert parse_strike("no price here") == 0.0

    def test_empty_string(self):
        assert parse_strike("") == 0.0

    def test_multiple_numbers_takes_first(self):
        result = parse_strike("$96,000 to $97,000")
        assert result == 96000.0
