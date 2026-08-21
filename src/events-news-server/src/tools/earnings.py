"""Earnings tools for earnings data by ticker symbol."""

from datetime import date, datetime
from typing import Any

from ..clients.fmp_client import FMPClient
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_earnings, filter_earnings_calendar

logger = get_logger(__name__)

_OVERFETCH_BUFFER = 10

# Hard cap: the market-wide calendar can span thousands of rows over a
# wide date range, so bound the returned payload.
_CALENDAR_MAX_ROWS = 250


def _is_reported(row: dict[str, Any]) -> bool:
    """Return True when the row has an actual (reported) EPS or revenue.

    Args:
        row: A filtered earnings record.

    Returns:
        True if actual EPS or actual revenue is present (non-null).
    """
    return row.get("epsActual") is not None or row.get("revenueActual") is not None


def _parse_date(row: dict[str, Any]) -> date:
    """Parse the earnings date; missing/invalid dates sort oldest.

    Args:
        row: A filtered earnings record.

    Returns:
        The parsed date, or ``date.min`` when absent or unparseable.
    """
    raw = row.get("date")
    if not isinstance(raw, str):
        return date.min
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return date.min


def _earnings_sort_key(row: dict[str, Any]) -> tuple[int, int]:
    """Sort key: reported rows first, then by date descending.

    Args:
        row: A filtered earnings record.

    Returns:
        A tuple of (group, negated date ordinal) where reported rows use
        group 0 (sorted before estimated group 1) and the negated ordinal
        orders each group most-recent-first.
    """
    group = 0 if _is_reported(row) else 1
    return (group, -_parse_date(row).toordinal())


async def get_earnings(
    symbol: str,
    limit: int = 5,
) -> dict[str, Any]:
    """Get earnings history and upcoming earnings for a specific ticker.

    Over-fetches from FMP, then sorts so reported results (actual EPS or
    revenue present) lead estimated/upcoming entries, each group ordered
    by date descending (most recent first), before truncating to ``limit``.
    The over-fetch gives the sort enough reported and upcoming rows to fill
    the returned prefix.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        limit: Maximum number of earnings records to return (default: 5)

    Returns:
        Earnings records with metadata.

    Raises:
        Exception: If earnings fetch fails
    """
    try:
        settings = get_settings()
        fetch_limit = limit + _OVERFETCH_BUFFER
        async with FMPClient(settings) as client:
            data = await client.get_earnings(symbol, fetch_limit)
        sorted_data = sorted(filter_earnings(data), key=_earnings_sort_key)
        return {
            "symbol": symbol,
            "count": len(sorted_data[:limit]),
            "earnings": sorted_data[:limit],
        }
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "get_earnings",
                "symbol": symbol,
                "limit": limit,
            },
        )
        raise


def _validate_calendar_date(label: str, value: str) -> date:
    """Parse a YYYY-MM-DD calendar bound, failing loud on bad input.

    Args:
        label: Field name used in the error message.
        value: The date string to validate.

    Returns:
        The parsed date.

    Raises:
        ValueError: If ``value`` is not a valid YYYY-MM-DD date.
    """
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError) as e:
        raise ValueError(f"{label} must be YYYY-MM-DD, got {value!r}") from e


async def get_earnings_calendar(
    from_date: str,
    to_date: str,
    limit: int = 100,
) -> dict[str, Any]:
    """Get the market-wide earnings calendar for a date range.

    Answers cross-company / date-range questions ("who reports next week?")
    that the per-ticker ``get_earnings`` tool cannot. Rows are returned in
    date order and bounded to ``_CALENDAR_MAX_ROWS``; when the range holds
    more, the earliest dates are kept and ``truncated`` says so, so a partial
    calendar is never mistaken for the whole market.

    Args:
        from_date: Start date in YYYY-MM-DD format (inclusive).
        to_date: End date in YYYY-MM-DD format (inclusive).
        limit: Maximum rows to return (default 100, hard-capped at 250).

    Returns:
        Earnings-calendar records in date order, with ``count``,
        ``total_available`` and ``truncated`` describing what was kept.

    Raises:
        ValueError: If dates are malformed or ``from_date`` is after ``to_date``.
        Exception: If the calendar fetch fails.
    """
    start = _validate_calendar_date("from_date", from_date)
    end = _validate_calendar_date("to_date", to_date)
    if start > end:
        raise ValueError(f"from_date {from_date} is after to_date {to_date}")
    capped = min(max(limit, 1), _CALENDAR_MAX_ROWS)
    try:
        settings = get_settings()
        async with FMPClient(settings) as client:
            data = await client.get_earnings_calendar(from_date, to_date)
        # Sort before capping. The provider returns rows in no useful order, so
        # slicing them can drop whole days out of the middle of the window and
        # still look like a complete answer. Date order makes the cap a
        # contiguous prefix, which is what a "who reports next" question wants.
        ordered = sorted(
            filter_earnings_calendar(data),
            key=lambda row: (str(row.get("date") or ""), str(row.get("symbol") or "")),
        )
        capped_rows = ordered[:capped]
        return {
            "from": from_date,
            "to": to_date,
            "count": len(capped_rows),
            "total_available": len(ordered),
            "truncated": len(capped_rows) < len(ordered),
            "earnings_calendar": capped_rows,
        }
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "get_earnings_calendar",
                "from_date": from_date,
                "to_date": to_date,
                "limit": limit,
            },
        )
        raise
