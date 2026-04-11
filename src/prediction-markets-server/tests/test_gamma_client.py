"""Tests for Gamma API client."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.clients.gamma_client import GammaClient
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


@pytest.fixture
def sample_market_response():
    return {
        "conditionId": "0xabc123",
        "questionID": "0xdef456",
        "slug": "will-btc-hit-100k",
        "question": "Will Bitcoin hit $100K by end of 2026?",
        "description": "This market resolves Yes if...",
        "outcomes": ["Yes", "No"],
        "outcomePrices": ["0.65", "0.35"],
        "bestBid": 0.64,
        "bestAsk": 0.66,
        "spread": 0.02,
        "lastTradePrice": 0.65,
        "volume": 500000,
        "volume24hr": 25000,
        "volume1wk": 100000,
        "volume1mo": 350000,
        "liquidity": 150000,
        "startDate": "2026-01-01T00:00:00Z",
        "endDate": "2026-12-31T00:00:00Z",
        "active": True,
        "closed": False,
        "archived": False,
        "negRisk": False,
        "clobTokenIds": ["token_yes_123", "token_no_456"],
        "groupItemTitle": "",
        "resolutionSource": "CoinGecko",
        "acceptingOrders": True,
        "orderMinSize": 5,
        "orderPriceMinTickSize": 0.01,
        "oneWeekPriceChange": 0.05,
        "events": [{"slug": "btc-100k", "category": "crypto"}],
    }


class TestGammaClientNormalization:
    def test_normalize_market_extracts_core_fields(self, sample_market_response):
        client = GammaClient()
        result = client._normalize_market(sample_market_response)

        assert result["condition_id"] == "0xabc123"
        assert result["question"] == "Will Bitcoin hit $100K by end of 2026?"
        assert result["outcomes"] == ["Yes", "No"]
        assert result["outcome_prices"] == [0.65, 0.35]
        assert result["best_bid"] == 0.64
        assert result["best_ask"] == 0.66
        assert result["spread"] == 0.02
        assert result["volume_24h"] == 25000
        assert result["liquidity"] == 150000
        assert result["active"] is True
        assert result["closed"] is False
        assert result["category"] == "crypto"

    def test_normalize_market_handles_empty_dict(self):
        client = GammaClient()
        result = client._normalize_market({})
        assert result["condition_id"] == ""
        assert result["outcomes"] == []
        assert result["outcome_prices"] == []

    def test_normalize_market_handles_non_dict(self):
        client = GammaClient()
        result = client._normalize_market("not a dict")
        assert result == {}

    def test_normalize_market_handles_invalid_prices(self):
        client = GammaClient()
        result = client._normalize_market(
            {
                "outcomes": ["Yes", "No"],
                "outcomePrices": ["not_a_number", None],
            }
        )
        assert result["outcome_prices"] == [0.0, 0.0]

    def test_normalize_market_parses_json_string_fields(self):
        """Gamma API sometimes returns array fields as JSON strings."""
        client = GammaClient()
        result = client._normalize_market(
            {
                "outcomes": '["Yes","No"]',
                "outcomePrices": '["0.9995","0.0005"]',
                "clobTokenIds": '["token_yes","token_no"]',
                "volume": "148719163.72",
                "liquidity": "35659441.80",
            }
        )
        assert result["outcomes"] == ["Yes", "No"]
        assert result["outcome_prices"] == [0.9995, 0.0005]
        assert result["clob_token_ids"] == ["token_yes", "token_no"]
        assert result["volume"] == pytest.approx(148719163.72)
        assert result["liquidity"] == pytest.approx(35659441.80)

    def test_normalize_market_handles_string_volume(self):
        client = GammaClient()
        result = client._normalize_market({"volume": "500000.50"})
        assert result["volume"] == 500000.50

    def test_normalize_market_uses_event_slug_for_url(self):
        """market_url should use the parent event slug, not the market slug."""
        client = GammaClient()
        result = client._normalize_market(
            {
                "slug": "will-btc-hit-100k-by-end-of-2026",
                "events": [{"slug": "btc-100k", "category": "crypto"}],
            }
        )
        assert result["market_url"] == "https://polymarket.com/event/btc-100k"
        assert result["slug"] == "will-btc-hit-100k-by-end-of-2026"

    def test_normalize_market_empty_url_when_no_event_slug(self):
        """No event slug means no market_url."""
        client = GammaClient()
        result = client._normalize_market(
            {"slug": "some-market", "events": [{"category": "crypto"}]}
        )
        assert result["market_url"] == ""


class TestGammaClientSearchMarkets:
    @pytest.mark.asyncio
    async def test_search_markets_returns_normalized_results(self, sample_market_response):
        client = GammaClient()
        mock_resp = _mock_httpx_response([sample_market_response])

        with patch.object(client._client, "get", AsyncMock(return_value=mock_resp)):
            results = await client.search_markets(query="bitcoin", limit=5)

        assert len(results) == 1
        assert results[0]["question"] == "Will Bitcoin hit $100K by end of 2026?"
        assert results[0]["condition_id"] == "0xabc123"
        await client.close()

    @pytest.mark.asyncio
    async def test_search_markets_empty_response(self):
        client = GammaClient()
        mock_resp = _mock_httpx_response([])

        with patch.object(client._client, "get", AsyncMock(return_value=mock_resp)):
            results = await client.search_markets(query="nonexistent")

        assert results == []
        await client.close()


class TestGammaClientGetMarket:
    @pytest.mark.asyncio
    async def test_get_market_by_condition_id(self, sample_market_response):
        """Condition ID (0x...) triggers broad search + client-side match."""
        client = GammaClient()
        # The conditionId branch calls /markets?limit=100 and matches client-side,
        # so the response must be a list containing the target market.
        mock_resp = _mock_httpx_response([sample_market_response])

        with patch.object(client._client, "get", AsyncMock(return_value=mock_resp)):
            result = await client.get_market("0xabc123")

        assert result["condition_id"] == "0xabc123"
        assert result["slug"] == "will-btc-hit-100k"
        await client.close()

    @pytest.mark.asyncio
    async def test_get_market_by_slug(self, sample_market_response):
        """Non-0x, non-numeric identifiers route through slug lookup."""
        client = GammaClient()
        mock_resp = _mock_httpx_response([sample_market_response])

        with patch.object(client._client, "get", AsyncMock(return_value=mock_resp)):
            result = await client.get_market("will-btc-hit-100k")

        assert result["slug"] == "will-btc-hit-100k"
        await client.close()

    @pytest.mark.asyncio
    async def test_get_market_raises_on_404(self):
        client = GammaClient()
        response = httpx.Response(404, request=httpx.Request("GET", "http://test"))
        error = httpx.HTTPStatusError("Not Found", request=response.request, response=response)

        with (
            patch.object(client._client, "get", AsyncMock(side_effect=error)),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await client.get_market("nonexistent")

        await client.close()
