"""Shared test fixtures for research-server."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.clients.exa_client import ResearchResult
from src.config import Settings


@pytest.fixture(autouse=True)
def _load_test_settings():
    """Ensure settings are loaded for all tests."""
    import src.config as config_mod

    config_mod._settings = Settings(exa_api_key="test-key-for-tests")
    yield
    config_mod._settings = None


@pytest.fixture()
def mock_exa_response():
    """Create a mock Exa search response with realistic results."""
    result1 = MagicMock()
    result1.title = "Apple's Vision Pro: Six Months Later"
    result1.url = "https://techcrunch.com/2026/03/15/apple-vision-pro-review"
    result1.highlights = [
        "User adoption has been slower than expected.",
        "Return rates around 15% in first quarter.",
    ]
    result1.text = ""
    result1.published_date = "2026-03-15"
    result1.author = "John Doe"
    result1.score = 0.92

    result2 = MagicMock()
    result2.title = "Apple Q1 2026 Strategy Shift"
    result2.url = "https://www.bloomberg.com/news/apple-strategy"
    result2.highlights = ["Services revenue surpassed hardware for the first time."]
    result2.text = ""
    result2.published_date = "2026-02-28"
    result2.author = None
    result2.score = 0.85

    response = MagicMock()
    response.results = [result1, result2]
    return response


@pytest.fixture()
def sample_research_results():
    """Pre-built ResearchResult list for tool tests."""
    return [
        ResearchResult(
            title="Test Article 1",
            url="https://example.com/article1",
            content="Some research content here.",
            published_date="2026-03-01",
            author="Author A",
            source_domain="example.com",
            relevance_score=0.95,
        ),
        ResearchResult(
            title="Test Article 2",
            url="https://blog.example.org/post",
            content="More research findings.",
            published_date="2026-02-15",
            author=None,
            source_domain="blog.example.org",
            relevance_score=0.78,
        ),
    ]
