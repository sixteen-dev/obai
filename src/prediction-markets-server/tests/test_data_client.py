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
    async def test_get_leaderboard_uses_official_field_names(self):
        client = DataClient()
        raw = [
            {
                "rank": 1,
                "proxyWallet": "0xabc",
                "userName": "toptrader",
                "vol": "123456.78",
            }
        ]

        with patch.object(client, "_get", AsyncMock(return_value=raw)) as mock_get:
            result = await client.get_leaderboard(limit=10)

        mock_get.assert_awaited_once_with(
            client._data_url, "/v1/leaderboard", {"limit": 10, "window": "all"}
        )
        assert result["period"] == "all"
        assert result["trader_count"] == 1
        assert result["traders"][0]["wallet"] == "0xabc"
        assert result["traders"][0]["display_name"] == "toptrader"
        assert result["traders"][0]["volume"] == 123456.78
        await client.close()

    @pytest.mark.asyncio
    async def test_get_leaderboard_with_period(self):
        client = DataClient()
        raw = [{"rank": 1, "proxyWallet": "0xdef", "userName": "daytrader", "vol": "500.0"}]

        with patch.object(client, "_get", AsyncMock(return_value=raw)) as mock_get:
            result = await client.get_leaderboard(period="daily", limit=5)

        mock_get.assert_awaited_once_with(
            client._data_url, "/v1/leaderboard", {"limit": 5, "window": "daily"}
        )
        assert result["period"] == "daily"
        assert result["trader_count"] == 1
        await client.close()

    @pytest.mark.asyncio
    async def test_get_leaderboard_rejects_invalid_period(self):
        client = DataClient()
        with pytest.raises(ValueError, match="Invalid period"):
            await client.get_leaderboard(period="yearly")
        await client.close()


class TestWalletProfileNormalization:
    @pytest.mark.asyncio
    async def test_get_wallet_profile_uses_public_profile_endpoint(self):
        client = DataClient()
        raw = {
            "proxyWallet": "0xabc",
            "userName": "pm_user",
            "xUsername": "pm_user_x",
            "bio": "Macro markets",
            "profileImage": "https://example.com/avatar.png",
            "volumeTraded": "9999.5",
            "pnl": "102.25",
            "marketsTraded": 42,
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
        assert result["username"] == "pm_user"
        assert result["x_username"] == "pm_user_x"
        assert result["volume_traded"] == 9999.5
        assert result["pnl"] == 102.25
        assert result["markets_traded"] == 42
        assert result["profile_available"] is True
        await client.close()
