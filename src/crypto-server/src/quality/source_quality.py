"""Source-quality construction helpers."""

from __future__ import annotations

from ..models import Candle, Coverage, SourceQuality
from .timeframes import latest_observation


def build_candle_source_quality(
    *,
    product_id: str,
    candles: list[Candle],
    coverage: Coverage,
    execution_grade_required: bool,
    max_missing_pct_execution: float,
    fetch_failed: bool,
) -> SourceQuality:
    """Build a fail-closed source-quality block for Coinbase candles."""
    warnings: list[str] = []
    blocking = False
    if coverage.missing_intervals > 0:
        warnings.append("missing_candles")
    if fetch_failed:
        warnings.append("coinbase_fetch_failed")
    # A window reaching past the last closed bar is assessed over less than
    # was asked for, and the shortfall never appears in missing_pct because
    # the unclosed bars are excluded from expected too. Judging that window
    # execution-grade reports complete coverage of data that does not exist.
    truncated = coverage.evaluated_end < coverage.end
    if truncated:
        warnings.append("requested_window_not_yet_closed")
    if execution_grade_required and (
        truncated
        or coverage.missing_pct > max_missing_pct_execution
        or (fetch_failed and coverage.missing_intervals > 0)
    ):
        warnings.append("blocking_missing_candles")
        blocking = True

    return SourceQuality(
        product_id=product_id,
        latest_observation_at=latest_observation(candles),
        freshness_seconds=None,
        coverage=coverage,
        limitations=["Coinbase public market data; no historical queue reconstruction"],
        warnings=warnings,
        blocking_quality_warning=blocking,
        execution_grade=not blocking and coverage.missing_pct <= max_missing_pct_execution,
    )
