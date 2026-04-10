"""Wallet and trader analysis tools."""

from __future__ import annotations

from typing import Any

from ..clients.data_client import DataClient
from ..logging_config import get_logger

logger = get_logger(__name__)


async def get_trader_leaderboard(
    *,
    period: str = "all",
    limit: int = 20,
) -> dict[str, Any]:
    """Discover top traders from the official leaderboard.

    Args:
        period: Time window — daily, weekly, monthly, or all.
        limit: Maximum traders to return.

    Returns:
        Dict with ranked trader list and relevant metrics.

    """
    client = DataClient()
    try:
        result = await client.get_leaderboard(period=period, limit=min(limit, 50))
        return {
            "tool": "get_trader_leaderboard",
            **result,
        }
    finally:
        await client.close()


async def get_wallet_activity(
    wallet_address: str,
    *,
    limit: int = 50,
) -> dict[str, Any]:
    """Show a wallet's recent trades and active markets.

    Args:
        wallet_address: Ethereum wallet address.
        limit: Maximum activity entries.

    Returns:
        Dict with recent trades, active positions,
        and directional behavior summary.

    """
    client = DataClient()
    try:
        activity = await client.get_wallet_activity(wallet_address, limit=min(limit, 100))
        positions = await client.get_wallet_positions(wallet_address)

        # Summarize directional behavior from recent activity
        activities = activity.get("activity", [])
        buy_count = sum(1 for a in activities if a.get("side", "").lower() == "buy")
        sell_count = sum(1 for a in activities if a.get("side", "").lower() == "sell")
        total = buy_count + sell_count

        if total > 0:
            buy_ratio = buy_count / total
            if buy_ratio > 0.65:
                direction = "net buyer"
            elif buy_ratio < 0.35:
                direction = "net seller"
            else:
                direction = "balanced"
        else:
            direction = "no recent activity"

        return {
            "tool": "get_wallet_activity",
            "wallet": wallet_address,
            "activity_count": activity["activity_count"],
            "recent_direction": direction,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "open_positions": positions["position_count"],
            "activity": activities[:20],
            "positions": positions["positions"][:10],
        }
    finally:
        await client.close()


async def get_wallet_profile(
    wallet_address: str,
) -> dict[str, Any]:
    """Summarize a wallet's trading profile.

    Returns a descriptive summary only — preferred categories,
    recent activity level, and directional tendency.
    Does NOT claim durable alpha without proper historical controls.

    Args:
        wallet_address: Ethereum wallet address.

    Returns:
        Dict with descriptive wallet summary.

    """
    client = DataClient()
    try:
        profile = await client.get_wallet_profile(wallet_address)
        activity = await client.get_wallet_activity(wallet_address, limit=50)
        positions = await client.get_wallet_positions(wallet_address)

        # Categorize activity by market types
        activities = activity.get("activity", [])
        categories: dict[str, int] = {}
        for a in activities:
            title = a.get("title", "")
            # Simple category extraction from title keywords
            for keyword in ["election", "politics", "crypto", "sports", "finance", "tech"]:
                if keyword.lower() in title.lower():
                    categories[keyword] = categories.get(keyword, 0) + 1

        # Activity level
        count = activity.get("activity_count", 0)
        if count > 30:
            activity_level = "high"
        elif count > 10:
            activity_level = "moderate"
        else:
            activity_level = "low"

        return {
            "tool": "get_wallet_profile",
            "wallet": wallet_address,
            "display_name": profile.get("display_name"),
            "volume_traded": profile.get("volume_traded"),
            "pnl": profile.get("pnl"),
            "markets_traded": profile.get("markets_traded"),
            "open_positions": positions.get("position_count", 0),
            "recent_activity_level": activity_level,
            "recent_activity_count": count,
            "category_preferences": categories if categories else None,
            "caveat": (
                "This is a descriptive profile based on recent observable activity. "
                "It does not constitute a reliable measure of trading skill or alpha."
            ),
        }
    finally:
        await client.close()
