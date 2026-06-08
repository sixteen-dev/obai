"""Tests for engine.edge — base-rate edge + Wilson CI (§12.5)."""

from __future__ import annotations

import math

import pytest

from src.engine.calibration import CalibrationBucket, CalibrationSummary
from src.engine.edge import estimate_edge, estimate_to_dict


def _bucket(  # noqa: PLR0913 — test helper assembles a full CalibrationBucket
    price_label: str,
    ttr: str,
    freq: float,
    *,
    market_count: int,
    sample_size: int,
    low_n: bool,
) -> CalibrationBucket:
    return CalibrationBucket(
        price_bucket=price_label,
        ttr_bucket=ttr,
        sample_size=sample_size,
        market_count=market_count,
        implied_probability=0.47,
        realized_frequency=freq,
        excess_return=freq - 0.47,
        brier_score=0.2,
        log_loss=0.6,
        low_n=low_n,
    )


def _summary(buckets: list[CalibrationBucket]) -> CalibrationSummary:
    return CalibrationSummary(
        sampling_mode="market_bucket_once",
        sample_size=sum(b.sample_size for b in buckets),
        raw_observation_count=sum(b.sample_size for b in buckets),
        market_count=sum(b.market_count for b in buckets),
        effective_n=sum(b.market_count for b in buckets),
        overall_brier=0.2,
        overall_log_loss=0.6,
        expected_calibration_error=0.05,
        buckets=buckets,
        low_n_bucket_count=sum(1 for b in buckets if b.low_n),
    )


def test_estimate_edge_healthy_bucket_uses_market_count_for_ci() -> None:
    summ = _summary(
        [_bucket("0.45-0.50", "2_7d", 0.5, market_count=100, sample_size=250, low_n=False)]
    )
    est = estimate_edge(price=0.47, ttr_bucket="2_7d", calibration=summ)
    assert est.side == "YES"
    assert est.price_bucket == "0.45-0.50"
    assert est.base_rate == 0.5
    assert est.edge == round(0.5 - 0.47, 6)
    assert est.reason is None
    assert est.low_n is False
    assert est.ci_n == 100  # distinct markets, NOT raw sample_size (250)
    assert est.base_rate_ci is not None
    lo, hi = est.base_rate_ci
    assert lo < 0.5 < hi
    # Wilson 95% for p=0.5, n=100 ≈ (0.4038, 0.5962). With n=250 it would be
    # ≈ (0.438, 0.562); the tight tolerance proves market_count drives the CI.
    assert math.isclose(lo, 0.4038, abs_tol=0.002)
    assert math.isclose(hi, 0.5962, abs_tol=0.002)


def test_estimate_edge_low_n_bucket_returns_no_edge() -> None:
    summ = _summary([_bucket("0.05-0.10", "2_7d", 0.04, market_count=3, sample_size=3, low_n=True)])
    est = estimate_edge(price=0.07, ttr_bucket="2_7d", calibration=summ)
    assert est.reason == "low_n"
    assert est.edge is None
    assert est.base_rate_ci is None
    assert est.low_n is True


def test_estimate_edge_no_matching_price_bucket() -> None:
    summ = _summary(
        [_bucket("0.45-0.50", "2_7d", 0.5, market_count=100, sample_size=100, low_n=False)]
    )
    est = estimate_edge(price=0.07, ttr_bucket="2_7d", calibration=summ)
    assert est.reason == "no_bucket"
    assert est.edge is None
    assert est.base_rate is None


def test_estimate_edge_ttr_mismatch_is_no_bucket() -> None:
    summ = _summary(
        [_bucket("0.45-0.50", "2_7d", 0.5, market_count=100, sample_size=100, low_n=False)]
    )
    est = estimate_edge(price=0.47, ttr_bucket="0_3h", calibration=summ)
    assert est.reason == "no_bucket"


@pytest.mark.parametrize("price", [0.0, 1.0, -0.1, 1.5])
def test_estimate_edge_rejects_out_of_range_price(price: float) -> None:
    with pytest.raises(ValueError, match="price"):
        estimate_edge(price=price, ttr_bucket="2_7d", calibration=_summary([]))


def test_estimate_to_dict_shape() -> None:
    summ = _summary(
        [_bucket("0.45-0.50", "2_7d", 0.5, market_count=100, sample_size=100, low_n=False)]
    )
    rendered = estimate_to_dict(estimate_edge(price=0.47, ttr_bucket="2_7d", calibration=summ))
    for key in (
        "side",
        "price",
        "price_bucket",
        "ttr_bucket",
        "base_rate",
        "base_rate_ci",
        "ci_n",
        "edge",
        "reason",
        "sample_size",
        "market_count",
        "low_n",
    ):
        assert key in rendered, f"missing {key}"
    assert rendered["side"] == "YES"
    assert isinstance(rendered["base_rate_ci"], list)


def test_estimate_edge_thin_market_count_withholds_edge_even_if_sample_size_ok() -> None:
    # 30 raw observations but only 3 distinct markets. The CI's independent
    # sample IS market_count, so quoting an edge here is over-confident even
    # though the sample_size-based low_n flag is False. Edge must be withheld,
    # while the raw frequency is still reported for context.
    summ = _summary(
        [_bucket("0.45-0.50", "2_7d", 0.5, market_count=3, sample_size=30, low_n=False)]
    )
    est = estimate_edge(price=0.47, ttr_bucket="2_7d", calibration=summ)
    assert est.reason == "low_n"
    assert est.edge is None
    assert est.base_rate_ci is None
    assert est.low_n is True
    assert est.base_rate == 0.5
    assert est.market_count == 3
    assert est.sample_size == 30


def test_estimate_edge_market_count_at_floor_quotes_edge() -> None:
    # Exactly at the floor (market_count == 10): usable, edge quoted on n=10.
    summ = _summary(
        [_bucket("0.45-0.50", "2_7d", 0.5, market_count=10, sample_size=10, low_n=False)]
    )
    est = estimate_edge(price=0.47, ttr_bucket="2_7d", calibration=summ)
    assert est.reason is None
    assert est.edge == round(0.5 - 0.47, 6)
    assert est.low_n is False
    assert est.ci_n == 10
