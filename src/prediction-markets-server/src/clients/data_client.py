"""Data API client for Polymarket trades, holders, and trader data.

Provides access to trade history, market holders, leaderboard data,
and wallet activity from official Polymarket endpoints.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..config import get_settings
from ..logging_config import get_logger, log_error

logger = get_logger(__name__)

_MAX_RETRIES = 2
_RETRY_DELAYS = (0.5, 1.5)


def _is_retryable(status_code: int) -> bool:
    """Check if HTTP status code warrants a retry."""
    return status_code in {429, 500, 502, 503, 504}


class DataClient:
    """Client for Polymarket data/analytics endpoints.

    Handles trade history, holder data, leaderboard, and wallet activity.
    """

    def __init__(self) -> None:
        """Initialize data client with settings."""
        settings = get_settings()
        self._data_url = settings.data_api_base_url.rstrip("/")
        self._profile_url = settings.profile_api_base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=settings.request_timeout)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def get_trades(
        self,
        condition_id: str,
        *,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Get recent trades for a market.

        Args:
            condition_id: Market condition ID.
            limit: Maximum trades to return.

        Returns:
            Dict with trades array and summary stats.

        """
        params: dict[str, Any] = {
            "market": condition_id,
            "limit": min(limit, 100),
        }
        raw = await self._get(self._data_url, "/trades", params)

        trades: list[dict[str, Any]] = []
        if isinstance(raw, list):
            for t in raw:
                if isinstance(t, dict):
                    trades.append(self._normalize_trade(t))
        elif isinstance(raw, dict):
            raw_trades = raw.get("trades", raw.get("data", []))
            if isinstance(raw_trades, list):
                for t in raw_trades:
                    if isinstance(t, dict):
                        trades.append(self._normalize_trade(t))

        # Compute summary
        buy_count = sum(1 for t in trades if t.get("side") == "buy")
        sell_count = sum(1 for t in trades if t.get("side") == "sell")
        total_size = sum(t.get("size", 0) for t in trades)

        return {
            "condition_id": condition_id,
            "trade_count": len(trades),
            "buy_count": buy_count,
            "sell_count": sell_count,
            "total_size": round(total_size, 2),
            "trades": trades,
        }

    async def get_holders(
        self,
        condition_id: str,
        *,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Get top holders for a market.

        Args:
            condition_id: Market condition ID.
            limit: Maximum holders to return.

        Returns:
            Dict with holders array and concentration summary.

        """
        params: dict[str, Any] = {
            "market": condition_id,
            "limit": min(limit, 50),
        }
        raw = await self._get(self._data_url, "/holders", params)

        holders: list[dict[str, Any]] = []
        if isinstance(raw, list):
            for h in raw:
                if isinstance(h, dict):
                    holders.append(self._normalize_holder(h))
        elif isinstance(raw, dict):
            raw_holders = raw.get("holders", raw.get("data", []))
            if isinstance(raw_holders, list):
                for h in raw_holders:
                    if isinstance(h, dict):
                        holders.append(self._normalize_holder(h))

        # Concentration analysis
        total_held = sum(h.get("amount", 0) for h in holders)
        top5_held = sum(h.get("amount", 0) for h in holders[:5])
        concentration_top5 = round(top5_held / total_held, 4) if total_held > 0 else 0

        return {
            "condition_id": condition_id,
            "holder_count": len(holders),
            "total_held": round(total_held, 2),
            "top5_concentration": concentration_top5,
            "holders": holders,
        }

    _VALID_PERIODS = {"daily", "weekly", "monthly", "all"}

    async def get_leaderboard(
        self,
        *,
        period: str = "all",
        limit: int = 20,
    ) -> dict[str, Any]:
        """Get the trader leaderboard.

        Args:
            period: Time window — daily, weekly, monthly, or all.
            limit: Maximum traders to return.

        Returns:
            Dict with ranked trader list.

        Raises:
            ValueError: If period is not a valid option.

        """
        if period not in self._VALID_PERIODS:
            valid = ", ".join(sorted(self._VALID_PERIODS))
            msg = f"Invalid period '{period}'. Must be one of: {valid}"
            raise ValueError(msg)

        params: dict[str, Any] = {"limit": min(limit, 50), "window": period}
        raw = await self._get(self._data_url, "/v1/leaderboard", params)

        traders: list[dict[str, Any]] = []
        if isinstance(raw, list):
            for t in raw:
                if isinstance(t, dict):
                    traders.append(self._normalize_leaderboard_entry(t))
        elif isinstance(raw, dict):
            raw_traders = raw.get("leaderboard", raw.get("data", raw.get("traders", [])))
            if isinstance(raw_traders, list):
                for t in raw_traders:
                    if isinstance(t, dict):
                        traders.append(self._normalize_leaderboard_entry(t))

        return {
            "period": period,
            "trader_count": len(traders),
            "traders": traders,
        }

    async def get_wallet_activity(
        self,
        wallet_address: str,
        *,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Get recent activity for a wallet.

        Args:
            wallet_address: Ethereum wallet address.
            limit: Maximum activity entries.

        Returns:
            Dict with wallet's recent trades and positions.

        """
        params: dict[str, Any] = {
            "user": wallet_address,
            "limit": min(limit, 100),
        }
        raw = await self._get(self._data_url, "/activity", params)

        activities: list[dict[str, Any]] = []
        if isinstance(raw, list):
            for a in raw:
                if isinstance(a, dict):
                    activities.append(self._normalize_activity(a))
        elif isinstance(raw, dict):
            raw_activities = raw.get("activity", raw.get("data", []))
            if isinstance(raw_activities, list):
                for a in raw_activities:
                    if isinstance(a, dict):
                        activities.append(self._normalize_activity(a))

        return {
            "wallet": wallet_address,
            "activity_count": len(activities),
            "activity": activities,
        }

    async def get_wallet_positions(
        self,
        wallet_address: str,
    ) -> dict[str, Any]:
        """Get current positions for a wallet.

        Args:
            wallet_address: Ethereum wallet address.

        Returns:
            Dict with open positions.

        """
        params: dict[str, Any] = {"user": wallet_address}
        raw = await self._get(self._data_url, "/positions", params)

        positions: list[dict[str, Any]] = []
        if isinstance(raw, list):
            for p in raw:
                if isinstance(p, dict):
                    positions.append(self._normalize_position(p))
        elif isinstance(raw, dict):
            raw_positions = raw.get("positions", raw.get("data", []))
            if isinstance(raw_positions, list):
                for p in raw_positions:
                    if isinstance(p, dict):
                        positions.append(self._normalize_position(p))

        return {
            "wallet": wallet_address,
            "position_count": len(positions),
            "positions": positions,
        }

    async def get_wallet_profile(self, wallet_address: str) -> dict[str, Any]:
        """Get public profile for a wallet.

        Args:
            wallet_address: Ethereum wallet address.

        Returns:
            Profile dict with display name and stats.

        """
        try:
            raw = await self._get(self._profile_url, "/public-profile", {"address": wallet_address})
        except (httpx.HTTPStatusError, httpx.RequestError):
            # Profile endpoint may not exist for all wallets
            return {
                "wallet": wallet_address,
                "display_name": None,
                "profile_available": False,
            }

        if not isinstance(raw, dict):
            return {
                "wallet": wallet_address,
                "display_name": None,
                "profile_available": False,
            }

        return {
            "wallet": raw.get("walletAddress") or raw.get("proxyWallet") or wallet_address,
            "display_name": (
                raw.get("name")
                or raw.get("displayName")
                or raw.get("userName")
                or raw.get("username")
                or raw.get("pseudonym")
            ),
            "username": raw.get("userName") or raw.get("username"),
            "x_username": raw.get("xUsername"),
            "bio": raw.get("bio", ""),
            "profile_image": (
                raw.get("profileImage") or raw.get("profileImageUrl") or raw.get("image")
            ),
            "volume_traded": self._maybe_float(
                raw.get("volumeTraded") or raw.get("volume") or raw.get("vol")
            ),
            "pnl": self._maybe_float(
                raw.get("pnl") or raw.get("profit") or raw.get("profitAndLoss")
            ),
            "markets_traded": (
                raw.get("marketsTraded") or raw.get("marketCount") or raw.get("numMarketsTraded")
            ),
            "created_at": raw.get("createdAt"),
            "profile_available": True,
        }

    def _normalize_trade(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize a trade record."""
        return {
            "id": raw.get("id") or raw.get("transactionHash", ""),
            "side": raw.get("side", "").lower(),
            "price": self._safe_float(raw.get("price")),
            "size": self._safe_float(raw.get("size") or raw.get("amount")),
            "timestamp": raw.get("timestamp") or raw.get("createdAt"),
            "wallet": raw.get("maker") or raw.get("user") or raw.get("wallet", ""),
            "outcome": raw.get("outcome", ""),
        }

    def _normalize_holder(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize a holder record."""
        return {
            "wallet": raw.get("address") or raw.get("user") or raw.get("wallet", ""),
            "amount": self._safe_float(raw.get("amount") or raw.get("balance")),
            "outcome": raw.get("outcome", ""),
        }

    def _normalize_leaderboard_entry(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize a leaderboard entry."""
        return {
            "rank": raw.get("rank"),
            "wallet": (
                raw.get("proxyWallet")
                or raw.get("walletAddress")
                or raw.get("address")
                or raw.get("user")
                or raw.get("wallet", "")
            ),
            "display_name": (
                raw.get("userName")
                or raw.get("username")
                or raw.get("name")
                or raw.get("displayName")
            ),
            "volume": self._maybe_float(
                raw.get("vol") or raw.get("volume") or raw.get("volumeTraded")
            ),
            "pnl": self._maybe_float(raw.get("pnl") or raw.get("profit")),
            "markets_traded": (
                raw.get("marketsTraded") or raw.get("marketCount") or raw.get("numMarketsTraded")
            ),
            "positions_won": raw.get("positionsWon") or raw.get("wins") or raw.get("numWon"),
            "positions_lost": (raw.get("positionsLost") or raw.get("losses") or raw.get("numLost")),
        }

    def _normalize_activity(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize an activity record."""
        return {
            "type": raw.get("type", ""),
            "market": raw.get("market") or raw.get("conditionId", ""),
            "title": raw.get("title") or raw.get("question", ""),
            "side": raw.get("side", ""),
            "price": self._safe_float(raw.get("price")),
            "size": self._safe_float(raw.get("size") or raw.get("amount")),
            "timestamp": raw.get("timestamp") or raw.get("createdAt"),
            "outcome": raw.get("outcome", ""),
        }

    def _normalize_position(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize a position record."""
        return {
            "market": raw.get("market") or raw.get("conditionId", ""),
            "title": raw.get("title") or raw.get("question", ""),
            "outcome": raw.get("outcome", ""),
            "size": self._safe_float(raw.get("size") or raw.get("amount")),
            "avg_price": self._safe_float(raw.get("avgPrice") or raw.get("averagePrice")),
            "current_price": self._safe_float(raw.get("currentPrice") or raw.get("price")),
            "pnl": self._safe_float(raw.get("pnl") or raw.get("profit")),
        }

    @staticmethod
    def _safe_float(value: Any) -> float:
        """Safely convert a value to float."""
        if value is None:
            return 0.0
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _maybe_float(value: Any) -> float | None:
        """Safely convert a value to float, preserving missing values."""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    async def _get(self, base_url: str, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET request with retry logic."""
        url = f"{base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await self._client.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if attempt == _MAX_RETRIES or not _is_retryable(exc.response.status_code):
                    log_error(
                        logger,
                        exc,
                        context={"url": url, "status": exc.response.status_code},
                    )
                    raise
                delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                await asyncio.sleep(delay)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt == _MAX_RETRIES:
                    log_error(logger, exc, context={"url": url})
                    raise
                delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                await asyncio.sleep(delay)

        if last_error is not None:
            raise last_error
        msg = "Unexpected retry loop exit"  # pragma: no cover
        raise RuntimeError(msg)  # pragma: no cover
