"""Exa API client — direct HTTP calls with retry logic and response normalization."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import get_settings
from ..logging_config import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://api.exa.ai"
_MAX_RETRIES = 3
_RETRY_DELAYS = [0.5, 1.0, 2.0]
_TIMEOUT = 30.0


@dataclass(frozen=True)
class ResearchResult:
    """Normalized research result from Exa.

    Attributes:
        title: Page title.
        url: Source URL.
        content: Highlights or text snippet.
        published_date: ISO date string or None.
        author: Author name or None.
        source_domain: Domain extracted from URL.
        relevance_score: Exa's 0-1 relevance score.
        freshness: "recent", "older", "stale", or "unknown".

    """

    title: str
    url: str
    content: str
    published_date: str | None
    author: str | None
    source_domain: str
    relevance_score: float
    freshness: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "title": self.title,
            "url": self.url,
            "content": self.content,
            "published_date": self.published_date,
            "freshness": self.freshness,
            "author": self.author,
            "source_domain": self.source_domain,
            "relevance_score": round(self.relevance_score, 3),
        }


def _extract_domain(url: str) -> str:
    """Extract domain from URL, stripping www prefix."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path.split("/")[0]
        return re.sub(r"^www\.", "", domain)
    except Exception:
        return url


def _is_retryable(status_code: int) -> bool:
    """Check if an HTTP status code is worth retrying."""
    return status_code in {429, 500, 502, 503, 504}


def _days_ago(days: int) -> str:
    """Return ISO date string for N days ago."""
    dt = datetime.now(UTC) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _assess_freshness(published_date: str | None) -> str:
    """Assess freshness of a result based on published date.

    Returns:
        "future" (date is after today — likely a misdated republish or
        templated article), "recent" (< 3 months), "older" (3-12 months),
        "stale" (> 12 months), or "unknown" if the date is missing or
        unparseable.

    """
    if not published_date:
        return "unknown"
    try:
        date_str = published_date[:10]
        pub = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)  # noqa: DTZ007
        age_days = (datetime.now(UTC) - pub).days
        if age_days < 0:
            return "future"
        if age_days < 90:
            return "recent"
        if age_days < 365:
            return "older"
        return "stale"
    except (ValueError, TypeError):
        return "unknown"


class ExaClient:
    """Exa API client using direct HTTP calls via httpx.

    Natively async — no sync SDK wrapping needed.

    """

    def __init__(self, api_key: str = "") -> None:
        """Initialize Exa client.

        Args:
            api_key: Exa API key. If empty, reads from settings.

        Raises:
            ValueError: If no API key is configured.

        """
        key = api_key or get_settings().exa_api_key
        if not key:
            msg = "EXA_API_KEY is not configured"
            raise ValueError(msg)
        self._headers = {
            "x-api-key": key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._client = httpx.AsyncClient(timeout=_TIMEOUT)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> ExaClient:
        """Enter async context — caller gets the live client."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Exit async context — always close the underlying HTTP client."""
        await self.close()

    async def search(
        self,
        query: str,
        *,
        search_type: str = "auto",
        num_results: int = 8,
        category: str | None = None,
        highlight_query: str | None = None,
        max_highlight_chars: int = 4000,
        start_published_date: str | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> list[ResearchResult]:
        """Search via Exa with retry logic.

        Args:
            query: Search query string.
            search_type: "auto", "neural", "keyword", "fast", etc.
            num_results: Number of results to return.
            category: Exa category filter ("company", "people", etc.).
            highlight_query: Query for highlight extraction.
            max_highlight_chars: Max chars per highlight.
            start_published_date: ISO date string for recency filter.
            include_domains: Whitelist domains.
            exclude_domains: Blacklist domains.

        Returns:
            List of normalized ResearchResult objects.

        """
        body: dict[str, Any] = {
            "query": query,
            "type": search_type,
            "numResults": num_results,
            "contents": {
                "highlights": {
                    "query": highlight_query or query,
                    "numSentences": 5,
                    "highlightsPerUrl": 3,
                    "maxCharacters": max_highlight_chars,
                },
            },
        }
        if category:
            body["category"] = category
        if start_published_date:
            body["startPublishedDate"] = start_published_date
        if include_domains:
            body["includeDomains"] = include_domains
        if exclude_domains:
            body["excludeDomains"] = exclude_domains

        data = await self._post("/search", body)
        return self._normalize_results(data)

    async def find_similar(
        self,
        url: str,
        *,
        num_results: int = 5,
        exclude_source_domain: bool = True,
        category: str | None = None,
        max_highlight_chars: int = 2000,
    ) -> list[ResearchResult]:
        """Find pages similar to a given URL.

        Args:
            url: Source URL to find similar content for.
            num_results: Number of results.
            exclude_source_domain: Exclude the source domain from results.
            category: Exa category filter.
            max_highlight_chars: Max chars per highlight.

        Returns:
            List of normalized ResearchResult objects.

        """
        body: dict[str, Any] = {
            "url": url,
            "numResults": num_results,
            "excludeSourceDomain": exclude_source_domain,
            "contents": {
                "highlights": {
                    "numSentences": 3,
                    "highlightsPerUrl": 2,
                    "maxCharacters": max_highlight_chars,
                },
            },
        }
        if category:
            body["category"] = category

        data = await self._post("/findSimilar", body)
        return self._normalize_results(data)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST to Exa API with exponential backoff retry.

        Args:
            path: API path (e.g., "/search").
            body: Request body dict.

        Returns:
            Parsed JSON response.

        Raises:
            httpx.HTTPStatusError: If all retries are exhausted.

        """
        url = f"{_BASE_URL}{path}"
        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await self._client.post(url, json=body, headers=self._headers)
                resp.raise_for_status()
                return resp.json()  # type: ignore[no-any-return]
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if attempt == _MAX_RETRIES or not _is_retryable(exc.response.status_code):
                    break
                delay = _RETRY_DELAYS[attempt]
                logger.warning(
                    "exa_retry",
                    path=path,
                    attempt=attempt + 1,
                    status=exc.response.status_code,
                    delay=delay,
                )
                await asyncio.sleep(delay)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt == _MAX_RETRIES:
                    break
                delay = _RETRY_DELAYS[attempt]
                logger.warning(
                    "exa_retry",
                    path=path,
                    attempt=attempt + 1,
                    error=str(exc),
                    delay=delay,
                )
                await asyncio.sleep(delay)

        if last_error is not None:
            raise last_error
        msg = "Exa call failed with no error captured"
        raise RuntimeError(msg)

    def _normalize_results(self, data: dict[str, Any]) -> list[ResearchResult]:
        """Normalize Exa JSON response into ResearchResult list."""
        results: list[ResearchResult] = []

        for item in data.get("results", []):
            highlights = item.get("highlights") or []
            content = " ... ".join(highlights) if highlights else (item.get("text") or "")
            pub_date = item.get("publishedDate")

            results.append(
                ResearchResult(
                    title=item.get("title") or "",
                    url=item.get("url") or "",
                    content=content,
                    published_date=pub_date,
                    author=item.get("author"),
                    source_domain=_extract_domain(item.get("url") or ""),
                    relevance_score=float(item.get("score") or 0.0),
                    freshness=_assess_freshness(pub_date),
                )
            )

        return results
