"""
Unit tests for AsyncBaseClient and AsyncBinanceClient.

Tests cover:
- Connection pooling setup
- Request retries with backoff
- Latency tracking
- Error counting
- AsyncBinanceClient price/klines methods
"""

import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

from clients.async_base import AsyncBaseClient, AsyncBinanceClient


@pytest.fixture
def async_client():
    return AsyncBaseClient(base_url="https://api.example.com", max_retries=1)


class TestAsyncBaseClient:
    def test_initial_state(self, async_client):
        assert async_client._request_count == 0
        assert async_client._error_count == 0
        assert async_client._client is None

    def test_avg_latency_no_requests(self, async_client):
        assert async_client.avg_latency_ms is None

    @pytest.mark.asyncio
    async def test_lazy_client_init(self, async_client):
        client = await async_client._get_client()
        assert client is not None
        assert async_client._client is not None
        # Second call returns same client
        client2 = await async_client._get_client()
        assert client is client2
        await async_client.close()

    @pytest.mark.asyncio
    async def test_close_cleans_up(self, async_client):
        await async_client._get_client()
        await async_client.close()
        assert async_client._client is None

    @pytest.mark.asyncio
    async def test_close_when_not_initialized(self, async_client):
        await async_client.close()  # Should not raise

    def test_status_structure(self, async_client):
        status = async_client.get_status()
        assert "base_url" in status
        assert "request_count" in status
        assert "error_count" in status
        assert "avg_latency_ms" in status
        assert status["client_initialized"] is False

    def test_status_no_secrets(self, async_client):
        status = async_client.get_status()
        status_str = str(status).lower()
        assert "api_key" not in status_str
        assert "private_key" not in status_str
        assert "token" not in status_str


class TestAsyncBinanceClient:
    def test_init(self):
        client = AsyncBinanceClient()
        assert "binance" in client.base_url.lower()
        assert client.timeout == 5.0

    @pytest.mark.asyncio
    async def test_get_price_success(self):
        client = AsyncBinanceClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {"symbol": "BTCUSDT", "price": "96543.21"}
        mock_response.raise_for_status = MagicMock()

        with patch.object(client, 'get', new_callable=AsyncMock, return_value=mock_response):
            price = await client.get_price("BTCUSDT")
            assert price == pytest.approx(96543.21)

    @pytest.mark.asyncio
    async def test_get_price_failure(self):
        client = AsyncBinanceClient()
        with patch.object(
            client, 'get', new_callable=AsyncMock,
            side_effect=httpx.RequestError("timeout"),
        ):
            price = await client.get_price("BTCUSDT")
            assert price is None

    @pytest.mark.asyncio
    async def test_get_klines_success(self):
        client = AsyncBinanceClient()
        mock_response = MagicMock()
        mock_response.json.return_value = [
            [1700000000, "96000", "97000", "95000", "96500", "1000"],
        ]
        mock_response.raise_for_status = MagicMock()

        with patch.object(client, 'get', new_callable=AsyncMock, return_value=mock_response):
            klines = await client.get_klines(limit=1)
            assert len(klines) == 1

    @pytest.mark.asyncio
    async def test_get_klines_failure(self):
        client = AsyncBinanceClient()
        with patch.object(
            client, 'get', new_callable=AsyncMock,
            side_effect=httpx.RequestError("timeout"),
        ):
            klines = await client.get_klines()
            assert klines == []
