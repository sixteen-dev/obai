"""Congressional stock-trade disclosures (STOCK Act periodic transaction reports)."""

from dataclasses import asdict
from datetime import date
from typing import Any

from ..clients.congress_dataset import CongressDataset, TradeFilters
from ..logging_config import get_logger, log_error

logger = get_logger(__name__)

_WINDOW_BASIS = "disclosure_date: US Eastern date the current version of the filing became public"


def _json_row(row: dict[str, Any]) -> dict[str, Any]:
    """Render DATE columns as ISO strings for the tool payload."""
    return {
        key: value.isoformat() if isinstance(value, date) else value for key, value in row.items()
    }


async def get_congress_trades(dataset: CongressDataset, filters: TradeFilters) -> dict[str, Any]:
    """Get congressional trade disclosures matching ``filters``.

    Loads or refreshes the dataset when its check interval has elapsed, then
    returns the newest disclosures first. ``total_available`` counts every
    match so a capped page is never mistaken for the complete answer.

    Args:
        dataset: The server's congressional trade dataset.
        filters: Validated query filters.

    Returns:
        Trades with source attribution, snapshot freshness, and counts.

    Raises:
        Exception: If the dataset cannot be loaded or queried.
    """
    try:
        await dataset.ensure_fresh()
        page = dataset.query(filters)
        trades = [_json_row(row) for row in page.trades]
        return {
            "source": (
                "Official House Clerk and Senate eFD periodic transaction reports, "
                f"via {dataset.repo_url}"
            ),
            "snapshot": dataset.status(),
            "filters": asdict(filters),
            "window_basis": _WINDOW_BASIS,
            "count": len(trades),
            "total_available": page.total_matched,
            "truncated": len(trades) < page.total_matched,
            "trades": trades,
        }
    except Exception as e:
        log_error(logger, e, context={"tool": "get_congress_trades", **asdict(filters)})
        raise
