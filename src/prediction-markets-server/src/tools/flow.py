"""Trade flow and holder analysis tools."""

from __future__ import annotations

from typing import Any

from ..clients.data_client import DataClient
from ..logging_config import get_logger

logger = get_logger(__name__)


async def get_trade_flow(
    condition_id: str,
    *,
    limit: int = 50,
) -> dict[str, Any]:
    """Summarize recent buy/sell flow and notable large trades.

    Args:
        condition_id: Market condition ID.
        limit: Maximum trades to analyze.

    Returns:
        Dict with flow summary, large trade counts, and recent prints.
        Includes explicit caveat that this is recent flow, not proof of edge.

    """
    client = DataClient()
    try:
        result = await client.get_trades(condition_id, limit=min(limit, 100))

        trades = result.get("trades", [])

        # Identify large trades (top 10% by size)
        sizes = [t.get("size", 0) for t in trades if t.get("size", 0) > 0]
        large_threshold = sorted(sizes, reverse=True)[max(len(sizes) // 10, 1) - 1] if sizes else 0
        large_trades = [
            t for t in trades if t.get("size", 0) >= large_threshold > 0
        ]

        return {
            "tool": "get_trade_flow",
            "condition_id": condition_id,
            "trade_count": result["trade_count"],
            "buy_count": result["buy_count"],
            "sell_count": result["sell_count"],
            "total_size": result["total_size"],
            "large_trade_count": len(large_trades),
            "large_trade_threshold": round(large_threshold, 2),
            "notable_trades": large_trades[:10],
            "recent_trades": trades[:10],
            "caveat": (
                "This shows recent trade flow only. "
                "Flow direction is not proof of informed edge — "
                "large traders can be wrong and flow can reverse."
            ),
        }
    finally:
        await client.close()


async def get_top_holders(
    condition_id: str,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    """Show holder concentration and risk for a market.

    Args:
        condition_id: Market condition ID.
        limit: Maximum holders to return.

    Returns:
        Dict with top holders, concentration metrics, and risk summary.

    """
    client = DataClient()
    try:
        result = await client.get_holders(condition_id, limit=min(limit, 50))

        concentration = result.get("top5_concentration", 0)
        risk_level = "low"
        if concentration > 0.7:
            risk_level = "high"
        elif concentration > 0.4:
            risk_level = "moderate"

        return {
            "tool": "get_top_holders",
            "condition_id": condition_id,
            "holder_count": result["holder_count"],
            "total_held": result["total_held"],
            "top5_concentration": concentration,
            "concentration_risk": risk_level,
            "holders": result["holders"],
        }
    finally:
        await client.close()
