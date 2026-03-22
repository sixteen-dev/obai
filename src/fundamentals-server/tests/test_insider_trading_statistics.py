"""Tests for insider trading statistics tool."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.config import Settings
from src.response_filters import filter_insider_trading_statistics

# --- Sample data fixtures ---


@pytest.fixture
def sample_insider_stats_unordered() -> list[dict[str, Any]]:
    """Sample FMP response with quarters out of order (API has no guaranteed sort)."""
    return [
        {
            "symbol": "AAPL",
            "cik": "0000320193",
            "year": 2025,
            "quarter": 3,
            "acquiredTransactions": 8,
            "disposedTransactions": 12,
            "acquiredDisposedRatio": 0.67,
            "totalAcquired": 40000,
            "totalDisposed": 85000,
            "averageAcquired": 5000.0,
            "averageDisposed": 7083.33,
            "totalPurchases": 0,
            "totalSales": 0,
        },
        {
            "symbol": "AAPL",
            "cik": "0000320193",
            "year": 2026,
            "quarter": 1,
            "acquiredTransactions": 15,
            "disposedTransactions": 10,
            "acquiredDisposedRatio": 1.5,
            "totalAcquired": 76696,
            "totalDisposed": 102492,
            "averageAcquired": 5113.07,
            "averageDisposed": 10249.2,
            "totalPurchases": 0,
            "totalSales": 0,
        },
        {
            "symbol": "AAPL",
            "cik": "0000320193",
            "year": 2025,
            "quarter": 4,
            "acquiredTransactions": 10,
            "disposedTransactions": 8,
            "acquiredDisposedRatio": 1.25,
            "totalAcquired": 50000,
            "totalDisposed": 60000,
            "averageAcquired": 5000.0,
            "averageDisposed": 7500.0,
            "totalPurchases": 0,
            "totalSales": 0,
        },
        {
            "symbol": "AAPL",
            "cik": "0000320193",
            "year": 2025,
            "quarter": 2,
            "acquiredTransactions": 5,
            "disposedTransactions": 15,
            "acquiredDisposedRatio": 0.33,
            "totalAcquired": 20000,
            "totalDisposed": 120000,
            "averageAcquired": 4000.0,
            "averageDisposed": 8000.0,
            "totalPurchases": 0,
            "totalSales": 0,
        },
        {
            "symbol": "AAPL",
            "cik": "0000320193",
            "year": 2025,
            "quarter": 1,
            "acquiredTransactions": 3,
            "disposedTransactions": 20,
            "acquiredDisposedRatio": 0.15,
            "totalAcquired": 10000,
            "totalDisposed": 200000,
            "averageAcquired": 3333.33,
            "averageDisposed": 10000.0,
            "totalPurchases": 0,
            "totalSales": 0,
        },
    ]


# --- FMP Client tests ---


class TestFMPClientInsiderTradingStatistics:
    """Tests for FMPClient.get_insider_trading_statistics."""

    @pytest.mark.asyncio
    async def test_returns_sorted_most_recent_first(
        self,
        mock_settings: Settings,
        sample_insider_stats_unordered: list[dict[str, Any]],
    ) -> None:
        """Test that results are sorted by year/quarter descending."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_insider_stats_unordered)

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = mock_response

            from src.clients.fmp_client import FMPClient

            client = FMPClient()
            result = await client.get_insider_trading_statistics("AAPL", limit=5)
            await client.close()

            assert result[0]["year"] == 2026
            assert result[0]["quarter"] == 1
            assert result[1]["year"] == 2025
            assert result[1]["quarter"] == 4
            assert result[-1]["year"] == 2025
            assert result[-1]["quarter"] == 1

    @pytest.mark.asyncio
    async def test_limits_to_requested_quarters(
        self,
        mock_settings: Settings,
        sample_insider_stats_unordered: list[dict[str, Any]],
    ) -> None:
        """Test that result is sliced to the requested limit."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_insider_stats_unordered)

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = mock_response

            from src.clients.fmp_client import FMPClient

            client = FMPClient()
            result = await client.get_insider_trading_statistics("AAPL", limit=2)
            await client.close()

            assert len(result) == 2
            # Should be the 2 most recent quarters
            assert result[0]["year"] == 2026
            assert result[0]["quarter"] == 1
            assert result[1]["year"] == 2025
            assert result[1]["quarter"] == 4

    @pytest.mark.asyncio
    async def test_default_limit_is_four(
        self,
        mock_settings: Settings,
        sample_insider_stats_unordered: list[dict[str, Any]],
    ) -> None:
        """Test that default limit returns 4 quarters."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_insider_stats_unordered)

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = mock_response

            from src.clients.fmp_client import FMPClient

            client = FMPClient()
            result = await client.get_insider_trading_statistics("AAPL")
            await client.close()

            assert len(result) == 4

    @pytest.mark.asyncio
    async def test_caches_results(
        self,
        mock_settings: Settings,
        sample_insider_stats_unordered: list[dict[str, Any]],
    ) -> None:
        """Test that second call uses cache instead of making API request."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_insider_stats_unordered)

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = mock_response

            from src.clients.fmp_client import FMPClient

            client = FMPClient()
            await client.get_insider_trading_statistics("AAPL", limit=4)
            await client.get_insider_trading_statistics("AAPL", limit=4)
            await client.close()

            # Only one HTTP call — second hit cache
            assert mock_get.call_count == 1

    @pytest.mark.asyncio
    async def test_empty_api_response(self, mock_settings: Settings) -> None:
        """Test graceful handling of empty API response."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=[])

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = mock_response

            from src.clients.fmp_client import FMPClient

            client = FMPClient()
            result = await client.get_insider_trading_statistics("UNKNOWN")
            await client.close()

            assert result == []


