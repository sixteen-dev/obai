"""estimate_market_edge — live price vs our own measured base rate (§10.7).

The de-risked analog of "model says 55%, market says 45%": given a live YES
price and time-to-resolution, look up the realized base rate for the matching
(price-bucket, ttr-bucket) from our own resolved-market calibration and report
the gap with a Wilson interval. Sourced entirely from our cached Polymarket
data — never an imported table or a price-process simulation.

Reuses ``analyze_prediction_calibration`` for the universe (forced to
``market_bucket_once`` so the CI's trials are independent) and ``_resolve_market``
for slug → live price, so this tool adds no new discovery/backfill path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..data import HistoryDownloader
from ..engine import (
    DEFAULT_PRICE_BUCKET_SIZE,
    CalibrationBucket,
    CalibrationSummary,
    estimate_edge,
    estimate_to_dict,
    time_to_resolution_bucket,
)
from ..logging_config import get_logger
from ..storage import PredictionStore, fingerprint_analysis
from .calibration import analyze_prediction_calibration
from .market_state import _resolve_market

logger = get_logger(__name__)

_BASE_RATE_LIMITATIONS = (
    "Base rate is a population average for this price/TTR bucket, not a forecast "
    "for this specific market.",
    "Edge is YES-side; for a NO trade use edge_no = -edge_yes, which is the NO "
    "edge at this tool's reference price only. Re-derive the executable NO edge "
    "from the NO token's best ask in the market-state snapshot before acting.",
)


async def estimate_market_edge(
    *,
    downloader: HistoryDownloader,
    store: PredictionStore,
    slug: str = "",
    condition_id: str = "",
    market_url: str = "",
    price: float | None = None,
    days_to_resolution: int | None = None,
    category: str = "",
    max_markets: int = 200,
    fidelity: int = 60,
    price_bucket_size: float = DEFAULT_PRICE_BUCKET_SIZE,
    max_history_points: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Estimate the YES-side edge of a live market against our own calibration.

    Provide a market identifier (``slug`` preferred, else ``market_url`` or
    ``condition_id``) to read the live price + TTR, or pass explicit
    ``price`` + ``days_to_resolution`` to skip the live fetch.

    Args:
        downloader: Backfill orchestrator (constructed by the MCP wrapper).
        store: PredictionStore for read-back after backfill.
        slug: Market slug (preferred identifier).
        condition_id: Market condition id (fallback identifier).
        market_url: Market URL; its last path segment is used as the slug.
        price: Explicit live YES price in (0, 1); pair with ``days_to_resolution``.
        days_to_resolution: Explicit days until resolution; pair with ``price``.
        category: Optional Gamma tag for the calibration universe. When the
            matching bucket is empty/thin the universe broadens to all
            resolved markets (``edge_universe_broadened``).
        max_markets: Cap on the calibration universe size.
        fidelity: Price-history fidelity in minutes.
        price_bucket_size: Bucket width; must match the calibration run.
        max_history_points: Per-token CLOB cap from settings.
        now: Optional override; defaults to current UTC.

    Returns:
        Dict with the YES-side edge estimate, the calibration universe, data
        coverage/quality, and the mandatory base-rate limitations.

    Raises:
        ValueError: If neither an identifier nor an explicit price+TTR is
            given (or only one of the explicit pair is given).

    """
    now = now or datetime.now(tz=timezone.utc)
    yes_price, ttr_bucket, identifier = await _resolve_target(
        downloader,
        slug=slug,
        condition_id=condition_id,
        market_url=market_url,
        price=price,
        days_to_resolution=days_to_resolution,
        now=now,
    )

    calib = await _run_calibration(
        downloader,
        store,
        category=category,
        kwargs=_calib_kwargs(
            max_markets=max_markets,
            fidelity=fidelity,
            price_bucket_size=price_bucket_size,
            max_history_points=max_history_points,
            now=now,
        ),
    )
    summary = _summary_from_metrics(calib)
    estimate = estimate_edge(
        price=yes_price,
        ttr_bucket=ttr_bucket,
        calibration=summary,
        price_bucket_size=price_bucket_size,
    )
    universe = f"tag:{category}" if category else "all_resolved"
    broadened = False
    # Bounded fallback (one retry max): a category that yields no usable
    # bucket broadens to all resolved markets so the agent still gets a read.
    if category and estimate.reason is not None:
        calib = await _run_calibration(
            downloader,
            store,
            category="",
            kwargs=_calib_kwargs(
                max_markets=max_markets,
                fidelity=fidelity,
                price_bucket_size=price_bucket_size,
                max_history_points=max_history_points,
                now=now,
            ),
        )
        summary = _summary_from_metrics(calib)
        estimate = estimate_edge(
            price=yes_price,
            ttr_bucket=ttr_bucket,
            calibration=summary,
            price_bucket_size=price_bucket_size,
        )
        universe = "all_resolved"
        broadened = True

    return _build_response(
        estimate=estimate,
        identifier=identifier,
        universe=universe,
        broadened=broadened,
        calib=calib,
        price_bucket_size=price_bucket_size,
    )


