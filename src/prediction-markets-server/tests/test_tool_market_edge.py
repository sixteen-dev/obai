"""End-to-end tests for tools.market_edge.estimate_market_edge (§10.7)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from src.data import HistoryDownloader
from src.engine.edge import EdgeEstimate
from src.storage import PredictionDuckDBManager, PredictionStore
from src.tools.market_edge import _build_response, _yes_price, estimate_market_edge


def _now() -> datetime:
    return datetime(2026, 5, 16, tzinfo=timezone.utc)


def _payload(condition_id: str, *, terminal_yes: bool) -> dict[str, Any]:
    return {
        "condition_id": condition_id,
        "slug": condition_id.replace("0x", "slug-"),
        "question": "Will X happen?",
        "category": "politics",
        "clob_token_ids": [f"{condition_id}-Y", f"{condition_id}-N"],
        "outcomes": ["Yes", "No"],
        "outcome_prices": [1.0, 0.0] if terminal_yes else [0.0, 1.0],
        "closed": True,
        "uma_resolution_status": "resolved",
        "winning_outcome": None,
        "volume": 50000.0,
        "end_date": "2026-04-01T00:00:00Z",
        "event_slug": "event-x",
    }


class _FakeGamma:
    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self._payloads = payloads

    async def list_markets(self, *, limit: int = 10, **_: Any) -> list[dict[str, Any]]:
        return list(self._payloads.values())[:limit]

    async def public_search(self, **_: Any) -> dict[str, Any]:
        return {"events": [{"markets": list(self._payloads.values())}], "pagination": {}}

    async def get_market(self, identifier: str) -> dict[str, Any]:
        return self._payloads[identifier]

    async def close(self) -> None:
        pass


class _FakeClob:
    async def get_price_history(
        self, token_id: str, *, interval: str = "1d", fidelity: int = 60
    ) -> dict[str, Any]:
        return {
            "token_id": token_id,
            "interval": interval,
            "fidelity": fidelity,
            "count": 2,
            "history": [
                {"timestamp": 1700000000, "price": 0.10},
                {"timestamp": 1700100000, "price": 1.0 if "-Y" in token_id else 0.0},
            ],
        }

    async def close(self) -> None:
        pass


class _FakeData:
    async def get_trades(self, condition_id: str, *, limit: int = 50) -> dict[str, Any]:
        return {"condition_id": condition_id, "trade_count": 0, "trades": []}

    async def close(self) -> None:
        pass


@pytest.fixture
def fixtures() -> tuple[HistoryDownloader, PredictionStore]:
    payloads = {
        "0xA": _payload("0xA", terminal_yes=True),
        "0xB": _payload("0xB", terminal_yes=False),
    }
    store = PredictionStore(manager=PredictionDuckDBManager(db_path=":memory:"))
    store.ensure_connected()
    dl = HistoryDownloader(
        gamma=_FakeGamma(payloads),
        clob=_FakeClob(),
        data_client=_FakeData(),
        store=store,
    )
    return dl, store


@pytest.mark.asyncio
async def test_market_edge_explicit_price_reports_yes_side_base_rate(fixtures) -> None:
    """Explicit price+TTR path: YES-side base rate, mandatory limitation, no live fetch."""
    dl, store = fixtures
    result = await estimate_market_edge(
        downloader=dl,
        store=store,
        price=0.10,
        days_to_resolution=60,  # → "1m_plus", matching the fixture observations
        max_markets=5,
        max_history_points=1000,
        now=_now(),
    )
    assert result["tool"] == "estimate_market_edge"
    assert result["side"] == "YES"
    assert result["price_bucket"] == "0.10-0.15"
    assert result["ttr_bucket"] == "1m_plus"
    assert result["base_rate"] == 0.5  # 4 obs at 0.10, realized [1,0,0,1]
    assert result["calibration_universe"] == "all_resolved"
    # Two distinct markets → below the floor → low_n, edge withheld (not fabricated).
    assert result["reason"] == "low_n"
    assert result["edge"] is None
    assert result["low_n"] is True
    assert any("not a forecast for this specific market" in lim for lim in result["limitations"])
    assert any("edge_no = -edge_yes" in lim for lim in result["limitations"])


@pytest.mark.asyncio
async def test_market_edge_requires_both_explicit_fields(fixtures) -> None:
    """Price without days_to_resolution is a caller mistake → ValueError."""
    dl, store = fixtures
    with pytest.raises(ValueError, match="BOTH price and days_to_resolution"):
        await estimate_market_edge(
            downloader=dl,
            store=store,
            price=0.10,
            max_markets=5,
            max_history_points=1000,
            now=_now(),
        )


@pytest.mark.asyncio
async def test_market_edge_requires_identifier_or_explicit_pricing(fixtures) -> None:
    """No identifier and no explicit pricing → ValueError, not a silent empty read."""
    dl, store = fixtures
    with pytest.raises(ValueError, match="Provide slug"):
        await estimate_market_edge(
            downloader=dl,
            store=store,
            max_markets=5,
            max_history_points=1000,
            now=_now(),
        )


@pytest.mark.asyncio
async def test_market_edge_broadens_universe_when_category_is_thin(fixtures) -> None:
    """A category yielding no usable bucket broadens to all_resolved (bounded, one retry)."""
    dl, store = fixtures
    result = await estimate_market_edge(
        downloader=dl,
        store=store,
        price=0.10,
        days_to_resolution=60,
        category="politics",
        max_markets=5,
        max_history_points=1000,
        now=_now(),
    )
    assert result["edge_universe_broadened"] is True
    assert result["calibration_universe"] == "all_resolved"


def _calib_with_freq(realized_frequency: float) -> dict[str, Any]:
    """Minimal calibration result; only the bucket frequency varies between runs."""
    return {
        "selected_condition_ids": ["0xA", "0xB"],
        "metrics": {
            "market_bucket_once": {
                "buckets": [
                    {
                        "price_bucket": "0.10-0.15",
                        "ttr_bucket": "1m_plus",
                        "sample_size": 12,
                        "market_count": 12,
                        "implied_probability": 0.12,
                        "realized_frequency": realized_frequency,
                        "excess_return": realized_frequency - 0.12,
                        "brier_score": 0.2,
                        "log_loss": 0.6,
                        "low_n": False,
                    }
                ]
            }
        },
        "data_coverage": {"markets_selected": 12},
        "quality_flags": [],
        "reliability_label": "moderate",
        "limitations": [],
    }


def _fixed_estimate() -> EdgeEstimate:
    """Build a constant estimate so ONLY the calibration buckets vary in the fingerprint."""
    return EdgeEstimate(
        side="YES",
        price=0.12,
        price_bucket="0.10-0.15",
        ttr_bucket="1m_plus",
        base_rate=0.5,
        base_rate_ci=(0.3, 0.7),
        ci_n=12,
        edge=0.38,
        reason=None,
        sample_size=12,
        market_count=12,
        low_n=False,
    )


def test_yes_price_matches_label() -> None:
    """A No-first market returns the YES-labeled price, not positional outcome_prices[0].

    Gamma markets are not always ordered ['Yes', 'No']; reading prices[0]
    positionally would report the No-side price against the YES-side contract
    and flip the edge sign.
    """
    market = {"outcomes": ["No", "Yes"], "outcome_prices": [0.7, 0.3]}
    assert _yes_price(market) == 0.3  # YES-labeled price, not prices[0] (0.7)


def test_none_price_raises_clear_error() -> None:
    """An unparseable (None) YES price raises a descriptive ValueError, not TypeError.

    The Gamma normalizer preserves parse failures as None; float(None) would
    raise an opaque TypeError, so the tool must fail loud with a clear message.
    """
    market = {"outcomes": ["Yes", "No"], "outcome_prices": [None, 0.4]}
    with pytest.raises(ValueError, match="YES price"):
        _yes_price(market)


def test_market_edge_fingerprint_tracks_calibration_buckets() -> None:
    """Flip data_fingerprint when a calibration bucket frequency changes.

    Same params + selected IDs but a different bucket frequency (e.g. a UMA
    re-resolution) must shift the token; identical inputs must reproduce it.
    Previously only selected IDs fed the fingerprint, so a resolution change
    left the reproducibility token stale.
    """
    est = _fixed_estimate()
    low = _build_response(
        estimate=est,
        identifier={},
        universe="all_resolved",
        broadened=False,
        calib=_calib_with_freq(0.50),
        price_bucket_size=0.05,
    )
    high = _build_response(
        estimate=est,
        identifier={},
        universe="all_resolved",
        broadened=False,
        calib=_calib_with_freq(0.60),
        price_bucket_size=0.05,
    )
    low_again = _build_response(
        estimate=est,
        identifier={},
        universe="all_resolved",
        broadened=False,
        calib=_calib_with_freq(0.50),
        price_bucket_size=0.05,
    )
    assert low["data_fingerprint"] != high["data_fingerprint"]  # bucket change flips token
    assert low["data_fingerprint"] == low_again["data_fingerprint"]  # identical inputs reproduce
