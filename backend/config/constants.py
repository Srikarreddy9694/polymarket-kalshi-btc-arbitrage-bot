"""
Constants that don't change with environment configuration.
"""

# Polymarket market slug patterns
POLYMARKET_SLUG_PREFIX = "bitcoin-up-or-down"
POLYMARKET_BASE_URL = "https://polymarket.com/event/"

# Kalshi market slug patterns
KALSHI_SLUG_PREFIX = "kxbtcd"
KALSHI_BASE_URL = "https://kalshi.com/markets/kxbtcd/bitcoin-price-abovebelow/"

# Binary option payout
BINARY_OPTION_PAYOUT = 1.00  # $1.00 per contract

# Circuit breaker defaults
CIRCUIT_BREAKER_MAX_CONSECUTIVE_FAILURES = 3
CIRCUIT_BREAKER_COOLDOWN_SECONDS = 300  # 5 minutes
CIRCUIT_BREAKER_MAX_API_ERROR_RATE = 0.5  # 50%
CIRCUIT_BREAKER_STALENESS_THRESHOLD_SECONDS = 30
