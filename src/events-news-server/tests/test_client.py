"""Tests for FMP client with retry logic."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.clients.fmp_client import FMPClient
from src.clients.tavily_client import TavilyClient
from src.config import Settings
from src.tools.earnings import get_earnings_calendar


class TestFMPClientRetry:
    """Tests for FMP client exponential backoff retry logic."""

    @pytest.mark.asyncio
    async def test_successful_request_no_retry(
        self, mock_settings: Settings, sample_news_response: list[dict[str, Any]]
    ) -> None:
        """Test successful request doesn't trigger retry."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_news_response)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with FMPClient(mock_settings) as client:
                result = await client.get_stock_news("AAPL", limit=10)

            assert result == sample_news_response
            assert mock_get.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_timeout(self, mock_settings: Settings) -> None:
        """Test request retries on timeout."""
        success_response = MagicMock(spec=httpx.Response)
        success_response.status_code = 200
        success_response.raise_for_status = MagicMock()
        success_response.json = MagicMock(return_value=[])

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = [
                httpx.TimeoutException("Timeout"),
                success_response,
            ]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                async with FMPClient(mock_settings) as client:
                    result = await client.get_stock_news("AAPL", limit=5)

            assert result == []
            assert mock_get.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_5xx_error(self, mock_settings: Settings) -> None:
        """Test request retries on 5xx server errors."""
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 503

        success_response = MagicMock(spec=httpx.Response)
        success_response.status_code = 200
        success_response.raise_for_status = MagicMock()
        success_response.json = MagicMock(return_value=[])

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            error = httpx.HTTPStatusError(
                "Server Error", request=MagicMock(), response=error_response
            )
            mock_get.side_effect = [error, success_response]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                async with FMPClient(mock_settings) as client:
                    result = await client.get_stock_news("AAPL", limit=5)

            assert result == []
            assert mock_get.call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_4xx_error(self, mock_settings: Settings) -> None:
        """Test request does not retry on 4xx client errors."""
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 400

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            error = httpx.HTTPStatusError(
                "Bad Request", request=MagicMock(), response=error_response
            )
            mock_get.side_effect = error

            async with FMPClient(mock_settings) as client:
                with pytest.raises(httpx.HTTPStatusError):
                    await client.get_stock_news("AAPL", limit=5)

            assert mock_get.call_count == 1

    @pytest.mark.asyncio
    async def test_max_retries_exhausted(self, mock_settings: Settings) -> None:
        """Test exception raised when max retries exhausted."""
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 503

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            error = httpx.HTTPStatusError(
                "Server Error", request=MagicMock(), response=error_response
            )
            mock_get.side_effect = error

            with patch("asyncio.sleep", new_callable=AsyncMock):
                async with FMPClient(mock_settings) as client:
                    with pytest.raises(httpx.HTTPStatusError):
                        await client.get_stock_news("AAPL", limit=5)

            # Initial + 3 retries = 4 calls
            assert mock_get.call_count == 4


class TestFMPClientEndpoints:
    """Tests for FMP client API endpoints."""

    @pytest.mark.asyncio
    async def test_get_earnings(
        self, mock_settings: Settings, sample_earnings_response: list[dict[str, Any]]
    ) -> None:
        """Test earnings endpoint."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_earnings_response)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with FMPClient(mock_settings) as client:
                result = await client.get_earnings(symbol="AAPL", limit=10)

            assert len(result) == 2
            assert result[0]["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_get_dividends(
        self, mock_settings: Settings, sample_dividends_response: list[dict[str, Any]]
    ) -> None:
        """Test dividends endpoint."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_dividends_response)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with FMPClient(mock_settings) as client:
                result = await client.get_dividends(symbol="AAPL", limit=10)

            assert len(result) == 1
            assert result[0]["symbol"] == "AAPL"
            assert result[0]["dividend"] == 0.24


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


class TestTavilyRecencyFilter:
    """Tests for Tavily recency window mapping (guards accuracy.md §29)."""

    @pytest.mark.asyncio
    async def test_recency_uses_time_range(self, mock_settings: Settings) -> None:
        """Recency window is passed via Tavily's time_range param, not the ignored days."""
        async with TavilyClient(mock_settings) as client:
            with patch.object(client.client, "search", new_callable=AsyncMock) as mock_search:
                mock_search.return_value = {"results": []}

                await client.search_market_news(
                    query="latest news",
                    ticker="NVDA",
                    time_range="day",
                )

            mock_search.assert_called_once()
            params = mock_search.call_args.kwargs
            # "news" is the only topic Tavily returns published_date for, and it
            # is the only one that returns journalism: "finance" answers a news
            # query with Yahoo quote pages and option-contract pages.
            assert params["topic"] == "news"
            assert params["time_range"] == "d"
            assert "days" not in params


