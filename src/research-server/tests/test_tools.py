"""Tests for research tool implementations."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from src.clients.exa_client import ResearchResult


def _patch_async_client(mock_cls):
    """Wire the mocked ExaClient class so `async with ExaClient() as c:` returns
    the same mock instance that the test will configure with `.search` etc.
    """
    mock_client = mock_cls.return_value
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


def _mock_search_results():
    """Create mock ResearchResult list."""
    return [
        ResearchResult(
            title="Test Result",
            url="https://example.com",
            content="Test content",
            published_date="2026-03-01",
            author="Author",
            source_domain="example.com",
            relevance_score=0.9,
        ),
    ]


class TestCompanyProfile:
    async def test_returns_structured_result(self):
        with patch("src.tools.company_profile.ExaClient") as mock_cls:
            mock_client = _patch_async_client(mock_cls)
            mock_client.search = AsyncMock(return_value=_mock_search_results())

            from src.tools.company_profile import research_company_profile

            result = await research_company_profile("AAPL", "Apple Inc")

        assert result["symbol"] == "AAPL"
        assert result["company_name"] == "Apple Inc"
        assert result["tool"] == "company_profile"
        assert result["count"] == 1
        assert len(result["results"]) == 1

    async def test_uppercases_symbol(self):
        with patch("src.tools.company_profile.ExaClient") as mock_cls:
            mock_client = _patch_async_client(mock_cls)
            mock_client.search = AsyncMock(return_value=[])

            from src.tools.company_profile import research_company_profile

            result = await research_company_profile("aapl", "Apple Inc")

        assert result["symbol"] == "AAPL"


class TestLeadership:
    async def test_default_person_is_ceo(self):
        with patch("src.tools.leadership.ExaClient") as mock_cls:
            mock_client = _patch_async_client(mock_cls)
            mock_client.search = AsyncMock(return_value=_mock_search_results())

            from src.tools.leadership import research_leadership

            result = await research_leadership("AAPL", "Apple Inc")

        assert result["person"] == "CEO"
        assert result["tool"] == "leadership"

    async def test_custom_person_name(self):
        with patch("src.tools.leadership.ExaClient") as mock_cls:
            mock_client = _patch_async_client(mock_cls)
            mock_client.search = AsyncMock(return_value=[])

            from src.tools.leadership import research_leadership

            result = await research_leadership("AAPL", "Apple Inc", "Tim Cook")

        assert result["person"] == "Tim Cook"


class TestProductSentiment:
    async def test_returns_structured_result(self):
        with patch("src.tools.product_sentiment.ExaClient") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.search = AsyncMock(return_value=_mock_search_results())

            from src.tools.product_sentiment import research_product_sentiment

            result = await research_product_sentiment("AAPL", "Apple Inc", "Vision Pro")

        assert result["product"] == "Vision Pro"
        assert result["tool"] == "product_sentiment"

    async def test_default_product(self):
        with patch("src.tools.product_sentiment.ExaClient") as mock_cls:
            mock_client = _patch_async_client(mock_cls)
            mock_client.search = AsyncMock(return_value=[])

            from src.tools.product_sentiment import research_product_sentiment

            result = await research_product_sentiment("AAPL", "Apple Inc")

        assert result["product"] == "products"


class TestCompetitiveLandscape:
    async def test_two_step_approach(self):
        with patch("src.tools.competitive_landscape.ExaClient") as mock_cls:
            mock_client = _patch_async_client(mock_cls)
            mock_client.search = AsyncMock(return_value=_mock_search_results())
            mock_client.find_similar = AsyncMock(return_value=_mock_search_results())

            from src.tools.competitive_landscape import research_competitive_landscape

            result = await research_competitive_landscape("AAPL", "Apple Inc")

        assert result["tool"] == "competitive_landscape"
        assert "competitors" in result
        assert "comparisons" in result


class TestGeneralResearch:
    async def test_returns_structured_result(self):
        with patch("src.tools.general_research.ExaClient") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.search = AsyncMock(return_value=_mock_search_results())

            from src.tools.general_research import research_general

            result = await research_general("AI chip market trends", "NVDA")

        assert result["query"] == "AI chip market trends"
        assert result["symbol"] == "NVDA"
        assert result["tool"] == "general_research"

    async def test_optional_symbol(self):
        with patch("src.tools.general_research.ExaClient") as mock_cls:
            mock_client = _patch_async_client(mock_cls)
            mock_client.search = AsyncMock(return_value=[])

            from src.tools.general_research import research_general

            result = await research_general("global EV adoption rates")

        assert result["symbol"] is None
