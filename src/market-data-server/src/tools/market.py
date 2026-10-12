"""Market overview tools for market data."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from ..clients.fmp_client import FMPClient
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_market_open, filter_sector_performance

logger = get_logger(__name__)

# US equity venues relevant to this product; foreign exchanges are dropped
# so callers see only the sessions that govern US ticker quotes.
_US_EXCHANGES = frozenset({"NASDAQ", "NYSE"})

# Days tried before giving up: covers a weekend next to a two-day closure.
SNAPSHOT_LOOKBACK_DAYS = 7


async def get_market_snapshot(today: date | None = None) -> dict[str, Any]:
    """Get sector performance for the latest session that has data.

    FMP serves the snapshot per date. A weekend date returns [], and a date
    it has no snapshot for yet returns stale rows from earlier sessions, so
    only rows dated the requested day count. This walks back from today one
    day at a time, at most SNAPSHOT_LOOKBACK_DAYS requests. Today is the UTC
    date: once UTC has rolled past New York's date, that day has no rows.

    Args:
        today: First date to try; defaults to the current UTC date.

    Returns:
        Sector performance rows; each carries its session date

    Raises:
        LookupError: If no date in the lookback has data
        Exception: If sector performance fetch fails
    """
    start = today or datetime.now(UTC).date()
    try:
        settings = get_settings()
        async with FMPClient(settings) as client:
            for back in range(SNAPSHOT_LOOKBACK_DAYS):
                day = start - timedelta(days=back)
                rows = await client.get_sector_performance(day)
                data = [row for row in rows if row.get("date") == day.isoformat()]
                if data:
                    # Filter sector performance (already minimal, but apply for consistency)
                    return {"data": filter_sector_performance(data)}
        raise LookupError(
            f"FMP has no sector snapshot in the {SNAPSHOT_LOOKBACK_DAYS} days through {start}"
        )
    except Exception as e:
        log_error(logger, e, context={"tool": "get_market_snapshot"})
        raise


async def is_market_open() -> dict[str, Any]:
    """Check if the US market is currently open.

    Returns market hours and open/closed status for the US equity venues
    (NASDAQ, NYSE); foreign exchanges are dropped to avoid dumping every
    global exchange into context.

    Returns:
        Market hours and open status for the US exchanges.

    Raises:
        Exception: If market status fetch fails.
    """
    try:
        settings = get_settings()
        async with FMPClient(settings) as client:
            data = await client.is_market_open()
            us_data = [row for row in data if row.get("exchange") in _US_EXCHANGES]
            filtered_data = filter_market_open(us_data)
            return {"data": filtered_data}
    except Exception as e:
        log_error(logger, e, context={"tool": "is_market_open"})
        raise