class TestPublishedDateNormalization:
    """Article dates survive the client and arrive ISO-8601 (guards trace 01a01d20)."""

    @pytest.mark.asyncio
    async def test_published_date_is_normalized_to_iso(self, mock_settings: Settings) -> None:
        """Tavily returns RFC 2822; the specialist needs a comparable ISO date."""
        async with TavilyClient(mock_settings) as client:
            with patch.object(client.client, "search", new_callable=AsyncMock) as mock_search:
                mock_search.return_value = {
                    "results": [
                        {
                            "title": "Moderna stock nearly doubles",
                            "url": "https://www.biospace.com/x",
                            "content": "...",
                            "published_date": "Wed, 19 Aug 2026 13:25:41 GMT",
                            "score": 0.9,
                        }
                    ]
                }

                articles = await client.search_market_news(query="melanoma", ticker="MRNA")

        assert articles[0]["publishedDate"] == "2026-08-19T13:25:41+00:00"

    @pytest.mark.asyncio
    async def test_missing_published_date_stays_empty(self, mock_settings: Settings) -> None:
        """A result with no date must not invent one."""
        async with TavilyClient(mock_settings) as client:
            with patch.object(client.client, "search", new_callable=AsyncMock) as mock_search:
                mock_search.return_value = {
                    "results": [{"title": "t", "url": "https://x.com/y", "content": "c"}]
                }

                articles = await client.search_market_news(query="q", ticker="MRNA")

        assert articles[0]["publishedDate"] == ""

    @pytest.mark.asyncio
    async def test_unparseable_published_date_is_kept_verbatim(
        self, mock_settings: Settings
    ) -> None:
        """An unexpected format is passed through, never dropped or guessed."""
        async with TavilyClient(mock_settings) as client:
            with patch.object(client.client, "search", new_callable=AsyncMock) as mock_search:
                mock_search.return_value = {
                    "results": [
                        {
                            "title": "t",
                            "url": "https://x.com/y",
                            "content": "c",
                            "published_date": "last Tuesday",
                        }
                    ]
                }

                articles = await client.search_market_news(query="q", ticker="MRNA")

        assert articles[0]["publishedDate"] == "last Tuesday"


class TestEarningsReportedBeforeEstimated:
    """Earnings tool sorts reported rows first (guards accuracy.md §30)."""

    @pytest.mark.asyncio
    async def test_earnings_reported_before_estimated(self, mock_settings: Settings) -> None:
        """A small limit returns reported actuals first, date-desc, not FMP's raw order."""
        from src.tools.earnings import get_earnings

        # FMP raw order: estimated/upcoming rows first, reported rows scrambled.
        raw_earnings: list[dict[str, Any]] = [
            {
                "date": "2024-06-01",
                "symbol": "AAPL",
                "epsActual": None,
                "epsEstimated": 2.15,
                "revenueActual": None,
                "revenueEstimated": 118000000000,
            },
            {
                "date": "2023-10-20",
                "symbol": "AAPL",
                "epsActual": 1.46,
                "epsEstimated": 1.39,
                "revenueActual": 89500000000,
                "revenueEstimated": 88000000000,
            },
            {
                "date": "2024-09-01",
                "symbol": "AAPL",
                "epsActual": None,
                "epsEstimated": 2.20,
                "revenueActual": None,
                "revenueEstimated": 120000000000,
            },
            {
                "date": "2024-01-25",
                "symbol": "AAPL",
                "epsActual": 2.10,
                "epsEstimated": 2.05,
                "revenueActual": 117500000000,
                "revenueEstimated": 115000000000,
            },
        ]

        mock_client = AsyncMock()
        mock_client.get_earnings = AsyncMock(return_value=raw_earnings)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("src.tools.earnings.get_settings", return_value=mock_settings),
            patch("src.tools.earnings.FMPClient", return_value=mock_client),
        ):
            result = await get_earnings("AAPL", limit=2)

        earnings = result["earnings"]
        assert result["count"] == 2
        # Reported rows (actual EPS present) lead the prefix.
        assert earnings[0]["epsActual"] is not None
        assert earnings[1]["epsActual"] is not None
        # Within the reported group, most-recent date first.
        assert earnings[0]["date"] == "2024-01-25"
        assert earnings[1]["date"] == "2023-10-20"


