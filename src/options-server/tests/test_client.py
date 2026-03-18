"""Tests for Polygon client with retry logic."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.clients.polygon_client import PolygonAPIError, PolygonClient
from src.config import Settings


class TestPolygonClientRetry:
    """Tests for Polygon client exponential backoff retry logic."""

    @pytest.mark.asyncio
    async def test_successful_request_no_retry(
        self, mock_settings: Settings, sample_option_chain_response: dict[str, Any]
    ) -> None:
        """Test successful request doesn't trigger retry."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_option_chain_response)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with PolygonClient(mock_settings) as client:
                result = await client.get_option_chain_snapshot(underlying_asset="AAPL", limit=10)

            assert result == sample_option_chain_response
            assert mock_get.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_timeout(self, mock_settings: Settings) -> None:
        """Test request retries on timeout."""
        success_response = MagicMock(spec=httpx.Response)
        success_response.status_code = 200
        success_response.raise_for_status = MagicMock()
        success_response.json = MagicMock(return_value={"status": "OK", "results": []})

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = [
                httpx.TimeoutException("Timeout"),
                success_response,
            ]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                async with PolygonClient(mock_settings) as client:
                    result = await client.get_option_chain_snapshot(
                        underlying_asset="AAPL", limit=5
                    )

            assert result == {"status": "OK", "results": []}
            assert mock_get.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_5xx_error(self, mock_settings: Settings) -> None:
        """Test request retries on 5xx server errors."""
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 503
        error_response.json = MagicMock(return_value={})

        success_response = MagicMock(spec=httpx.Response)
        success_response.status_code = 200
        success_response.raise_for_status = MagicMock()
        success_response.json = MagicMock(return_value={"status": "OK", "results": []})

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            error = httpx.HTTPStatusError(
                "Server Error", request=MagicMock(), response=error_response
            )
            mock_get.side_effect = [error, success_response]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                async with PolygonClient(mock_settings) as client:
                    result = await client.get_option_chain_snapshot(
                        underlying_asset="AAPL", limit=5
                    )

            assert result == {"status": "OK", "results": []}
            assert mock_get.call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_4xx_error(self, mock_settings: Settings) -> None:
        """Test request does not retry on 4xx client errors."""
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 400
        error_response.json = MagicMock(return_value={"status": "ERROR", "error": "Bad request"})

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            error = httpx.HTTPStatusError(
                "Bad Request", request=MagicMock(), response=error_response
            )
            mock_get.side_effect = error

            async with PolygonClient(mock_settings) as client:
                with pytest.raises(PolygonAPIError) as exc_info:
                    await client.get_option_chain_snapshot(underlying_asset="AAPL", limit=5)

            assert "Bad request" in str(exc_info.value)
            assert mock_get.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_429_rate_limit(self, mock_settings: Settings) -> None:
        """Test request retries on 429 rate limit errors."""
        rate_limit_response = MagicMock(spec=httpx.Response)
        rate_limit_response.status_code = 429
        rate_limit_response.json = MagicMock(return_value={})

        success_response = MagicMock(spec=httpx.Response)
        success_response.status_code = 200
        success_response.raise_for_status = MagicMock()
        success_response.json = MagicMock(return_value={"status": "OK", "results": []})

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            error = httpx.HTTPStatusError(
                "Rate Limited", request=MagicMock(), response=rate_limit_response
            )
            mock_get.side_effect = [error, success_response]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                async with PolygonClient(mock_settings) as client:
                    result = await client.get_option_chain_snapshot(
                        underlying_asset="AAPL", limit=5
                    )

            assert result == {"status": "OK", "results": []}
            assert mock_get.call_count == 2

    @pytest.mark.asyncio
    async def test_max_retries_exhausted(self, mock_settings: Settings) -> None:
        """Test exception raised when max retries exhausted."""
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 503
        error_response.json = MagicMock(return_value={})

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            error = httpx.HTTPStatusError(
                "Server Error", request=MagicMock(), response=error_response
            )
            mock_get.side_effect = error

            with patch("asyncio.sleep", new_callable=AsyncMock):
                async with PolygonClient(mock_settings) as client:
                    with pytest.raises(httpx.HTTPStatusError):
                        await client.get_option_chain_snapshot(underlying_asset="AAPL", limit=5)

            # Initial + 3 retries = 4 calls
            assert mock_get.call_count == 4

    @pytest.mark.asyncio
    async def test_retry_on_connect_error(self, mock_settings: Settings) -> None:
        """Test request retries on connection errors."""
        success_response = MagicMock(spec=httpx.Response)
        success_response.status_code = 200
        success_response.raise_for_status = MagicMock()
        success_response.json = MagicMock(return_value={"status": "OK", "results": []})

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = [
                httpx.ConnectError("Connection failed"),
                success_response,
            ]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                async with PolygonClient(mock_settings) as client:
                    result = await client.get_option_chain_snapshot(
                        underlying_asset="AAPL", limit=5
                    )

            assert result == {"status": "OK", "results": []}
            assert mock_get.call_count == 2


class TestPolygonClientHealthCheck:
    """Tests for Polygon client health check method."""

    @pytest.mark.asyncio
    async def test_health_check_returns_true_on_success(self, mock_settings: Settings) -> None:
        """Test health check returns True when API is reachable."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with PolygonClient(mock_settings) as client:
                result = await client.health_check()

            assert result is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_timeout(self, mock_settings: Settings) -> None:
        """Test health check returns False on timeout."""
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = httpx.TimeoutException("Timeout")

            async with PolygonClient(mock_settings) as client:
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

            async with PolygonClient(mock_settings) as client:
                result = await client.health_check()

            assert result is False
