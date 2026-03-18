"""Test fixtures for market-data-server."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.config import Settings


@pytest.fixture
def mock_settings() -> Settings:
    """Create mock settings for tests."""
    settings = Settings()
    settings.fmp_api_key = "test_api_key"
    settings.fmp_base_url = "https://financialmodelingprep.com/stable"
    settings.server_name = "market-data-server"
    settings.server_version = "0.1.0"
    return settings


@pytest.fixture
def mock_httpx_response() -> MagicMock:
    """Create a mock httpx response."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.raise_for_status = MagicMock()
    return response


@pytest.fixture
def sample_quote_response() -> list[dict[str, Any]]:
    """Sample quote response from FMP."""
    return [
        {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "price": 175.50,
            "changesPercentage": 1.25,
            "change": 2.17,
            "dayLow": 173.25,
            "dayHigh": 176.80,
            "yearHigh": 199.62,
            "yearLow": 124.17,
            "marketCap": 2750000000000,
            "priceAvg50": 171.35,
            "priceAvg200": 169.82,
            "volume": 55000000,
            "avgVolume": 65000000,
            "exchange": "NASDAQ",
            "open": 174.00,
            "previousClose": 173.33,
        }
    ]


@pytest.fixture
def sample_candles_response() -> list[dict[str, Any]]:
    """Sample historical candles response from FMP."""
    return [
        {
            "date": "2024-01-15",
            "open": 174.00,
            "high": 176.80,
            "low": 173.25,
            "close": 175.50,
            "volume": 55000000,
        },
        {
            "date": "2024-01-14",
            "open": 172.50,
            "high": 174.25,
            "low": 171.80,
            "close": 173.33,
            "volume": 48000000,
        },
    ]


@pytest.fixture
def sample_movers_response() -> list[dict[str, Any]]:
    """Sample market movers response from FMP."""
    return [
        {
            "symbol": "XYZ",
            "name": "XYZ Corp",
            "change": 5.25,
            "price": 45.50,
            "changesPercentage": 13.05,
        },
        {
            "symbol": "ABC",
            "name": "ABC Inc",
            "change": 3.75,
            "price": 32.25,
            "changesPercentage": 11.62,
        },
    ]


@pytest.fixture
def mock_fmp_client() -> AsyncMock:
    """Create a mock FMP client."""
    client = AsyncMock()
    client.get_quote = AsyncMock(return_value=[])
    client.get_quote_short = AsyncMock(return_value=[])
    client.get_historical_intraday = AsyncMock(return_value=[])
    client.get_historical_daily = AsyncMock(return_value={})
    client.get_stock_movers = AsyncMock(return_value=[])
    client.get_sector_performance = AsyncMock(return_value=[])
    client.get_afterhours_quote = AsyncMock(return_value=[])
    client.is_market_open = AsyncMock(return_value={})
    client.get_short_volume = AsyncMock(return_value=[])
    client.get_technical_indicators = AsyncMock(return_value=[])
    client.health_check = AsyncMock(return_value=True)
    client.close = AsyncMock()
    return client
