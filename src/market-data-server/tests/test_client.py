"""Tests for FMP client."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.clients.fmp_client import FMPClient
from src.config import Settings


class TestFMPClientBasics:
    """Tests for FMP client basic functionality."""

    @pytest.mark.asyncio
    async def test_successful_quote_request(
        self, mock_settings: Settings, sample_quote_response: list[dict[str, Any]]
    ) -> None:
        """Test successful quote request."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_quote_response)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with FMPClient(mock_settings) as client:
                result = await client.get_quote("AAPL")

            assert result == sample_quote_response
            assert mock_get.call_count == 1

    @pytest.mark.asyncio
    async def test_request_includes_api_key(self, mock_settings: Settings) -> None:
        """Test that API key is included in request parameters."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=[{"symbol": "AAPL"}])

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with FMPClient(mock_settings) as client:
                await client.get_quote("AAPL")

            # Check that apikey was in the params
            call_kwargs = mock_get.call_args
            params = call_kwargs.kwargs.get("params", {})
            assert params.get("apikey") == "test_api_key"

    @pytest.mark.asyncio
    async def test_get_quote_short(self, mock_settings: Settings) -> None:
        """Test quote short endpoint."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value=[{"symbol": "AAPL", "price": 175.50, "volume": 55000000}]
        )

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with FMPClient(mock_settings) as client:
                result = await client.get_quote_short("AAPL")

            assert len(result) == 1
            assert result[0]["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_get_historical_intraday(
        self, mock_settings: Settings, sample_candles_response: list[dict[str, Any]]
    ) -> None:
        """Test historical intraday endpoint."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_candles_response)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with FMPClient(mock_settings) as client:
                result = await client.get_historical_intraday(
                    "AAPL", "5min", from_date="2024-01-14", to_date="2024-01-15"
                )

            assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_stock_movers(
        self, mock_settings: Settings, sample_movers_response: list[dict[str, Any]]
    ) -> None:
        """Test stock movers endpoint."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_movers_response)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with FMPClient(mock_settings) as client:
                result = await client.get_stock_movers("gainers")

            assert len(result) == 2

    @pytest.mark.asyncio
    async def test_http_error_raised(self, mock_settings: Settings) -> None:
        """Test that HTTP errors are raised."""
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 500

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            error = httpx.HTTPStatusError(
                "Server Error", request=MagicMock(), response=error_response
            )
            mock_get.side_effect = error

            async with FMPClient(mock_settings) as client:
                with pytest.raises(httpx.HTTPStatusError):
                    await client.get_quote("AAPL")


class TestFMPClientHealthCheck:
    """Tests for FMP client health check method."""

    @pytest.mark.asyncio
    async def test_health_check_returns_true_on_success(self, mock_settings: Settings) -> None:
        """Test health check returns True when API is reachable."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with FMPClient(mock_settings) as client:
                result = await client.health_check()

            assert result is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_timeout(self, mock_settings: Settings) -> None:
        """Test health check returns False on timeout."""
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = httpx.TimeoutException("Timeout")

            async with FMPClient(mock_settings) as client:
                result = await client.health_check()

            assert result is False

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_http_error(self, mock_settings: Settings) -> None:
        """Test health check returns False on HTTP error."""
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 500

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            error = httpx.HTTPStatusError("Error", request=MagicMock(), response=error_response)
            mock_get.side_effect = error

            async with FMPClient(mock_settings) as client:
                result = await client.health_check()

            assert result is False
