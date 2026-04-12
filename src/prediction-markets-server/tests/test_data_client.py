"""Tests for Polymarket trader and wallet data normalization."""

from unittest.mock import AsyncMock, patch

import pytest

from src.clients.data_client import DataClient
from src.config import load_settings


@pytest.fixture(autouse=True)
def _load_settings():
    load_settings()


class TestLeaderboardNormalization:
    @pytest.mark.asyncio
    async def test_get_leaderboard_uses_official_params(self):
        client = DataClient()
        raw = [
            {
                "rank": 1,
                "proxyWallet": "0xabc",
                "userName": "toptrader",
                "vol": "123456.78",
                "pnl": "500.0",
                "xUsername": "toptrader_x",
                "verifiedBadge": True,
            }
        ]

        with patch.object(client, "_get", AsyncMock(return_value=raw)) as mock_get:
            result = await client.get_leaderboard(limit=10)

        mock_get.assert_awaited_once_with(
            client._data_url,
            "/v1/leaderboard",
            {"timePeriod": "ALL", "category": "OVERALL", "orderBy": "PNL", "limit": 10},
        )
        assert result["time_period"] == "ALL"
        assert result["order_by"] == "PNL"
        assert result["trader_count"] == 1
        trader = result["traders"][0]
        assert trader["wallet"] == "0xabc"
        assert trader["display_name"] == "toptrader"
        assert trader["volume"] == 123456.78
        assert trader["pnl"] == 500.0
        assert trader["x_username"] == "toptrader_x"
        assert trader["verified_badge"] is True
        await client.close()

    @pytest.mark.asyncio
    async def test_get_leaderboard_with_day_period_and_vol_order(self):
        client = DataClient()
        raw = [{"rank": 1, "proxyWallet": "0xdef", "userName": "daytrader", "vol": "500.0"}]

        with patch.object(client, "_get", AsyncMock(return_value=raw)) as mock_get:
            result = await client.get_leaderboard(time_period="DAY", order_by="VOL", limit=5)

        mock_get.assert_awaited_once_with(
            client._data_url,
            "/v1/leaderboard",
            {"timePeriod": "DAY", "category": "OVERALL", "orderBy": "VOL", "limit": 5},
        )
        assert result["time_period"] == "DAY"
        assert result["order_by"] == "VOL"
        assert result["trader_count"] == 1
        await client.close()

    @pytest.mark.asyncio
    async def test_get_leaderboard_rejects_invalid_time_period(self):
        client = DataClient()
        with pytest.raises(ValueError, match="Invalid time_period"):
            await client.get_leaderboard(time_period="YEARLY")
        await client.close()

    @pytest.mark.asyncio
    async def test_get_leaderboard_rejects_invalid_order_by(self):
        client = DataClient()
        with pytest.raises(ValueError, match="Invalid order_by"):
            await client.get_leaderboard(order_by="WINS")
        await client.close()

    @pytest.mark.asyncio
    async def test_get_leaderboard_normalizes_case(self):
        client = DataClient()
        raw = [{"rank": 1, "proxyWallet": "0xaaa", "userName": "t", "vol": "1.0"}]

        with patch.object(client, "_get", AsyncMock(return_value=raw)) as mock_get:
            result = await client.get_leaderboard(time_period="week", order_by="pnl")

        mock_get.assert_awaited_once_with(
            client._data_url,
            "/v1/leaderboard",
            {"timePeriod": "WEEK", "category": "OVERALL", "orderBy": "PNL", "limit": 20},
        )
        assert result["time_period"] == "WEEK"
        await client.close()


class TestWalletProfileNormalization:
    @pytest.mark.asyncio
    async def test_get_wallet_profile_uses_public_profile_endpoint(self):
        client = DataClient()
        raw = {
            "proxyWallet": "0xabc",
            "name": "pm_user",
            "xUsername": "pm_user_x",
            "verifiedBadge": True,
            "bio": "Macro markets",
            "profileImage": "https://example.com/avatar.png",
            "createdAt": "2026-04-01T00:00:00Z",
        }

        with patch.object(client, "_get", AsyncMock(return_value=raw)) as mock_get:
            result = await client.get_wallet_profile("0xabc")

        mock_get.assert_awaited_once_with(
            client._profile_url,
            "/public-profile",
            {"address": "0xabc"},
        )
        assert result["wallet"] == "0xabc"
        assert result["display_name"] == "pm_user"
        assert result["x_username"] == "pm_user_x"
        assert result["verified_badge"] is True
        assert result["bio"] == "Macro markets"
        assert result["profile_image"] == "https://example.com/avatar.png"
        assert result["profile_available"] is True
        # Profile endpoint does not return volume/PnL — those fields are absent
        assert "volume_traded" not in result
        assert "pnl" not in result
        await client.close()

    @pytest.mark.asyncio
    async def test_get_wallet_profile_missing_fields_are_none(self):
        """All profile fields are nullable — missing values should be None."""
        client = DataClient()
        raw = {"proxyWallet": "0xdef"}

        with patch.object(client, "_get", AsyncMock(return_value=raw)):
            result = await client.get_wallet_profile("0xdef")

        assert result["wallet"] == "0xdef"
        assert result["display_name"] is None
        assert result["x_username"] is None
        assert result["bio"] is None
        assert result["profile_available"] is True
        await client.close()
