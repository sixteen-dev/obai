"""Tests for TezNewz API client with retry logic."""

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.clients.teznewz_client import TezNewzClient
from src.config import Settings


@pytest.fixture
def mock_teznewz_settings() -> Settings:
    """Create mock settings for TezNewz tests."""
    settings = Settings()
    settings.fmp_api_key = "test_key"
    settings.tavily_api_key = "test_tavily_key"
    settings.teznewz_api_url = "http://api.teznewz.com/api/v1"
    settings.teznewz_api_key = "test_teznewz_key"
    return settings


@pytest.fixture
def sample_ticker_response() -> dict[str, Any]:
    """Sample ticker news response from TezNewz API."""
    return {
        "news": [
            {
                "ticker": "AAPL",
                "headline": "Apple Reports Strong Q1 Earnings",
                "summary": "Apple Inc. reported strong Q1 results.",
                "impact_score": 75,
                "source": "benzinga",
                "url": "https://example.com/article1",
                "created_time": "1706140800000",
                "sector": "Technology",
            },
            {
                "ticker": "AAPL",
                "headline": "Apple Faces Supply Chain Issues",
                "summary": "Apple facing supply chain disruptions.",
                "impact_score": -45,
                "source": "reuters",
                "url": "https://example.com/article2",
                "created_time": "1706054400000",
                "sector": "Technology",
            },
        ],
        "count": 2,
    }


@pytest.fixture
def sample_sector_response() -> dict[str, Any]:
    """Sample sector news response from TezNewz API."""
    return {
        "news": [
            {
                "ticker": "AAPL",
                "headline": "Apple Launches New Product",
                "summary": "Apple announced new product line.",
                "impact_score": 55,
                "source": "cnbc",
                "url": "https://example.com/aapl",
                "created_time": "1706140800000",
            },
        ],
        "count": 1,
    }


class TestTezNewzClientGetNewsByTicker:
    """Tests for get_news_by_ticker method."""

    @pytest.mark.asyncio
    async def test_successful_request(
        self,
        mock_teznewz_settings: Settings,
        sample_ticker_response: dict[str, Any],
    ) -> None:
        """Test successful ticker news request."""
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = sample_ticker_response

        with patch.object(httpx.AsyncClient, "get", return_value=mock_response):
            async with TezNewzClient(mock_teznewz_settings) as client:
                result = await client.get_news_by_ticker("AAPL", hours_back=24)

        assert len(result) == 2
        assert result[0]["ticker"] == "AAPL"
        assert result[0]["headline"] == "Apple Reports Strong Q1 Earnings"

    @pytest.mark.asyncio
    async def test_extracts_news_from_response(
        self,
        mock_teznewz_settings: Settings,
    ) -> None:
        """Test news list is extracted from wrapper response."""
        api_response = {"news": [{"ticker": "TSLA", "headline": "Tesla news"}], "count": 1}

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = api_response

        with patch.object(httpx.AsyncClient, "get", return_value=mock_response):
            async with TezNewzClient(mock_teznewz_settings) as client:
                result = await client.get_news_by_ticker("TSLA")

        assert len(result) == 1
        assert result[0]["ticker"] == "TSLA"

    @pytest.mark.asyncio
    async def test_empty_news_response(
        self,
        mock_teznewz_settings: Settings,
    ) -> None:
        """Test empty news list returns empty list."""
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = {"news": [], "count": 0}

        with patch.object(httpx.AsyncClient, "get", return_value=mock_response):
            async with TezNewzClient(mock_teznewz_settings) as client:
                result = await client.get_news_by_ticker("XYZ")

        assert result == []


class TestTezNewzClientGetNewsBySector:
    """Tests for get_news_by_sector method."""

    @pytest.mark.asyncio
    async def test_successful_sector_request(
        self,
        mock_teznewz_settings: Settings,
        sample_sector_response: dict[str, Any],
    ) -> None:
        """Test successful sector news request."""
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = sample_sector_response

        with patch.object(httpx.AsyncClient, "get", return_value=mock_response):
            async with TezNewzClient(mock_teznewz_settings) as client:
                result = await client.get_news_by_sector("Technology")

        assert len(result) == 1
        assert result[0]["ticker"] == "AAPL"


