"""Tests for FMP client."""

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.clients.fmp_client import FMPClient
from src.config import Settings
from src.tools.candles import get_candles
from src.tools.market import is_market_open
from src.tools.movers import get_movers
from src.tools.quotes import get_latest_trade


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


class TestIsMarketOpenTool:
    """Tests for the is_market_open tool's exchange filtering."""

    @pytest.mark.asyncio
    async def test_is_market_open_filters_to_us_exchanges(self) -> None:
        """Only US exchange entries (NASDAQ/NYSE) survive; foreign ones are dropped."""
        payload = [
            {"exchange": "NASDAQ", "name": "NASDAQ Global Market", "isMarketOpen": True},
            {"exchange": "NYSE", "name": "New York Stock Exchange", "isMarketOpen": True},
            {"exchange": "TSX", "name": "Toronto Stock Exchange", "isMarketOpen": True},
            {"exchange": "LSE", "name": "London Stock Exchange", "isMarketOpen": False},
            {"exchange": "JPX", "name": "Japan Exchange Group", "isMarketOpen": False},
        ]

        mock_client = AsyncMock()
        mock_client.is_market_open = AsyncMock(return_value=payload)

        with (
            patch("src.tools.market.get_settings"),
            patch("src.tools.market.FMPClient") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await is_market_open()

        exchanges = {row["exchange"] for row in result["data"]}
        assert exchanges == {"NASDAQ", "NYSE"}


class TestCandlesOrderingAndAdjustment:
    """Daily candles are normalized oldest-first and use the dividend-adjusted endpoint."""

    @pytest.mark.asyncio
    async def test_candles_oldest_first_and_dividend_adjusted(
        self, mock_settings: Settings
    ) -> None:
        """Tool returns oldest-first candles; daily client hits dividend-adjusted endpoint."""
        # Half 1: FMP returns a newest-first daily list; the tool must sort oldest-first.
        newest_first = [
            {"date": "2023-03-01", "open": 3.0, "high": 3.0, "low": 3.0, "close": 3.0, "volume": 1},
            {"date": "2023-02-01", "open": 2.0, "high": 2.0, "low": 2.0, "close": 2.0, "volume": 1},
            {"date": "2023-01-01", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1},
        ]
        mock_client = AsyncMock()
        mock_client.get_historical_daily = AsyncMock(return_value={"historical": newest_first})
        with (
            patch("src.tools.candles.get_settings"),
            patch("src.tools.candles.FMPClient") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_candles("AAPL", "daily", limit=10)

        dates = [row["date"] for row in result["data"]]
        assert dates == sorted(dates)
        assert dates[0] == "2023-01-01"
        assert dates[-1] == "2023-03-01"

        # Half 2: the daily client requests the dividend-adjusted endpoint and folds
        # the adjusted close into the canonical `close` key.
        captured: dict[str, Any] = {}

        def fake_get(endpoint: str, params: dict[str, Any] | None = None) -> Any:
            captured["endpoint"] = endpoint
            return [
                {
                    "date": "2023-01-03",
                    "adjOpen": 9.5,
                    "adjHigh": 10.5,
                    "adjLow": 9.0,
                    "adjClose": 10.0,
                    "volume": 100,
                }
            ]

        with patch.object(FMPClient, "_get", new_callable=AsyncMock, side_effect=fake_get):
            async with FMPClient(mock_settings) as client:
                data = await client.get_historical_daily("AAPL")

        assert captured["endpoint"] == "historical-price-eod/dividend-adjusted"
        row = data[0] if isinstance(data, list) else data["historical"][0]
        assert row["close"] == 10.0
        assert "adjClose" not in row


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


class TestResponseRetrievedAt:
    """Market-data responses carry a server retrieved_at (accuracy.md §20)."""

    @pytest.mark.asyncio
    async def test_latest_trade_has_retrieved_at(self) -> None:
        """get_latest_trade stamps a parseable ISO retrieved_at (quote-short has no time)."""
        mock_client = AsyncMock()
        mock_client.get_quote_short = AsyncMock(
            return_value=[{"symbol": "AAPL", "price": 175.50, "volume": 55000000}]
        )

        with (
            patch("src.tools.quotes.get_settings"),
            patch("src.tools.quotes.FMPClient") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_latest_trade("AAPL")

        assert "retrieved_at" in result
        datetime.fromisoformat(result["retrieved_at"])

    @pytest.mark.asyncio
    async def test_movers_have_retrieved_at(self) -> None:
        """get_movers stamps a parseable ISO retrieved_at on the response."""
        mock_client = AsyncMock()
        mock_client.get_stock_movers = AsyncMock(
            return_value=[{"symbol": "AAPL", "price": 175.50, "changesPercentage": 2.0}]
        )

        with (
            patch("src.tools.movers.get_settings"),
            patch("src.tools.movers.FMPClient") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_movers("gainers")

        assert "retrieved_at" in result
        datetime.fromisoformat(result["retrieved_at"])
