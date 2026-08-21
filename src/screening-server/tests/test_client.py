"""Tests for FMP client with retry logic."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.clients.fmp_client import FMPAPIError, FMPClient
from src.config import Settings
from src.tools.screening import screen_stocks


class TestFMPClientRetry:
    """Tests for FMP client exponential backoff retry logic."""

    @pytest.mark.asyncio
    async def test_successful_request_no_retry(
        self, mock_settings: Settings, sample_screen_response: list[dict[str, Any]]
    ) -> None:
        """Test successful request doesn't trigger retry."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_screen_response)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with FMPClient(mock_settings) as client:
                result = await client.screen_stocks(sector="Technology", limit=10)

            assert result == sample_screen_response
            assert mock_get.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_timeout(self, mock_settings: Settings) -> None:
        """Test request retries on timeout."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=[{"symbol": "AAPL"}])

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            # First call times out, second succeeds
            mock_get.side_effect = [
                httpx.TimeoutException("Timeout"),
                mock_response,
            ]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                async with FMPClient(mock_settings) as client:
                    result = await client.screen_stocks(limit=5)

            assert result == [{"symbol": "AAPL"}]
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
        success_response.json = MagicMock(return_value=[{"symbol": "MSFT"}])

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            error = httpx.HTTPStatusError(
                "Server Error", request=MagicMock(), response=error_response
            )
            mock_get.side_effect = [error, success_response]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                async with FMPClient(mock_settings) as client:
                    result = await client.screen_stocks(limit=5)

            assert result == [{"symbol": "MSFT"}]
            assert mock_get.call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_4xx_error(self, mock_settings: Settings) -> None:
        """Test request does not retry on 4xx client errors."""
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 400
        error_response.json = MagicMock(return_value={"Error Message": "Bad request"})

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            error = httpx.HTTPStatusError(
                "Bad Request", request=MagicMock(), response=error_response
            )
            mock_get.side_effect = error

            async with FMPClient(mock_settings) as client:
                with pytest.raises(FMPAPIError) as exc_info:
                    await client.screen_stocks(limit=5)

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
        success_response.json = MagicMock(return_value=[{"symbol": "GOOGL"}])

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            error = httpx.HTTPStatusError(
                "Rate Limited", request=MagicMock(), response=rate_limit_response
            )
            mock_get.side_effect = [error, success_response]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                async with FMPClient(mock_settings) as client:
                    result = await client.screen_stocks(limit=5)

            assert result == [{"symbol": "GOOGL"}]
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
                async with FMPClient(mock_settings) as client:
                    with pytest.raises(httpx.HTTPStatusError):
                        await client.screen_stocks(limit=5)

            # Initial + 3 retries = 4 calls
            assert mock_get.call_count == 4


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


def test_dividend_filter_is_dollars_per_share() -> None:
    """Dividend filter params must be documented as dollars-per-share, not yield.

    Guards accuracy.md §18: FMP's dividendMoreThan filters on lastAnnualDividend
    (dollars per share), so the docstring must not call it a dividend yield.
    """
    doc = FMPClient.screen_stocks.__doc__
    assert doc is not None
    assert "dollars per share" in doc
    assert "Minimum dividend yield" not in doc
    assert "Maximum dividend yield" not in doc


def _screen_rows(n: int) -> list[dict[str, Any]]:
    """Build ``n`` minimal screener rows that survive the field filter."""
    return [
        {"symbol": f"SYM{i}", "companyName": f"Company {i}", "marketCap": 10_000_000_000}
        for i in range(n)
    ]


def _mock_client_cm(rows: list[dict[str, Any]]) -> MagicMock:
    """Build a mock FMPClient async context manager whose screener returns ``rows``."""
    client = AsyncMock()
    client.screen_stocks = AsyncMock(return_value=rows)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


class TestScreenMetaSignals:
    """Screener truncation signals and ranking honesty (accuracy.md §19)."""

    def test_screen_docstring_makes_no_unqualified_ranking_claim(self) -> None:
        """The tool docstring must not call the output a 'ranked'/'top' list.

        No local sort is applied, so results are in provider-default order and
        must not be advertised as ranked.
        """
        doc = screen_stocks.__doc__
        assert doc is not None
        assert "ranked list" not in doc.lower()
        assert "top " not in doc.lower()

    @pytest.mark.asyncio
    async def test_screen_meta_signals_truncation(self, mock_settings: Settings) -> None:
        """meta must expose has_more/returned/limit when more rows exist."""
        limit = 3
        # Provider returns limit+1 rows: the tool over-fetches one extra to
        # detect truncation, then trims back to the requested limit.
        cm = _mock_client_cm(_screen_rows(limit + 1))
        with (
            patch("src.tools.screening.FMPClient", return_value=cm),
            patch("src.tools.screening.get_settings", return_value=mock_settings),
        ):
            result = await screen_stocks(sector="Technology", limit=limit)

        meta = result["meta"]
        assert meta["has_more"] is True
        assert meta["returned"] == limit
        assert meta["limit"] == limit
        assert len(result["results"]) == limit
        # The response must not advertise the order as ranked/top.
        assert "ranked" not in str(meta).lower()

    @pytest.mark.asyncio
    async def test_screen_meta_no_more_when_within_limit(self, mock_settings: Settings) -> None:
        """has_more must be False when the provider returns at/under the limit."""
        limit = 5
        cm = _mock_client_cm(_screen_rows(2))
        with (
            patch("src.tools.screening.FMPClient", return_value=cm),
            patch("src.tools.screening.get_settings", return_value=mock_settings),
        ):
            result = await screen_stocks(sector="Technology", limit=limit)

        meta = result["meta"]
        assert meta["has_more"] is False
        assert meta["returned"] == 2
        assert meta["limit"] == limit
