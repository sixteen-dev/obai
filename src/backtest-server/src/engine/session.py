"""Market session boundary utilities.

Design doc: docs/plans/DUCKDB_INTRADAY_BACKTEST.md, Phase 3.1.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
EARLY_CLOSE_TIME = time(13, 0)
EASTERN = ZoneInfo("America/New_York")

# US market early close dates — 13:00 ET.
# Day after Thanksgiving, Christmas Eve, day before Independence Day.
# Good Friday is a full close (not early close).
# This list covers 2020-2030. Extend as needed.
EARLY_CLOSE_DATES: set[date] = {
    # 2020
    date(2020, 7, 3),
    date(2020, 11, 27),
    date(2020, 12, 24),
    # 2021
    date(2021, 11, 26),
    date(2021, 12, 24),  # Friday — early close
    # 2022
    date(2022, 7, 1),  # Friday before July 4 Monday
    date(2022, 11, 25),
    # 2023
    date(2023, 7, 3),
    date(2023, 11, 24),
    # 2024
    date(2024, 7, 3),
    date(2024, 11, 29),
    date(2024, 12, 24),
    # 2025
    date(2025, 7, 3),
    date(2025, 11, 28),
    date(2025, 12, 24),
    # 2026
    date(2026, 7, 3),  # Friday
    date(2026, 11, 27),
    date(2026, 12, 24),
    # 2027-2030 — extend when needed
}

# Minutes per bar for each timeframe
_MINUTES_PER_BAR: dict[str, int] = {
    "1hour": 60,
    "15min": 15,
    "5min": 5,
}


def session_close_time(dt: date) -> time:
    """Get the market close time for a given date.

    Args:
        dt: Calendar date.

    Returns:
        time(13, 0) for early close dates, time(16, 0) otherwise.

    """
    if dt in EARLY_CLOSE_DATES:
        return EARLY_CLOSE_TIME
    return MARKET_CLOSE


def session_end(dt: date) -> datetime:
    """Get the session end datetime in Eastern time.

    Args:
        dt: Calendar date.

    Returns:
        Datetime of session close in ET.

    """
    return datetime.combine(dt, session_close_time(dt), tzinfo=EASTERN)


def session_start(dt: date) -> datetime:
    """Get the session start datetime in Eastern time.

    Args:
        dt: Calendar date.

    Returns:
        Datetime of session open (9:30 ET).

    """
    return datetime.combine(dt, MARKET_OPEN, tzinfo=EASTERN)


def is_market_hours(ts: datetime) -> bool:
    """Check if a timestamp falls within regular market hours.

    Args:
        ts: Timestamp to check (naive assumed ET, aware converted to ET).

    Returns:
        True if within market hours for that date.

    """
    t = ts.time()
    close = session_close_time(ts.date())
    return MARKET_OPEN <= t < close


def is_last_bar_of_session(
    current_idx: int,
    dates: list[object],
) -> bool:
    """Check if a bar is the last bar of the trading session.

    Uses the next-bar date change method: if the next bar is a different
    calendar date (or doesn't exist), this is the last bar. This naturally
    handles early closes without comparing against hardcoded times.

    Args:
        current_idx: Index of the current bar.
        dates: List of date/datetime values for all bars.

    Returns:
        True if this is the last bar of the session.

    """
    if current_idx >= len(dates) - 1:
        return True  # Last bar in the dataset

    current = dates[current_idx]
    next_bar = dates[current_idx + 1]

    current_day = _extract_date(current)
    next_day = _extract_date(next_bar)

    return current_day != next_day


def bars_remaining_in_session(
    ts: datetime,
    timeframe: str,
) -> int:
    """Estimate bars remaining in the trading session.

    Args:
        ts: Current timestamp.
        timeframe: Bar timeframe (1hour, 15min, 5min).

    Returns:
        Estimated number of bars remaining.

    """
    close = session_close_time(ts.date())
    close_dt = datetime.combine(ts.date(), close)
    remaining_minutes = (close_dt - ts.replace(tzinfo=None)).total_seconds() / 60

    minutes_per_bar = _MINUTES_PER_BAR.get(timeframe, 5)
    return max(0, int(remaining_minutes / minutes_per_bar))


def parse_time_str(time_str: str) -> time:
    """Parse a HH:MM time string.

    Args:
        time_str: Time string like "15:30".

    Returns:
        time object.

    Raises:
        ValueError: If format is invalid.

    """
    parts = time_str.split(":")
    if len(parts) != 2:  # noqa: PLR2004
        msg = f"Invalid time format '{time_str}', expected HH:MM"
        raise ValueError(msg)
    return time(int(parts[0]), int(parts[1]))


def is_after_time(ts: datetime, cutoff: time) -> bool:
    """Check if timestamp is at or after a cutoff time.

    Args:
        ts: Timestamp to check.
        cutoff: Cutoff time.

    Returns:
        True if ts.time() >= cutoff.

    """
    return ts.time() >= cutoff


def _extract_date(val: object) -> date:
    """Extract a date from a date or datetime value."""
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    return date.min  # fallback for non-date types


def next_trading_day(dt: date) -> date:
    """Get the next trading day (skip weekends).

    Args:
        dt: Current date.

    Returns:
        Next weekday date.

    """
    next_day = dt + timedelta(days=1)
    while next_day.weekday() >= 5:  # noqa: PLR2004
        next_day += timedelta(days=1)
    return next_day