class TestTezNewzClientRetry:
    """Tests for TezNewz client retry logic."""

    @pytest.mark.asyncio
    async def test_retries_on_server_error(
        self,
        mock_teznewz_settings: Settings,
        sample_ticker_response: dict[str, Any],
    ) -> None:
        """Test request retries on 5xx server error."""
        error_response = httpx.Response(500, request=httpx.Request("GET", "http://test"))
        success_response = AsyncMock(spec=httpx.Response)
        success_response.status_code = 200
        success_response.raise_for_status = lambda: None
        success_response.json.return_value = sample_ticker_response

        call_count = 0

        async def mock_get(*args: Any, **kwargs: Any) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.HTTPStatusError(
                    "Server Error",
                    request=httpx.Request("GET", "http://test"),
                    response=error_response,
                )
            return success_response

        with (
            patch.object(httpx.AsyncClient, "get", side_effect=mock_get),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            async with TezNewzClient(mock_teznewz_settings) as client:
                result = await client.get_news_by_ticker("AAPL")

        assert len(result) == 2
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_timeout(
        self,
        mock_teznewz_settings: Settings,
        sample_ticker_response: dict[str, Any],
    ) -> None:
        """Test request retries on timeout."""
        success_response = AsyncMock(spec=httpx.Response)
        success_response.status_code = 200
        success_response.raise_for_status = lambda: None
        success_response.json.return_value = sample_ticker_response

        call_count = 0

        async def mock_get(*args: Any, **kwargs: Any) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.TimeoutException("Request timed out")
            return success_response

        with (
            patch.object(httpx.AsyncClient, "get", side_effect=mock_get),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            async with TezNewzClient(mock_teznewz_settings) as client:
                result = await client.get_news_by_ticker("AAPL")

        assert len(result) == 2
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_4xx_error(
        self,
        mock_teznewz_settings: Settings,
    ) -> None:
        """Test no retry on client error (4xx)."""
        error_response = httpx.Response(404, request=httpx.Request("GET", "http://test"))

        with patch.object(
            httpx.AsyncClient,
            "get",
            side_effect=httpx.HTTPStatusError(
                "Not Found",
                request=httpx.Request("GET", "http://test"),
                response=error_response,
            ),
        ):
            async with TezNewzClient(mock_teznewz_settings) as client:
                with pytest.raises(httpx.HTTPStatusError):
                    await client.get_news_by_ticker("AAPL")

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(
        self,
        mock_teznewz_settings: Settings,
    ) -> None:
        """Test raises after exhausting retries."""
        with (
            patch.object(
                httpx.AsyncClient,
                "get",
                side_effect=httpx.TimeoutException("Request timed out"),
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            async with TezNewzClient(mock_teznewz_settings) as client:
                with pytest.raises(httpx.TimeoutException):
                    await client.get_news_by_ticker("AAPL")


class TestTezNewzClientHealthCheck:
    """Tests for TezNewz health check."""

    @pytest.mark.asyncio
    async def test_health_check_returns_true(
        self,
        mock_teznewz_settings: Settings,
    ) -> None:
        """Test health check returns True when API responds."""
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None

        with patch.object(httpx.AsyncClient, "get", return_value=mock_response):
            async with TezNewzClient(mock_teznewz_settings) as client:
                result = await client.health_check()

        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_error(
        self,
        mock_teznewz_settings: Settings,
    ) -> None:
        """Test health check returns False on error."""
        with patch.object(
            httpx.AsyncClient,
            "get",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            async with TezNewzClient(mock_teznewz_settings) as client:
                result = await client.health_check()

        assert result is False


class TestTezNewzClientInit:
    """Tests for TezNewz client initialization."""

    def test_sets_api_key_header(
        self,
        mock_teznewz_settings: Settings,
    ) -> None:
        """Test API key is set in request headers."""
        client = TezNewzClient(mock_teznewz_settings)
        assert client.client.headers["X-API-Key"] == "test_teznewz_key"

    def test_strips_trailing_slash_from_base_url(self) -> None:
        """Test trailing slash is removed from base URL."""
        settings = Settings()
        settings.fmp_api_key = "test"
        settings.tavily_api_key = "test"
        settings.teznewz_api_url = "http://api.teznewz.com/api/v1/"
        settings.teznewz_api_key = "key"

        client = TezNewzClient(settings)
        assert client.base_url == "http://api.teznewz.com/api/v1"