# -- target resolution --------------------------------------------------------


async def _resolve_target(
    downloader: HistoryDownloader,
    *,
    slug: str,
    condition_id: str,
    market_url: str,
    price: float | None,
    days_to_resolution: int | None,
    now: datetime,
) -> tuple[float, str, dict[str, Any]]:
    """Resolve (yes_price, ttr_bucket, identifier_echo) from explicit values or a live market."""
    if price is not None and days_to_resolution is not None:
        if days_to_resolution < 0:
            msg = f"days_to_resolution must be >= 0; got {days_to_resolution}"
            raise ValueError(msg)
        ttr = time_to_resolution_bucket(now, now + timedelta(days=days_to_resolution))
        return price, ttr, {}
    if price is not None or days_to_resolution is not None:
        msg = "Explicit pricing needs BOTH price and days_to_resolution."
        raise ValueError(msg)

    resolved_slug = slug or _slug_from_url(market_url)
    if not resolved_slug and not condition_id:
        msg = "Provide slug, market_url, or condition_id — or explicit price + days_to_resolution."
        raise ValueError(msg)
    market = await _resolve_market(downloader.gamma, slug=resolved_slug, condition_id=condition_id)
    ttr = time_to_resolution_bucket(now, _parse_end_date(market))
    echo = {
        "slug": market.get("slug", resolved_slug),
        "condition_id": market.get("condition_id", condition_id),
    }
    return _yes_price(market), ttr, echo


def _slug_from_url(market_url: str) -> str:
    """Extract the trailing slug from a Polymarket URL (empty input → empty)."""
    if not market_url:
        return ""
    return market_url.rstrip("/").split("/")[-1].split("?")[0]


def _yes_price(market: dict[str, Any]) -> float:
    """Read the live YES price by matching the outcome label, never by position.

    Gamma markets are not always ordered ``['Yes', 'No']`` — many are
    ``['No', 'Yes']`` or ``['Up', 'Down']`` — so reading ``outcome_prices[0]``
    positionally would report the wrong side against this tool's YES-side
    contract and flip the edge sign. Pair ``outcomes`` with ``outcome_prices``
    index-for-index (as the Gamma normalizer emits them) and pick the price
    whose label is "yes" (case-insensitive). The normalizer preserves
    unparseable prices as ``None``, so raise a clear error rather than let
    ``float(None)`` crash with an opaque ``TypeError``.

    Raises:
        ValueError: If there are no outcome_prices, no YES-labeled outcome, or
            the YES price is unparseable/unavailable (``None``).

    """
    prices = market.get("outcome_prices") or []
    if not prices:
        msg = "Market has no outcome_prices; cannot read a live YES price."
        raise ValueError(msg)
    outcomes = market.get("outcomes") or []
    yes_index = next(
        (i for i, label in enumerate(outcomes) if str(label).strip().lower() == "yes"),
        None,
    )
    if yes_index is None:
        msg = (
            f"Market has no 'Yes' outcome (available outcomes: {outcomes}); "
            "cannot read a YES price."
        )
        raise ValueError(msg)
    yes_price = prices[yes_index] if yes_index < len(prices) else None
    if yes_price is None:
        msg = f"YES price is unparseable/unavailable for this market (outcome_prices: {prices})."
        raise ValueError(msg)
    return float(yes_price)


