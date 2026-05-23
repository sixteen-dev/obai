"""Wallet and trader analysis tools."""

from __future__ import annotations

import re
from typing import Any

from ..clients.data_client import DataClient
from ..logging_config import get_logger

logger = get_logger(__name__)

# EVM-style wallet addresses are exactly 40 hex characters prefixed with `0x`.
# Validating at the tool boundary keeps typos from turning into ambiguous
# "no results" responses from the upstream provider.
_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _validate_wallet_address(address: str) -> None:
    if not isinstance(address, str) or not _EVM_ADDRESS_RE.fullmatch(address.strip()):
        msg = (
            "Invalid wallet address. Expected a 42-character EVM address "
            "(0x followed by 40 hex characters)."
        )
        raise ValueError(msg)


async def get_trader_leaderboard(
    *,
    time_period: str = "ALL",
    order_by: str = "PNL",
    limit: int = 20,
) -> dict[str, Any]:
    """Discover top traders from the official leaderboard.

    Args:
        time_period: DAY, WEEK, MONTH, or ALL.
        order_by: PNL or VOL.
        limit: Maximum traders to return (1-50).

    Returns:
        Dict with ranked trader list and relevant metrics.

    """
    client = DataClient()
    try:
        result = await client.get_leaderboard(
            time_period=time_period,
            order_by=order_by,
            limit=min(limit, 50),
        )
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
    _validate_wallet_address(wallet_address)
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
    _validate_wallet_address(wallet_address)
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
            "x_username": profile.get("x_username"),
            "verified_badge": profile.get("verified_badge"),
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
