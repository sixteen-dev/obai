"""Tests for Exa client wrapper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.clients.exa_client import (
    ExaClient,
    _assess_freshness,
    _extract_domain,
    _is_retryable,
)


class TestResearchResult:
    def test_to_dict(self, sample_research_results):
        result = sample_research_results[0]
        d = result.to_dict()
        assert d["title"] == "Test Article 1"
        assert d["url"] == "https://example.com/article1"
        assert d["source_domain"] == "example.com"
        assert d["relevance_score"] == 0.95

    def test_to_dict_none_author(self, sample_research_results):
        result = sample_research_results[1]
        d = result.to_dict()
        assert d["author"] is None

    def test_freshness_field_present(self, sample_research_results):
        d = sample_research_results[0].to_dict()
        assert "freshness" in d


class TestExtractDomain:
    def test_simple_url(self):
        assert _extract_domain("https://techcrunch.com/article") == "techcrunch.com"

    def test_www_prefix_stripped(self):
        assert _extract_domain("https://www.bloomberg.com/news") == "bloomberg.com"

    def test_subdomain_kept(self):
        assert _extract_domain("https://blog.example.com/post") == "blog.example.com"

    def test_empty_url(self):
        assert _extract_domain("") == ""


class TestIsRetryable:
    def test_429_retryable(self):
        assert _is_retryable(429)

    def test_500_retryable(self):
        assert _is_retryable(500)

    def test_503_retryable(self):
        assert _is_retryable(503)

    def test_400_not_retryable(self):
        assert not _is_retryable(400)

    def test_401_not_retryable(self):
        assert not _is_retryable(401)

    def test_403_not_retryable(self):
        assert not _is_retryable(403)


class TestAssessFreshness:
    def test_recent(self):
        from datetime import UTC, datetime, timedelta

        recent = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%d")
        assert _assess_freshness(recent) == "recent"

    def test_older(self):
        from datetime import UTC, datetime, timedelta

        older = (datetime.now(UTC) - timedelta(days=200)).strftime("%Y-%m-%d")
        assert _assess_freshness(older) == "older"

    def test_stale(self):
        assert _assess_freshness("2020-01-01") == "stale"

    def test_unknown_none(self):
        assert _assess_freshness(None) == "unknown"

    def test_unknown_bad_format(self):
        assert _assess_freshness("not-a-date") == "unknown"


class TestExaClientInit:
    def test_raises_without_api_key(self):
        with patch("src.clients.exa_client.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(exa_api_key="")
            with pytest.raises(ValueError, match="EXA_API_KEY"):
                ExaClient()

    def test_accepts_explicit_key(self):
        client = ExaClient(api_key="test-key-123")
        assert client._headers["x-api-key"] == "test-key-123"


class TestExaClientNormalization:
    def test_normalize_search_results(self):
        client = ExaClient(api_key="test-key")
        data = {
            "results": [
                {
                    "title": "Apple's Vision Pro",
                    "url": "https://techcrunch.com/article",
                    "highlights": ["Adoption slower than expected.", "Return rates around 15%."],
                    "publishedDate": "2026-03-15",
                    "author": "John Doe",
                    "score": 0.92,
                },
                {
                    "title": "Apple Q1 Strategy",
                    "url": "https://www.bloomberg.com/news",
                    "highlights": ["Services revenue surpassed hardware."],
                    "publishedDate": "2026-02-28",
                    "author": None,
                    "score": 0.85,
                },
            ],
        }
        results = client._normalize_results(data)

        assert len(results) == 2
        assert results[0].title == "Apple's Vision Pro"
        assert results[0].source_domain == "techcrunch.com"
        assert results[0].relevance_score == 0.92
        assert "slower than expected" in results[0].content

    def test_normalize_empty_response(self):
        client = ExaClient(api_key="test-key")
        results = client._normalize_results({"results": []})
        assert results == []

    def test_normalize_missing_highlights_falls_back_to_text(self):
        client = ExaClient(api_key="test-key")
        data = {
            "results": [
                {
                    "title": "No highlights",
                    "url": "https://example.com",
                    "text": "Fallback text content",
                    "publishedDate": None,
                    "author": None,
                    "score": 0.5,
                },
            ],
        }
        results = client._normalize_results(data)
        assert len(results) == 1
        assert results[0].content == "Fallback text content"
        assert results[0].freshness == "unknown"


class TestDedicatedIndexCategories:
    """`company` and `people` run on indices that reject a recency filter.

    Exa answers the combination with a 400, so every call a tool made with
    both set failed outright. Rejecting it here names the conflict instead of
    spending a request to be told, and stops a caller silently losing either
    the category or the window.
    """

    @pytest.mark.asyncio
    async def test_company_with_recency_filter_is_rejected(self) -> None:
        client = ExaClient(api_key="k")
        with pytest.raises(ValueError, match="startPublishedDate"):
            await client.search(
                query="Moderna business model",
                category="company",
                start_published_date="2025-08-20T00:00:00.000Z",
            )

    @pytest.mark.asyncio
    async def test_people_with_recency_filter_is_rejected(self) -> None:
        client = ExaClient(api_key="k")
        with pytest.raises(ValueError, match="startPublishedDate"):
            await client.search(
                query="CEO track record",
                category="people",
                start_published_date="2024-08-20T00:00:00.000Z",
            )

    @pytest.mark.asyncio
    async def test_keyword_type_with_company_is_rejected(self) -> None:
        """Exa: 'The "company" category is not supported for type "keyword"'."""
        client = ExaClient(api_key="k")
        with pytest.raises(ValueError, match="keyword"):
            await client.search(
                query="Moderna official website",
                search_type="keyword",
                category="company",
            )

    @pytest.mark.asyncio
    async def test_category_without_recency_is_allowed(self) -> None:
        """The category alone is valid and must still reach the API."""
        client = ExaClient(api_key="k")
        with patch.object(client, "_post", new_callable=AsyncMock) as post:
            post.return_value = {"results": []}
            await client.search(query="Moderna", category="company")

        assert post.await_args is not None
        assert post.await_args.args[1]["category"] == "company"

    @pytest.mark.asyncio
    async def test_recency_without_category_is_allowed(self) -> None:
        """A dateless index is only a constraint when the category selects one."""
        client = ExaClient(api_key="k")
        with patch.object(client, "_post", new_callable=AsyncMock) as post:
            post.return_value = {"results": []}
            await client.search(
                query="Moderna",
                start_published_date="2025-08-20T00:00:00.000Z",
            )

        assert post.await_args is not None
        assert "startPublishedDate" in post.await_args.args[1]
