"""Test fixtures for screening-server."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.config import Settings


@pytest.fixture
def mock_settings() -> Settings:
    """Create mock settings for tests."""
    settings = Settings()
    settings.fmp_api_key = "test_api_key"
    settings.fmp_base_url = "https://financialmodelingprep.com/stable"
    settings.server_name = "screening-server"
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
def sample_screen_response() -> list[dict[str, Any]]:
    """Sample response from FMP stock screener."""
    return [
        {
            "symbol": "AAPL",
            "companyName": "Apple Inc.",
            "marketCap": 3000000000000,
            "price": 175.50,
            "volume": 50000000,
            "beta": 1.2,
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "country": "US",
            "exchange": "NASDAQ",
        },
        {
            "symbol": "MSFT",
            "companyName": "Microsoft Corporation",
            "marketCap": 2800000000000,
            "price": 380.25,
            "volume": 25000000,
            "beta": 0.9,
            "sector": "Technology",
            "industry": "Software",
            "country": "US",
            "exchange": "NASDAQ",
        },
    ]


@pytest.fixture
def sample_search_response() -> list[dict[str, Any]]:
    """Sample response from FMP company search."""
    return [
        {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "currency": "USD",
            "stockExchange": "NASDAQ",
            "exchangeShortName": "NASDAQ",
        },
        {
            "symbol": "AAPD",
            "name": "Direxion Daily AAPL Bear 1X Shares",
            "currency": "USD",
            "stockExchange": "NASDAQ",
            "exchangeShortName": "NASDAQ",
        },
    ]


@pytest.fixture
def mock_fmp_client(
    mock_settings: Settings,
    mock_httpx_response: MagicMock,
    sample_screen_response: list[dict[str, Any]],
) -> AsyncMock:
    """Create a mock FMP client."""
    client = AsyncMock()
    client.screen_stocks = AsyncMock(return_value=sample_screen_response)
    client.search_by_name = AsyncMock(return_value=sample_screen_response[:1])
    client.search_by_symbol = AsyncMock(return_value=sample_screen_response[:1])
    client.health_check = AsyncMock(return_value=True)
    client.close = AsyncMock()
    return client


@pytest.fixture
def patch_get_settings(mock_settings: Settings) -> Any:
    """Patch get_settings to return mock settings."""
    with patch("src.config.get_settings", return_value=mock_settings) as mock:
        yield mock
