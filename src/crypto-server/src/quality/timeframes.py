"""Timeframe parsing, pagination, and coverage helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Iterable

from ..models import Candle, Coverage

GRANULARITY_SECONDS: dict[str, int] = {
    "ONE_MINUTE": 60,
    "FIVE_MINUTE": 5 * 60,
    "FIFTEEN_MINUTE": 15 * 60,
    "ONE_HOUR": 60 * 60,
    "ONE_DAY": 24 * 60 * 60,
}

TIMEFRAME_TO_GRANULARITY: dict[str, str] = {
    "1m": "ONE_MINUTE",
    "1min": "ONE_MINUTE",
    "one_minute": "ONE_MINUTE",
    "ONE_MINUTE": "ONE_MINUTE",
    "5m": "FIVE_MINUTE",
    "5min": "FIVE_MINUTE",
    "five_minute": "FIVE_MINUTE",
    "FIVE_MINUTE": "FIVE_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "15min": "FIFTEEN_MINUTE",
    "fifteen_minute": "FIFTEEN_MINUTE",
    "FIFTEEN_MINUTE": "FIFTEEN_MINUTE",
    "1h": "ONE_HOUR",
    "1hour": "ONE_HOUR",
    "one_hour": "ONE_HOUR",
    "ONE_HOUR": "ONE_HOUR",
    "1d": "ONE_DAY",
    "1day": "ONE_DAY",
    "daily": "ONE_DAY",
    "one_day": "ONE_DAY",
    "ONE_DAY": "ONE_DAY",
}


def parse_time(value: str | int | float | datetime) -> datetime:
    """Parse a UTC datetime from ISO text, Unix seconds, or datetime."""
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, UTC)
    raw = str(value).strip()
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw), UTC)
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def normalize_granularity(timeframe: str) -> str:
    """Convert user timeframe to Coinbase granularity enum."""
    try:
        return TIMEFRAME_TO_GRANULARITY[timeframe]
    except KeyError:
        lowered = timeframe.lower()
        if lowered in TIMEFRAME_TO_GRANULARITY:
            return TIMEFRAME_TO_GRANULARITY[lowered]
        allowed = ", ".join(sorted(TIMEFRAME_TO_GRANULARITY))
        msg = f"Unsupported timeframe {timeframe!r}. Allowed: {allowed}"
        raise ValueError(msg) from None


def granularity_seconds(granularity: str) -> int:
    """Return seconds per candle for a Coinbase granularity enum."""
    try:
        return GRANULARITY_SECONDS[granularity]
    except KeyError:
        msg = f"Unsupported granularity {granularity!r}"
        raise ValueError(msg) from None


def iter_candle_chunks(
    start: datetime,
    end: datetime,
    granularity: str,
) -> Iterable[tuple[int, int]]:
    """Yield deterministic Coinbase candle chunks with at most 350 buckets."""
    step_seconds = granularity_seconds(granularity)
    chunk_seconds = step_seconds * 350
    cursor = int(start.timestamp())
    end_ts = int(end.timestamp())
    while cursor < end_ts:
        chunk_end = min(cursor + chunk_seconds, end_ts)
        yield cursor, chunk_end
        cursor = chunk_end


def compute_coverage(
    candles: list[Candle],
    *,
    requested_start: datetime,
    requested_end: datetime,
    granularity: str,
) -> Coverage:
    """Compute expected/returned candle coverage and gaps."""
    step = granularity_seconds(granularity)
    start_ts = int(requested_start.timestamp())
    end_ts = int(requested_end.timestamp())
    expected = max(0, (end_ts - start_ts + step - 1) // step)
    returned_starts = {c.start_ts for c in candles if start_ts <= c.start_ts < end_ts}

    missing_ranges: list[dict[str, str]] = []
    gap_start: int | None = None
    gap_end: int | None = None
    for ts in range(start_ts, end_ts, step):
        if ts not in returned_starts:
            gap_start = ts if gap_start is None else gap_start
            gap_end = ts + step
        elif gap_start is not None and gap_end is not None:
            missing_ranges.append(_gap_dict(gap_start, gap_end))
            gap_start = None
            gap_end = None
    if gap_start is not None and gap_end is not None:
        missing_ranges.append(_gap_dict(gap_start, gap_end))

    returned = len(returned_starts)
    missing = max(0, expected - returned)
    missing_pct = (missing / expected) if expected else 0.0
    return Coverage(
        start=requested_start.isoformat(),
        end=requested_end.isoformat(),
        expected_intervals=expected,
        returned_intervals=returned,
        missing_intervals=missing,
        missing_pct=missing_pct,
        gap_ranges=missing_ranges[:20],
    )


def snap_start_to_available(
    candles: list[Candle],
    coverage: Coverage,
    *,
    requested_start: datetime,
    requested_end: datetime,
    granularity: str,
) -> tuple[datetime, Coverage]:
    """Advance a leading data gap to the first available candle.

    Coinbase sometimes omits the candle at the requested start. When the only
    gap is at the leading edge, snap the effective start to the first available
    candle so a complete interior window counts as execution grade. Interior or
    trailing gaps remain and still block.

    Args:
        candles: Candles returned for the requested window.
        coverage: Coverage computed over the requested window.
        requested_start: The start the caller asked for.
        requested_end: The end the caller asked for.
        granularity: Coinbase granularity string.

    Returns:
        The effective start and its coverage. Unchanged when there is no
        leading-only gap to snap.

    """
    start_ts = int(requested_start.timestamp())
    end_ts = int(requested_end.timestamp())
    in_range = [c.start_ts for c in candles if start_ts <= c.start_ts < end_ts]
    if coverage.missing_intervals == 0 or not in_range:
        return requested_start, coverage
    first_ts = min(in_range)
    if first_ts <= start_ts:
        return requested_start, coverage
    snapped_start = datetime.fromtimestamp(first_ts, UTC)
    snapped = compute_coverage(
        candles,
        requested_start=snapped_start,
        requested_end=requested_end,
        granularity=granularity,
    )
    if snapped.missing_intervals > 0:
        return requested_start, coverage
    return snapped_start, snapped


def _gap_dict(start_ts: int, end_ts: int) -> dict[str, str]:
    return {
        "start": datetime.fromtimestamp(start_ts, UTC).isoformat(),
        "end": datetime.fromtimestamp(end_ts, UTC).isoformat(),
    }


def latest_observation(candles: list[Candle]) -> str | None:
    """Return latest candle timestamp ISO string."""
    if not candles:
        return None
    return max(c.start for c in candles).isoformat()


def freshness_seconds(observed_at: datetime) -> float:
    """Return age in seconds."""
    return max(0.0, (datetime.now(UTC) - observed_at.astimezone(UTC)).total_seconds())


def timedelta_for_granularity(granularity: str) -> timedelta:
    """Return timedelta for granularity."""
    return timedelta(seconds=granularity_seconds(granularity))
