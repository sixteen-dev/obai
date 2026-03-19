"""Test fixtures for fundamentals-server."""

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
    settings.openai_api_key = "test_openai_key"
    settings.server_name = "fundamentals-server"
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
def sample_income_statement() -> list[dict[str, Any]]:
    """Sample income statement response from FMP."""
    return [
        {
            "date": "2023-09-30",
            "symbol": "AAPL",
            "reportedCurrency": "USD",
            "fillingDate": "2023-10-28",
            "period": "FY",
            "revenue": 383285000000,
            "costOfRevenue": 214137000000,
            "grossProfit": 169148000000,
            "netIncome": 96995000000,
            "eps": 6.13,
        }
    ]


@pytest.fixture
def sample_company_profile() -> list[dict[str, Any]]:
    """Sample company profile response from FMP."""
    return [
        {
            "symbol": "AAPL",
            "companyName": "Apple Inc.",
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "description": "Apple Inc. designs, manufactures, and markets smartphones...",
            "ceo": "Tim Cook",
            "country": "US",
            "exchange": "NASDAQ",
            "marketCap": 3000000000000,
        }
    ]


@pytest.fixture
def sample_key_metrics() -> list[dict[str, Any]]:
    """Sample key metrics response from FMP."""
    return [
        {
            "symbol": "AAPL",
            "date": "2023-09-30",
            "period": "FY",
            "revenuePerShare": 24.19,
            "netIncomePerShare": 6.13,
            "peRatio": 28.5,
            "roe": 1.56,
            "roic": 0.58,
        }
    ]


@pytest.fixture
def mock_fmp_client() -> AsyncMock:
    """Create a mock FMP client."""
    client = AsyncMock()
    client.get_income_statement = AsyncMock(return_value=[])
    client.get_balance_sheet = AsyncMock(return_value=[])
    client.get_cash_flow = AsyncMock(return_value=[])
    client.get_company_profile = AsyncMock(return_value=[])
    client.get_key_metrics = AsyncMock(return_value=[])
    client.get_financial_ratios = AsyncMock(return_value=[])
    client.get_analyst_estimates = AsyncMock(return_value=[])
    client.get_price_target_summary = AsyncMock(return_value=[])
    client.get_company_rating = AsyncMock(return_value=[])
    client.health_check = AsyncMock(return_value=True)
    client.close = AsyncMock()
    return client
