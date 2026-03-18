"""Tests for AI-scored news tools."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.scored_news import get_scored_news, get_sector_news


@pytest.fixture
def sample_ticker_news() -> list[dict[str, Any]]:
    """Sample news articles from TezNewz API."""
    return [
        {
            "ticker": "NVDA",
            "headline": "NVIDIA Beats Q4 Earnings Expectations",
            "summary": "NVIDIA reported strong Q4 results, beating analyst expectations.",
            "impact_score": 85,
            "source": "benzinga",
            "url": "https://example.com/article1",
            "created_time": "1706140800000",
            "sector": "Technology",
            "company_info": {"company_name": "NVIDIA Corporation"},
        },
        {
            "ticker": "NVDA",
            "headline": "NVIDIA Faces Export Restrictions",
            "summary": "New export controls may limit NVIDIA chip sales to China.",
            "impact_score": -60,
            "source": "reuters",
            "url": "https://example.com/article2",
            "created_time": "1706054400000",
            "sector": "Technology",
            "company_info": {"company_name": "NVIDIA Corporation"},
        },
        {
            "ticker": "NVDA",
            "headline": "NVIDIA Partners with Cloud Provider",
            "summary": "Minor partnership announcement.",
            "impact_score": 20,
            "source": "bloomberg",
            "url": "https://example.com/article3",
            "created_time": "1705968000000",
            "sector": "Technology",
        },
    ]


@pytest.fixture
def sample_sector_news() -> list[dict[str, Any]]:
    """Sample sector news from TezNewz API."""
    return [
        {
            "ticker": "AAPL",
            "headline": "Apple Launches New Product",
            "summary": "Apple announced new product line.",
            "impact_score": 55,
            "source": "cnbc",
            "url": "https://example.com/aapl",
            "created_time": "1706140800000",
        },
        {
            "ticker": "MSFT",
            "headline": "Microsoft Cloud Revenue Grows",
            "summary": "Azure revenue up 30% YoY.",
            "impact_score": 70,
            "source": "wsj",
            "url": "https://example.com/msft",
            "created_time": "1706140800000",
        },
    ]


class TestGetScoredNews:
    """Tests for get_scored_news function."""

    @pytest.mark.asyncio
    async def test_returns_formatted_articles(
        self,
        sample_ticker_news: list[dict[str, Any]],
    ) -> None:
        """Test articles are properly formatted."""
        with patch("src.tools.scored_news.get_settings") as mock_settings:
            mock_settings.return_value = AsyncMock()

            with patch("src.tools.scored_news.TezNewzClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.get_news_by_ticker = AsyncMock(return_value=sample_ticker_news)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await get_scored_news("NVDA", hours_back=48)

        assert result["symbol"] == "NVDA"
        assert result["count"] == 3
        assert len(result["articles"]) == 3

        # Check first article format
        article = result["articles"][0]
        assert "headline" in article
        assert "summary" in article
        assert "impact_score" in article
        assert "source" in article
        assert "url" in article

    @pytest.mark.asyncio
    async def test_sorts_by_absolute_impact(
        self,
        sample_ticker_news: list[dict[str, Any]],
    ) -> None:
        """Test articles are sorted by absolute impact score."""
        with patch("src.tools.scored_news.get_settings") as mock_settings:
            mock_settings.return_value = AsyncMock()

            with patch("src.tools.scored_news.TezNewzClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.get_news_by_ticker = AsyncMock(return_value=sample_ticker_news)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await get_scored_news("NVDA")

        # Should be sorted: 85, -60, 20 by absolute value
        scores = [a["impact_score"] for a in result["articles"]]
        assert scores == [85, -60, 20]

    @pytest.mark.asyncio
    async def test_uses_summary_field(
        self,
        sample_ticker_news: list[dict[str, Any]],
    ) -> None:
        """Test summary field is correctly extracted from API response."""
        with patch("src.tools.scored_news.get_settings") as mock_settings:
            mock_settings.return_value = AsyncMock()

            with patch("src.tools.scored_news.TezNewzClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.get_news_by_ticker = AsyncMock(return_value=sample_ticker_news)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await get_scored_news("NVDA")

        # First article should have summary
        first = result["articles"][0]
        assert "beating analyst expectations" in first["summary"]

        # Third article should use its summary
        third = result["articles"][2]
        assert third["summary"] == "Minor partnership announcement."

    @pytest.mark.asyncio
    async def test_includes_scoring_info(
        self,
        sample_ticker_news: list[dict[str, Any]],
    ) -> None:
        """Test response includes scoring info."""
        with patch("src.tools.scored_news.get_settings") as mock_settings:
            mock_settings.return_value = AsyncMock()

            with patch("src.tools.scored_news.TezNewzClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.get_news_by_ticker = AsyncMock(return_value=sample_ticker_news)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await get_scored_news("NVDA")

        assert "scoring_info" in result
        assert result["scoring_info"]["range"] == "-100 to +100"
        assert "negative" in result["scoring_info"]
        assert "positive" in result["scoring_info"]

    @pytest.mark.asyncio
    async def test_includes_company_name_when_available(
        self,
        sample_ticker_news: list[dict[str, Any]],
    ) -> None:
        """Test company name is included when company_info is present."""
        with patch("src.tools.scored_news.get_settings") as mock_settings:
            mock_settings.return_value = AsyncMock()

            with patch("src.tools.scored_news.TezNewzClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.get_news_by_ticker = AsyncMock(return_value=sample_ticker_news)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await get_scored_news("NVDA")

        # First article has company_info
        first = result["articles"][0]
        assert first.get("company") == "NVIDIA Corporation"

        # Third article has no company_info
        third = result["articles"][2]
        assert "company" not in third

    @pytest.mark.asyncio
    async def test_published_date_uses_created_time(
        self,
        sample_ticker_news: list[dict[str, Any]],
    ) -> None:
        """Test published_date is mapped from created_time field."""
        with patch("src.tools.scored_news.get_settings") as mock_settings:
            mock_settings.return_value = AsyncMock()

            with patch("src.tools.scored_news.TezNewzClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.get_news_by_ticker = AsyncMock(return_value=sample_ticker_news)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await get_scored_news("NVDA")

        first = result["articles"][0]
        assert first["published_date"] == "1706140800000"


class TestGetSectorNews:
    """Tests for get_sector_news function."""

    @pytest.mark.asyncio
    async def test_returns_sector_articles(
        self,
        sample_sector_news: list[dict[str, Any]],
    ) -> None:
        """Test sector news is properly formatted."""
        with patch("src.tools.scored_news.get_settings") as mock_settings:
            mock_settings.return_value = AsyncMock()

            with patch("src.tools.scored_news.TezNewzClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.get_news_by_sector = AsyncMock(return_value=sample_sector_news)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await get_sector_news("Technology")

        assert result["sector"] == "Technology"
        assert result["count"] == 2
        assert len(result["articles"]) == 2

    @pytest.mark.asyncio
    async def test_extracts_unique_tickers(
        self,
        sample_sector_news: list[dict[str, Any]],
    ) -> None:
        """Test unique tickers are extracted from articles."""
        with patch("src.tools.scored_news.get_settings") as mock_settings:
            mock_settings.return_value = AsyncMock()

            with patch("src.tools.scored_news.TezNewzClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.get_news_by_sector = AsyncMock(return_value=sample_sector_news)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await get_sector_news("Technology")

        assert "tickers_mentioned" in result
        assert sorted(result["tickers_mentioned"]) == ["AAPL", "MSFT"]

    @pytest.mark.asyncio
    async def test_sorts_by_absolute_impact(
        self,
        sample_sector_news: list[dict[str, Any]],
    ) -> None:
        """Test sector news is sorted by absolute impact score."""
        with patch("src.tools.scored_news.get_settings") as mock_settings:
            mock_settings.return_value = AsyncMock()

            with patch("src.tools.scored_news.TezNewzClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.get_news_by_sector = AsyncMock(return_value=sample_sector_news)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await get_sector_news("Technology")

        # MSFT (70) should come before AAPL (55)
        scores = [a["impact_score"] for a in result["articles"]]
        assert scores == [70, 55]
