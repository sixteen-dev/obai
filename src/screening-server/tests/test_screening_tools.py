"""Tests for the stock screening tool's venue filtering and count reconciliation."""

from __future__ import annotations

from typing import Any

import pytest

from src.config import Settings
from src.tools import screening

# The page FMP returned for a country=US, industry='Software - Infrastructure'
# screen. `country` is company domicile, so NEO (Canadian) cross-listings of
# US-domiciled issuers survive the provider-side filter.
_PROVIDER_PAGE: list[dict[str, Any]] = [
    {"symbol": "MSFT.NE", "marketCap": 4795997581179, "exchangeShortName": "NEO", "country": "US"},
    {"symbol": "MSFT", "marketCap": 3450801596000, "exchangeShortName": "NASDAQ", "country": "US"},
    {"symbol": "PLTR.NE", "marketCap": 403399596832, "exchangeShortName": "NEO", "country": "US"},
    {"symbol": "ORCL", "marketCap": 374115443600, "exchangeShortName": "NYSE", "country": "US"},
    {"symbol": "PANW.NE", "marketCap": 302148998287, "exchangeShortName": "NEO", "country": "US"},
]


class _FakeFMPClient:
    """Async-context FMP client stub returning a fixed screener page."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.requested_limit: int | None = None

    async def __aenter__(self) -> _FakeFMPClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def screen_stocks(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.requested_limit = kwargs.get("limit")
        return self.rows[: self.requested_limit]


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeFMPClient:
    """Patch the screening tool's FMP client and settings with stubs."""
    client = _FakeFMPClient(list(_PROVIDER_PAGE))
    monkeypatch.setattr(screening, "FMPClient", lambda settings: client)
    monkeypatch.setattr(screening, "get_settings", Settings)
    return client


async def test_meta_reconciles_provider_rows_against_returned_rows(
    fake_client: _FakeFMPClient,
) -> None:
    """Counts must be reconcilable even when no row is dropped.

    ``returned`` was computed over the post-cap set with no record of what the
    provider actually sent, so any downstream drop contradicted the metadata it
    was timestamped against.
    """
    result = await screening.screen_stocks(country="US", limit=5)
    meta = result["meta"]

    assert meta["provider_rows_considered"] == 5
    assert meta["excluded_by_venue"] == 0
    assert meta["excluded_venues"] == []
    assert meta["returned"] == len(result["results"]) == 5
    # The over-fetched probe row is a has_more signal, never a reported count.
    assert fake_client.requested_limit == 6
    assert meta["provider_rows_considered"] <= meta["limit"]


async def test_us_listed_only_drops_foreign_venues_and_discloses_them(
    fake_client: _FakeFMPClient,
) -> None:
    """A 'US-listed' screen must exclude foreign cross-listings in the tool.

    Dropping them downstream is what produced a published count that disagreed
    with the provider metadata beside it.
    """
    result = await screening.screen_stocks(country="US", limit=5, us_listed_only=True)
    meta = result["meta"]

    assert [row["symbol"] for row in result["results"]] == ["MSFT", "ORCL"]
    assert meta["provider_rows_considered"] == 5
    assert meta["excluded_by_venue"] == 3
    assert meta["excluded_venues"] == ["NEO"]
    assert meta["returned"] == len(result["results"]) == 2
    assert meta["us_listed_only"] is True
    # The applied filter must appear where the agent restates filters.
    assert meta["filters_applied"]["us_listed_only"] is True
    assert meta["warning"] is None


async def test_has_more_tracks_the_provider_page_not_the_venue_filter(
    fake_client: _FakeFMPClient,
) -> None:
    """A thinned page must not be reported as more rows being available.

    The provider returned fewer rows than were requested, which is proof it is
    exhausted. Excluding cross-listings shortens the answer but conjures no
    further matches, so claiming otherwise fabricates a larger universe.
    """
    result = await screening.screen_stocks(country="US", limit=5, us_listed_only=True)

    assert fake_client.requested_limit == 6
    assert result["meta"]["provider_rows_considered"] == 5
    assert result["meta"]["excluded_by_venue"] == 3
    assert len(result["results"]) < result["meta"]["limit"]
    assert result["meta"]["has_more"] is False


async def test_has_more_is_true_only_when_the_provider_page_filled(
    fake_client: _FakeFMPClient,
) -> None:
    """A full page means the probe row came back, so more rows exist."""
    result = await screening.screen_stocks(country="US", limit=2)

    assert fake_client.requested_limit == 3
    assert result["meta"]["has_more"] is True
    assert result["meta"]["provider_rows_considered"] == 2
    assert result["meta"]["returned"] == 2


async def test_rows_missing_a_venue_are_excluded_not_assumed_us(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown venue must fail closed, never default to US-listed."""
    client = _FakeFMPClient([{"symbol": "MSFT", "exchangeShortName": "NASDAQ"}, {"symbol": "???"}])
    monkeypatch.setattr(screening, "FMPClient", lambda settings: client)
    monkeypatch.setattr(screening, "get_settings", Settings)

    result = await screening.screen_stocks(limit=5, us_listed_only=True)

    assert [row["symbol"] for row in result["results"]] == ["MSFT"]
    assert result["meta"]["excluded_by_venue"] == 1
    assert result["meta"]["excluded_venues"] == ["unknown"]
    assert (
        result["meta"]["returned"]
        == result["meta"]["provider_rows_considered"] - result["meta"]["excluded_by_venue"]
    )