class TestEarningsCalendarTool:
    """Market-wide earnings-calendar tool (guards accuracy.md §28)."""

    @pytest.mark.asyncio
    async def test_earnings_calendar_tool_returns_date_range(self, mock_settings: Settings) -> None:
        """A from/to query hits FMP earnings-calendar and returns multiple companies."""
        calendar_rows: list[dict[str, Any]] = [
            {
                "symbol": "AAPL",
                "date": "2024-07-25",
                "epsActual": None,
                "epsEstimated": 1.35,
                "revenueActual": None,
                "revenueEstimated": 84000000000,
                "lastUpdated": "2024-07-01",
            },
            {
                "symbol": "MSFT",
                "date": "2024-07-23",
                "epsActual": None,
                "epsEstimated": 2.93,
                "revenueActual": None,
                "revenueEstimated": 64000000000,
                "lastUpdated": "2024-07-01",
            },
            {
                "symbol": "TSLA",
                "date": "2024-07-24",
                "epsActual": None,
                "epsEstimated": 0.60,
                "revenueActual": None,
                "revenueEstimated": 24000000000,
                "lastUpdated": "2024-07-01",
            },
        ]

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=calendar_rows)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with FMPClient(mock_settings) as client:
                result = await client.get_earnings_calendar(
                    from_date="2024-07-22", to_date="2024-07-26"
                )

        # Multiple companies reporting across the range, not a single ticker.
        assert len(result) == 3
        assert {row["symbol"] for row in result} == {"AAPL", "MSFT", "TSLA"}

        # Client requested the earnings-calendar endpoint with the from/to params.
        mock_get.assert_called_once()
        called_url = mock_get.call_args.args[0]
        called_params = mock_get.call_args.kwargs["params"]
        assert called_url.endswith("/earnings-calendar")
        assert called_params["from"] == "2024-07-22"
        assert called_params["to"] == "2024-07-26"


class TestEarningsCalendarTruncation:
    """A capped calendar must stay date-ordered and admit what it dropped."""

    @staticmethod
    def _rows() -> list[dict[str, Any]]:
        """Five days of two companies each, in deliberately scrambled order."""
        rows: list[dict[str, Any]] = []
        for day in ("2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28"):
            for sym in ("ZZZ", "AAA"):
                rows.append(
                    {
                        "symbol": sym,
                        "date": day,
                        "epsActual": None,
                        "epsEstimated": 1.0,
                        "revenueActual": None,
                        "revenueEstimated": 1000.0,
                        "lastUpdated": day,
                    }
                )
        return [rows[i] for i in (7, 2, 9, 0, 5, 3, 8, 1, 6, 4)]

    async def _calendar(self, settings: Settings, limit: int) -> dict[str, Any]:
        """Run the tool against a stubbed provider response."""
        with (
            patch("src.tools.earnings.get_settings", return_value=settings),
            patch("src.tools.earnings.FMPClient") as mock_cls,
        ):
            client = mock_cls.return_value
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=None)
            client.get_earnings_calendar = AsyncMock(return_value=self._rows())
            return await get_earnings_calendar("2026-08-24", "2026-08-28", limit=limit)

    @pytest.mark.asyncio
    async def test_cap_keeps_the_earliest_days_not_an_arbitrary_slice(
        self, mock_settings: Settings
    ) -> None:
        """Provider order is arbitrary, so slicing it can drop whole days.

        "Who reports next week" answered from an unsorted prefix can return
        one day of a five-day window and still look complete.
        """
        result = await self._calendar(mock_settings, limit=4)

        days = [row["date"] for row in result["earnings_calendar"]]
        assert days == ["2026-08-24", "2026-08-24", "2026-08-25", "2026-08-25"]

    @pytest.mark.asyncio
    async def test_truncation_is_reported(self, mock_settings: Settings) -> None:
        """Returning only count lets a partial answer read as the whole market."""
        result = await self._calendar(mock_settings, limit=4)

        assert result["count"] == 4
        assert result["total_available"] == 10
        assert result["truncated"] is True

    @pytest.mark.asyncio
    async def test_complete_result_is_not_flagged(self, mock_settings: Settings) -> None:
        """A full answer must not carry a truncation warning."""
        result = await self._calendar(mock_settings, limit=100)

        assert result["count"] == 10
        assert result["total_available"] == 10
        assert result["truncated"] is False