def _parse_end_date(market: dict[str, Any]) -> datetime:
    """Parse the market end_date into an aware UTC datetime."""
    raw = market.get("end_date")
    if not isinstance(raw, str) or not raw:
        msg = "Market has no end_date; cannot compute time-to-resolution."
        raise ValueError(msg)
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


# -- calibration universe -----------------------------------------------------


def _calib_kwargs(
    *,
    max_markets: int,
    fidelity: int,
    price_bucket_size: float,
    max_history_points: int,
    now: datetime,
) -> dict[str, Any]:
    """Shared calibration kwargs (mode forced to market_bucket_once for independence)."""
    return {
        "max_markets": max_markets,
        "fidelity": fidelity,
        "price_bucket_size": price_bucket_size,
        "sampling_mode": "market_bucket_once",
        "max_history_points": max_history_points,
        "now": now,
    }


async def _run_calibration(
    downloader: HistoryDownloader,
    store: PredictionStore,
    *,
    category: str,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Run analyze_prediction_calibration over the tagged (or all-resolved) universe."""
    return await analyze_prediction_calibration(
        downloader=downloader, store=store, category=category, **kwargs
    )


def _summary_from_metrics(calib: dict[str, Any]) -> CalibrationSummary:
    """Rebuild a CalibrationSummary from the calibration tool's market_bucket_once metrics."""
    metrics = calib.get("metrics", {}).get("market_bucket_once", {})
    buckets = [
        CalibrationBucket(
            price_bucket=row["price_bucket"],
            ttr_bucket=row["ttr_bucket"],
            sample_size=row["sample_size"],
            market_count=row["market_count"],
            implied_probability=row["implied_probability"],
            realized_frequency=row["realized_frequency"],
            excess_return=row["excess_return"],
            brier_score=row["brier_score"],
            log_loss=row["log_loss"],
            low_n=row["low_n"],
        )
        for row in metrics.get("buckets", [])
    ]
    return CalibrationSummary(
        sampling_mode="market_bucket_once",
        sample_size=metrics.get("sample_size", 0),
        raw_observation_count=metrics.get("raw_observation_count", 0),
        market_count=metrics.get("market_count", 0),
        effective_n=metrics.get("effective_n", 0),
        overall_brier=metrics.get("overall_brier", 0.0),
        overall_log_loss=metrics.get("overall_log_loss", 0.0),
        expected_calibration_error=metrics.get("expected_calibration_error", 0.0),
        buckets=buckets,
        low_n_bucket_count=metrics.get("low_n_bucket_count", 0),
    )


# -- response -----------------------------------------------------------------


def _build_response(
    *,
    estimate: Any,
    identifier: dict[str, Any],
    universe: str,
    broadened: bool,
    calib: dict[str, Any],
    price_bucket_size: float,
) -> dict[str, Any]:
    """Assemble the §10.7 response from the estimate + the calibration universe."""
    # Fold the calibration buckets into the fingerprint so a resolution change
    # or price-history shift that moves a market between buckets flips the token
    # even when params + selected IDs are unchanged (mirrors backtest_rule's
    # resolution-fingerprint guard). Buckets arrive sorted from calibration;
    # re-sort defensively so the digest never depends on emission order.
    buckets = calib.get("metrics", {}).get("market_bucket_once", {}).get("buckets", [])
    fingerprint = fingerprint_analysis(
        tool_name="estimate_market_edge",
        params={
            "price": estimate.price,
            "ttr_bucket": estimate.ttr_bucket,
            "price_bucket_size": price_bucket_size,
            "calibration_universe": universe,
            "selected": sorted(calib.get("selected_condition_ids", [])),
            "calibration_buckets": sorted(
                buckets, key=lambda b: (b["price_bucket"], b["ttr_bucket"])
            ),
        },
        universe_fingerprint="",
        resolution_fingerprints=[],
    )
    return {
        "tool": "estimate_market_edge",
        **estimate_to_dict(estimate),
        "identifier": identifier,
        "calibration_universe": universe,
        "edge_universe_broadened": broadened,
        "data_coverage": calib.get("data_coverage"),
        "quality_flags": calib.get("quality_flags", []),
        "reliability_label": calib.get("reliability_label"),
        "data_fingerprint": fingerprint,
        "limitations": [*_BASE_RATE_LIMITATIONS, *calib.get("limitations", [])],
    }
