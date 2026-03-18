"""Test fixtures for options-server."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.config import Settings


@pytest.fixture
def mock_settings() -> Settings:
    """Create mock settings for tests."""
    settings = Settings()
    settings.polygon_api_key = "test_api_key"
    settings.polygon_base_url = "https://api.polygon.io"
    settings.server_name = "options-server"
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
def sample_option_chain_response() -> dict[str, Any]:
    """Sample response from Polygon option chain snapshot."""
    return {
        "status": "OK",
        "request_id": "test-request-id",
        "results": [
            {
                "break_even_price": 180.50,
                "day": {"change": 0.5, "change_percent": 1.2, "close": 5.25},
                "details": {
                    "contract_type": "call",
                    "exercise_style": "american",
                    "expiration_date": "2024-01-19",
                    "shares_per_contract": 100,
                    "strike_price": 175.0,
                    "ticker": "O:AAPL240119C00175000",
                },
                "greeks": {"delta": 0.65, "gamma": 0.03, "theta": -0.05, "vega": 0.15},
                "implied_volatility": 0.25,
                "open_interest": 15000,
                "underlying_asset": {"ticker": "AAPL", "price": 178.25},
            }
        ],
    }


@pytest.fixture
def sample_option_trade_response() -> dict[str, Any]:
    """Sample response from Polygon latest option trade."""
    return {
        "status": "OK",
        "request_id": "test-request-id",
        "results": {
            "T": "O:AAPL240119C00175000",
            "c": [2],
            "p": 5.25,
            "s": 10,
            "t": 1705600000000000000,
            "x": 304,
        },
    }


@pytest.fixture
def sample_option_quote_response() -> dict[str, Any]:
    """Sample response from Polygon latest option quote."""
    return {
        "status": "OK",
        "request_id": "test-request-id",
        "results": {
            "T": "O:AAPL240119C00175000",
            "P": 5.30,
            "S": 50,
            "p": 5.20,
            "s": 100,
            "t": 1705600000000000000,
        },
    }


@pytest.fixture
def mock_polygon_client(
    mock_settings: Settings,
) -> AsyncMock:
    """Create a mock Polygon client."""
    client = AsyncMock()
    client.get_option_chain_snapshot = AsyncMock(return_value={"status": "OK", "results": []})
    client.get_option_contract_snapshot = AsyncMock(return_value={"status": "OK", "results": {}})
    client.get_latest_option_trade = AsyncMock(return_value={"status": "OK", "results": {}})
    client.get_latest_option_quote = AsyncMock(return_value={"status": "OK", "results": {}})
    client.health_check = AsyncMock(return_value=True)
    client.close = AsyncMock()
    return client
