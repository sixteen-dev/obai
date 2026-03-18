"""Test fixtures for events-news-server."""

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
    settings.tavily_api_key = "test_tavily_key"
    settings.fmp_base_url = "https://financialmodelingprep.com/stable"
    settings.server_name = "events-news-server"
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
def sample_news_response() -> list[dict[str, Any]]:
    """Sample news response from FMP."""
    return [
        {
            "symbol": "AAPL",
            "publishedDate": "2024-01-15T14:30:00.000Z",
            "title": "Apple Announces Record Q1 Earnings",
            "image": "https://example.com/image.jpg",
            "site": "Reuters",
            "text": "Apple Inc. reported record quarterly results...",
            "url": "https://example.com/article",
        },
        {
            "symbol": "AAPL",
            "publishedDate": "2024-01-14T10:00:00.000Z",
            "title": "Apple Expands Services Division",
            "image": "https://example.com/image2.jpg",
            "site": "Bloomberg",
            "text": "Apple is expanding its services...",
            "url": "https://example.com/article2",
        },
    ]


@pytest.fixture
def sample_earnings_response() -> list[dict[str, Any]]:
    """Sample earnings response from FMP."""
    return [
        {
            "date": "2024-01-25",
            "symbol": "AAPL",
            "epsActual": 2.10,
            "epsEstimated": 2.05,
            "revenueActual": 117500000000,
            "revenueEstimated": 115000000000,
            "lastUpdated": "2024-01-26",
        },
        {
            "date": "2024-01-23",
            "symbol": "AAPL",
            "epsActual": 2.93,
            "epsEstimated": 2.78,
            "revenueActual": 62020000000,
            "revenueEstimated": 61100000000,
            "lastUpdated": "2024-01-24",
        },
    ]


@pytest.fixture
def sample_dividends_response() -> list[dict[str, Any]]:
    """Sample dividends response from FMP."""
    return [
        {
            "symbol": "AAPL",
            "date": "2024-02-08",
            "recordDate": "2024-02-12",
            "paymentDate": "2024-02-15",
            "declarationDate": "2024-01-30",
            "adjDividend": 0.24,
            "dividend": 0.24,
            "yield": 0.38,
            "frequency": "Quarterly",
        },
    ]


@pytest.fixture
def mock_fmp_client() -> AsyncMock:
    """Create a mock FMP client."""
    client = AsyncMock()
    client.get_stock_news = AsyncMock(return_value=[])
    client.get_earnings = AsyncMock(return_value=[])
    client.get_dividends = AsyncMock(return_value=[])
    client.health_check = AsyncMock(return_value=True)
    client.close = AsyncMock()
    return client