# --- Response filter tests ---


class TestFilterInsiderTradingStatistics:
    """Tests for the insider trading statistics response filter."""

    def test_removes_cik_field(self) -> None:
        """Test that cik field is stripped from response."""
        data: list[dict[str, Any]] = [
            {
                "symbol": "AAPL",
                "cik": "0000320193",
                "year": 2026,
                "quarter": 1,
                "acquiredTransactions": 15,
                "disposedTransactions": 10,
                "acquiredDisposedRatio": 1.5,
            }
        ]
        result = filter_insider_trading_statistics(data)

        assert len(result) == 1
        assert "cik" not in result[0]
        assert result[0]["symbol"] == "AAPL"
        assert result[0]["acquiredDisposedRatio"] == 1.5

    def test_preserves_all_non_cik_fields(self) -> None:
        """Test that all fields except cik are preserved."""
        data: list[dict[str, Any]] = [
            {
                "symbol": "AAPL",
                "cik": "0000320193",
                "year": 2026,
                "quarter": 1,
                "acquiredTransactions": 15,
                "disposedTransactions": 10,
                "acquiredDisposedRatio": 1.5,
                "totalAcquired": 76696,
                "totalDisposed": 102492,
                "averageAcquired": 5113.07,
                "averageDisposed": 10249.2,
                "totalPurchases": 0,
                "totalSales": 0,
            }
        ]
        result = filter_insider_trading_statistics(data)

        expected_fields = {
            "symbol",
            "year",
            "quarter",
            "acquiredTransactions",
            "disposedTransactions",
            "acquiredDisposedRatio",
            "totalAcquired",
            "totalDisposed",
            "averageAcquired",
            "averageDisposed",
            "totalPurchases",
            "totalSales",
        }
        assert set(result[0].keys()) == expected_fields

    def test_handles_empty_list(self) -> None:
        """Test filter on empty data."""
        result = filter_insider_trading_statistics([])
        assert result == []


# --- Tool function tests ---


class TestGetInsiderTradingStatisticsTool:
    """Tests for the get_insider_trading_statistics tool function."""

    @pytest.mark.asyncio
    async def test_returns_expected_structure(self, mock_settings: Settings) -> None:
        """Test that tool returns correct response structure."""
        mock_fmp = AsyncMock()
        mock_fmp.get_insider_trading_statistics = AsyncMock(
            return_value=[
                {
                    "symbol": "AAPL",
                    "cik": "0000320193",
                    "year": 2026,
                    "quarter": 1,
                    "acquiredTransactions": 15,
                    "disposedTransactions": 10,
                    "acquiredDisposedRatio": 1.5,
                    "totalAcquired": 76696,
                    "totalDisposed": 102492,
                    "averageAcquired": 5113.07,
                    "averageDisposed": 10249.2,
                    "totalPurchases": 0,
                    "totalSales": 0,
                }
            ]
        )
        mock_fmp.close = AsyncMock()

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("src.tools.fundamentals.FMPClient", return_value=mock_fmp),
        ):
            from src.tools.fundamentals import get_insider_trading_statistics

            result = await get_insider_trading_statistics("AAPL", limit=4)

        assert result["symbol"] == "AAPL"
        assert result["quarters"] == 1
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 1
        # cik should be filtered out
        assert "cik" not in result["data"][0]

    @pytest.mark.asyncio
    async def test_closes_client_on_success(self, mock_settings: Settings) -> None:
        """Test that FMP client is closed after successful call."""
        mock_fmp = AsyncMock()
        mock_fmp.get_insider_trading_statistics = AsyncMock(return_value=[])
        mock_fmp.close = AsyncMock()

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("src.tools.fundamentals.FMPClient", return_value=mock_fmp),
        ):
            from src.tools.fundamentals import get_insider_trading_statistics

            await get_insider_trading_statistics("AAPL")

        mock_fmp.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_closes_client_on_error(self, mock_settings: Settings) -> None:
        """Test that FMP client is closed even when API call fails."""
        mock_fmp = AsyncMock()
        mock_fmp.get_insider_trading_statistics = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Server error",
                request=MagicMock(),
                response=MagicMock(status_code=500),
            )
        )
        mock_fmp.close = AsyncMock()

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("src.tools.fundamentals.FMPClient", return_value=mock_fmp),
        ):
            from src.tools.fundamentals import get_insider_trading_statistics

            with pytest.raises(httpx.HTTPStatusError):
                await get_insider_trading_statistics("AAPL")

        mock_fmp.close.assert_awaited_once()
