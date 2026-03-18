"""Tests for FMP client with retry logic."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.config import Settings


class TestFMPClientRetry:
    """Tests for FMP client retry logic via decorator."""

    @pytest.mark.asyncio
    async def test_successful_request_no_retry(
        self,
        mock_settings: Settings,
        sample_income_statement: list[dict[str, Any]],
    ) -> None:
        """Test successful request doesn't trigger retry."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_income_statement)

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = mock_response

            from src.clients.fmp_client import FMPClient

            client = FMPClient()
            result = await client.get_income_statement("AAPL", period="annual", limit=1)
            await client.close()

            assert result == sample_income_statement
            assert mock_get.call_count == 1

    @pytest.mark.asyncio
    async def test_request_includes_api_key(self, mock_settings: Settings) -> None:
        """Test that API key is included in request parameters."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=[{"symbol": "AAPL"}])

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = mock_response

            from src.clients.fmp_client import FMPClient

            client = FMPClient()
            await client.get_company_profile("AAPL")
            await client.close()

            # Check that apikey was in the params
            call_kwargs = mock_get.call_args
            params = call_kwargs.kwargs.get("params", {})
            assert params.get("apikey") == "test_api_key"

    @pytest.mark.asyncio
    async def test_get_balance_sheet(self, mock_settings: Settings) -> None:
        """Test balance sheet endpoint."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value=[{"symbol": "AAPL", "totalAssets": 352755000000}]
        )

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = mock_response

            from src.clients.fmp_client import FMPClient

            client = FMPClient()
            result = await client.get_balance_sheet("AAPL")
            await client.close()

            assert len(result) == 1
            assert result[0]["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_get_cash_flow(self, mock_settings: Settings) -> None:
        """Test cash flow endpoint."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value=[{"symbol": "AAPL", "operatingCashFlow": 110543000000}]
        )

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = mock_response

            from src.clients.fmp_client import FMPClient

            client = FMPClient()
            result = await client.get_cash_flow("AAPL")
            await client.close()

            assert len(result) == 1
            assert result[0]["symbol"] == "AAPL"


class TestFMPClientHealthCheck:
    """Tests for FMP client health check method."""

    @pytest.mark.asyncio
    async def test_health_check_returns_true_on_success(self, mock_settings: Settings) -> None:
        """Test health check returns True when API is reachable."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = mock_response

            from src.clients.fmp_client import FMPClient

            client = FMPClient()
            result = await client.health_check()
            await client.close()

            assert result is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_timeout(self, mock_settings: Settings) -> None:
        """Test health check returns False on timeout."""
        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.side_effect = httpx.TimeoutException("Timeout")

            from src.clients.fmp_client import FMPClient

            client = FMPClient()
            result = await client.health_check()
            await client.close()

            assert result is False

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_http_error(self, mock_settings: Settings) -> None:
        """Test health check returns False on HTTP error."""
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 500

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            error = httpx.HTTPStatusError("Error", request=MagicMock(), response=error_response)
            mock_get.side_effect = error

            from src.clients.fmp_client import FMPClient

            client = FMPClient()
            result = await client.health_check()
            await client.close()

            assert result is False
