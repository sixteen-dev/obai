"""Tests for CLOB API client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.clients.clob_client import ClobClient
from src.config import load_settings


@pytest.fixture(autouse=True)
def _load_settings():
    load_settings()


def _mock_httpx_response(json_data):
    """Create a mock httpx response with sync json() and raise_for_status()."""
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    resp.status_code = 200
    return resp


class TestClobClientOrderBook:
    def test_normalize_book_with_levels(self):
        client = ClobClient()
        raw = {
            "bids": [
                {"price": "0.64", "size": "1000"},
                {"price": "0.63", "size": "2000"},
                {"price": "0.62", "size": "500"},
            ],
            "asks": [
                {"price": "0.66", "size": "800"},
                {"price": "0.67", "size": "1500"},
            ],
        }
        result = client._normalize_book(raw, "token_123")

        assert result["token_id"] == "token_123"
        assert result["best_bid"] == 0.64
        assert result["best_ask"] == 0.66
        assert result["midpoint"] == 0.65
        assert result["spread"] == 0.02
        assert len(result["bids"]) == 3
        assert len(result["asks"]) == 2
        assert result["bid_depth_top5"] == 3500.0
        assert result["ask_depth_top5"] == 2300.0

    def test_normalize_book_empty(self):
        client = ClobClient()
        result = client._normalize_book({}, "token_123")

        assert result["token_id"] == "token_123"
        assert result["bids"] == []
        assert result["asks"] == []
        assert result["best_bid"] is None
        assert result["best_ask"] is None

    def test_normalize_book_preserves_zero_price_levels(self):
        client = ClobClient()
        raw = {
            "bids": [{"price": "0.00", "size": "100"}],
            "asks": [{"price": "0.05", "size": "200"}],
        }

        result = client._normalize_book(raw, "token_123")

        assert result["best_bid"] == 0.0
        assert result["best_ask"] == 0.05
        assert result["midpoint"] == 0.025
        assert result["spread"] == 0.05

    def test_normalize_book_non_dict(self):
        client = ClobClient()
        result = client._normalize_book("not a dict", "token_123")
        assert result["bids"] == []


class TestClobClientPriceHistory:
    @pytest.mark.asyncio
    async def test_get_price_history_parses_points(self):
        client = ClobClient()
        raw_response = {
            "history": [
                {"t": 1700000000, "p": "0.55"},
                {"t": 1700003600, "p": "0.58"},
                {"t": 1700007200, "p": "0.60"},
            ],
        }
        mock_resp = _mock_httpx_response(raw_response)

        with patch.object(client._client, "get", AsyncMock(return_value=mock_resp)):
            result = await client.get_price_history("token_123", interval="1h", fidelity=3)

        assert result["token_id"] == "token_123"
        assert result["count"] == 3
        assert result["history"][0]["price"] == 0.55
        assert result["history"][2]["price"] == 0.60
        await client.close()

    @pytest.mark.asyncio
    async def test_get_price_history_empty(self):
        client = ClobClient()
        mock_resp = _mock_httpx_response({"history": []})

        with patch.object(client._client, "get", AsyncMock(return_value=mock_resp)):
            result = await client.get_price_history("token_123")

        assert result["count"] == 0
        assert result["history"] == []
        await client.close()


class TestClobClientPrice:
    @pytest.mark.asyncio
    async def test_get_price_computes_spread(self):
        client = ClobClient()
        mock_resp = _mock_httpx_response({"bid": "0.64", "ask": "0.66"})

        with patch.object(client._client, "get", AsyncMock(return_value=mock_resp)):
            result = await client.get_price("token_123")

        assert result["bid"] == 0.64
        assert result["ask"] == 0.66
        assert result["spread"] == pytest.approx(0.02)
        await client.close()
