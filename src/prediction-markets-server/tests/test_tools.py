"""Tests for prediction market tool implementations."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.config import load_settings
from src.tools.backtest import backtest_prediction_setup
from src.tools.discovery import (
    explore_trending_markets,
    get_market_details,
    search_prediction_markets,
)
from src.tools.market_state import compare_prediction_markets, get_market_snapshot


@pytest.fixture(autouse=True)
def _load_settings():
    load_settings()


def _mock_gamma_market():
    return {
        "condition_id": "0xabc123",
        "question_id": "0xdef456",
        "slug": "will-btc-hit-100k",
        "market_url": "https://polymarket.com/event/btc-100k",
        "question": "Will Bitcoin hit $100K?",
        "description": "Resolves Yes if...",
        "outcomes": ["Yes", "No"],
        "outcome_prices": [0.65, 0.35],
        "best_bid": 0.64,
        "best_ask": 0.66,
        "spread": 0.02,
        "last_trade_price": 0.65,
        "volume": 500000,
        "volume_24h": 25000,
        "volume_1w": 100000,
        "volume_1m": 350000,
        "liquidity": 150000,
        "start_date": "2026-01-01T00:00:00Z",
        "end_date": "2026-12-31T00:00:00Z",
        "active": True,
        "closed": False,
        "archived": False,
        "neg_risk": False,
        "clob_token_ids": ["token_yes", "token_no"],
        "group_item_title": "",
        "resolution_source": "CoinGecko",
        "category": "crypto",
        "accepting_orders": True,
        "order_min_size": 5,
        "tick_size": 0.01,
        "one_week_price_change": 0.05,
    }


class TestSearchPredictionMarkets:
    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        mock_market = _mock_gamma_market()

        with patch("src.tools.discovery.GammaClient") as MockGamma:
            instance = AsyncMock()
            instance.search_markets = AsyncMock(return_value=[mock_market])
            instance.close = AsyncMock()
            MockGamma.return_value = instance

            result = await search_prediction_markets("bitcoin", limit=5)

        assert result["tool"] == "search_prediction_markets"
        assert result["count"] == 1
        assert result["markets"][0]["question"] == "Will Bitcoin hit $100K?"
        assert result["markets"][0]["condition_id"] == "0xabc123"
        assert result["markets"][0]["slug"] == "will-btc-hit-100k"

    @pytest.mark.asyncio
    async def test_search_empty_query(self):
        with patch("src.tools.discovery.GammaClient") as MockGamma:
            instance = AsyncMock()
            instance.search_markets = AsyncMock(return_value=[])
            instance.close = AsyncMock()
            MockGamma.return_value = instance

            result = await search_prediction_markets()

        assert result["count"] == 0
        assert result["markets"] == []

    @pytest.mark.asyncio
    async def test_search_filters_unrelated_high_volume_results_for_specific_query(self):
        unrelated = [
            _mock_gamma_market()
            | {
                "condition_id": "0xfed",
                "question": "Will the Fed decrease interest rates by 50+ bps?",
                "slug": "will-the-fed-decrease-interest-rates-by-50-bps",
            },
            _mock_gamma_market()
            | {
                "condition_id": "0xipl",
                "question": "IPL: Rajasthan Royals vs Royal Challengers Bangalore",
                "slug": "cricipl-raj-roy-2026-04-10",
            },
        ]

        with patch("src.tools.discovery.GammaClient") as MockGamma:
            instance = AsyncMock()
            instance.search_markets = AsyncMock(return_value=unrelated)
            instance.close = AsyncMock()
            MockGamma.return_value = instance

            result = await search_prediction_markets(
                "Atletico Madrid win 2025-26 Champions League",
                limit=10,
            )

        assert result["raw_count"] == 2
        assert result["relevance_filter_applied"] is True
        assert result["count"] == 0
        assert result["markets"] == []
        assert "no relevant market found" in result["note"]

    @pytest.mark.asyncio
    async def test_search_keeps_relevant_specific_result(self):
        relevant = _mock_gamma_market() | {
            "condition_id": "0xatm",
            "question": "Will Atletico Madrid win the 2025-26 Champions League?",
            "slug": "will-atletico-madrid-win-the-2025-26-champions-league",
        }

        with patch("src.tools.discovery.GammaClient") as MockGamma:
            instance = AsyncMock()
            instance.search_markets = AsyncMock(return_value=[relevant])
            instance.close = AsyncMock()
            MockGamma.return_value = instance

            result = await search_prediction_markets(
                "Atletico Madrid win 2025-26 Champions League",
                limit=10,
            )

        assert result["count"] == 1
        assert result["markets"][0]["slug"] == (
            "will-atletico-madrid-win-the-2025-26-champions-league"
        )


    @pytest.mark.asyncio
    async def test_search_prefix_matches_stem_variants(self):
        """'rate' should match 'rates', 'cut' should match 'cutting'."""
        fed_market = _mock_gamma_market() | {
            "condition_id": "0xfed",
            "question": "Will the Fed decrease interest rates by 25 bps?",
            "slug": "will-the-fed-decrease-interest-rates-by-25-bps",
        }

        with patch("src.tools.discovery.GammaClient") as MockGamma:
            instance = AsyncMock()
            instance.search_markets = AsyncMock(return_value=[fed_market])
            instance.close = AsyncMock()
            MockGamma.return_value = instance

            result = await search_prediction_markets("fed rate cut", limit=10)

        # "fed" exact-matches, "rate" prefix-matches "rates" → score=2 >= min_score=2
        assert result["count"] == 1
        assert result["markets"][0]["condition_id"] == "0xfed"


class TestGetMarketDetails:
    @pytest.mark.asyncio
    async def test_get_by_condition_id(self):
        mock_market = _mock_gamma_market()

        with patch("src.tools.discovery.GammaClient") as MockGamma:
            instance = AsyncMock()
            instance.get_market = AsyncMock(return_value=mock_market)
            instance.close = AsyncMock()
            MockGamma.return_value = instance

            result = await get_market_details(condition_id="0xabc123")

        assert result["tool"] == "get_market_details"
        assert result["condition_id"] == "0xabc123"
        assert result["question"] == "Will Bitcoin hit $100K?"
        assert result["outcomes"] == ["Yes", "No"]

    @pytest.mark.asyncio
    async def test_get_by_slug(self):
        mock_market = _mock_gamma_market()

        with patch("src.tools.discovery.GammaClient") as MockGamma:
            instance = AsyncMock()
            instance.get_market_by_slug = AsyncMock(return_value=mock_market)
            instance.close = AsyncMock()
            MockGamma.return_value = instance

            result = await get_market_details(slug="will-btc-hit-100k")

        assert result["tool"] == "get_market_details"
        assert result["slug"] == "will-btc-hit-100k"

    @pytest.mark.asyncio
    async def test_get_prefers_slug_when_both_identifiers_present(self):
        mock_market = _mock_gamma_market()

        with patch("src.tools.discovery.GammaClient") as MockGamma:
            instance = AsyncMock()
            instance.get_market_by_slug = AsyncMock(return_value=mock_market)
            instance.get_market = AsyncMock()
            instance.close = AsyncMock()
            MockGamma.return_value = instance

            result = await get_market_details(
                condition_id="0xwrong",
                slug="will-btc-hit-100k",
            )

        instance.get_market_by_slug.assert_called_once_with("will-btc-hit-100k")
        instance.get_market.assert_not_called()
        assert result["condition_id"] == "0xabc123"

    @pytest.mark.asyncio
    async def test_get_requires_identifier(self):
        with pytest.raises(ValueError, match="Provide either condition_id or slug"):
            await get_market_details()


class TestComparePredictionMarkets:
    @pytest.mark.asyncio
    async def test_compare_two_markets(self):
        mock_market = _mock_gamma_market()
        mock_book = {
            "best_bid": 0.64,
            "best_ask": 0.66,
            "midpoint": 0.65,
            "spread": 0.02,
            "bid_depth_top5": 1000,
            "ask_depth_top5": 800,
        }

        with (
            patch("src.tools.market_state.GammaClient") as MockGamma,
            patch("src.tools.market_state.ClobClient") as MockClob,
        ):
            gamma = AsyncMock()
            gamma.get_market = AsyncMock(return_value=mock_market)
            gamma.close = AsyncMock()
            MockGamma.return_value = gamma

            clob = AsyncMock()
            clob.get_order_book = AsyncMock(return_value=mock_book)
            clob.close = AsyncMock()
            MockClob.return_value = clob

            result = await compare_prediction_markets(["0xabc", "0xdef"])

        assert result["tool"] == "compare_prediction_markets"
        assert result["count"] == 2
        assert result["markets"][0]["outcome_books"][0]["outcome"] == "Yes"
        assert result["markets"][0]["outcome_books"][0]["best_bid"] == 0.64

    @pytest.mark.asyncio
    async def test_compare_requires_at_least_two(self):
        with pytest.raises(ValueError, match="at least 2"):
            await compare_prediction_markets(["0xabc"])

    @pytest.mark.asyncio
    async def test_compare_max_five(self):
        with pytest.raises(ValueError, match="Maximum 5"):
            await compare_prediction_markets(["a", "b", "c", "d", "e", "f"])


class TestGetMarketSnapshot:
    @pytest.mark.asyncio
    async def test_snapshot_returns_books_for_all_outcomes(self):
        mock_market = _mock_gamma_market()
        mock_book = {
            "best_bid": 0.64,
            "best_ask": 0.66,
            "midpoint": 0.65,
            "spread": 0.02,
            "bid_depth_top5": 1000,
            "ask_depth_top5": 800,
        }

        with (
            patch("src.tools.market_state.GammaClient") as MockGamma,
            patch("src.tools.market_state.ClobClient") as MockClob,
        ):
            gamma = AsyncMock()
            gamma.get_market = AsyncMock(return_value=mock_market)
            gamma.close = AsyncMock()
            MockGamma.return_value = gamma

            clob = AsyncMock()
            clob.get_order_book = AsyncMock(return_value=mock_book)
            clob.close = AsyncMock()
            MockClob.return_value = clob

            result = await get_market_snapshot("0xabc123")

        assert result["tool"] == "get_market_snapshot"
        assert result["slug"] == "will-btc-hit-100k"
        assert len(result["outcome_books"]) == 2
        assert result["outcome_books"][0]["outcome"] == "Yes"
        assert result["outcome_books"][1]["outcome"] == "No"
        assert result["outcome_books"][0]["best_ask"] == 0.66


class TestSlugBasedLookups:
    """Test that slug is preferred over condition_id for Gamma lookups."""

    @pytest.mark.asyncio
    async def test_snapshot_prefers_slug(self):
        mock_market = _mock_gamma_market()
        mock_book = {
            "best_bid": 0.64,
            "best_ask": 0.66,
            "midpoint": 0.65,
            "spread": 0.02,
            "bid_depth_top5": 1000,
            "ask_depth_top5": 800,
        }

        with (
            patch("src.tools.market_state.GammaClient") as MockGamma,
            patch("src.tools.market_state.ClobClient") as MockClob,
        ):
            gamma = AsyncMock()
            gamma.get_market_by_slug = AsyncMock(return_value=mock_market)
            gamma.get_market = AsyncMock()
            gamma.close = AsyncMock()
            MockGamma.return_value = gamma

            clob = AsyncMock()
            clob.get_order_book = AsyncMock(return_value=mock_book)
            clob.close = AsyncMock()
            MockClob.return_value = clob

            result = await get_market_snapshot(slug="will-btc-hit-100k")

        gamma.get_market_by_slug.assert_called_once_with("will-btc-hit-100k")
        gamma.get_market.assert_not_called()
        assert result["condition_id"] == "0xabc123"

    @pytest.mark.asyncio
    async def test_snapshot_requires_identifier(self):
        with pytest.raises(ValueError, match="slug or condition_id"):
            await get_market_snapshot()

    @pytest.mark.asyncio
    async def test_compare_accepts_slugs(self):
        mock_market = _mock_gamma_market()
        mock_book = {
            "best_bid": 0.64,
            "best_ask": 0.66,
            "midpoint": 0.65,
            "spread": 0.02,
            "bid_depth_top5": 1000,
            "ask_depth_top5": 800,
        }

        with (
            patch("src.tools.market_state.GammaClient") as MockGamma,
            patch("src.tools.market_state.ClobClient") as MockClob,
        ):
            gamma = AsyncMock()
            gamma.get_market = AsyncMock(return_value=mock_market)
            gamma.close = AsyncMock()
            MockGamma.return_value = gamma

            clob = AsyncMock()
            clob.get_order_book = AsyncMock(return_value=mock_book)
            clob.close = AsyncMock()
            MockClob.return_value = clob

            result = await compare_prediction_markets(["will-btc-hit-100k", "will-eth-hit-10k"])

        assert result["count"] == 2
        # get_market routes slugs through get_market_by_slug internally
        assert gamma.get_market.call_count == 2
        assert result["markets"][0]["slug"] == "will-btc-hit-100k"


class TestBacktestPredictionSetup:
    @pytest.mark.asyncio
    async def test_backtest_uses_history_and_forward_windows(self):
        mock_market = _mock_gamma_market() | {"closed": True, "active": False}
        mock_history = {
            "history": [
                {"timestamp": 1700000000, "price": 0.20},
                {"timestamp": 1700086400, "price": 0.45},
                {"timestamp": 1700172800, "price": 0.60},
                {"timestamp": 1700259200, "price": 1.00},
            ]
        }

        with (
            patch("src.tools.backtest.GammaClient") as MockGamma,
            patch("src.tools.backtest.ClobClient") as MockClob,
        ):
            gamma = AsyncMock()
            gamma.search_markets = AsyncMock(return_value=[mock_market])
            gamma.close = AsyncMock()
            MockGamma.return_value = gamma

            clob = AsyncMock()
            clob.get_price_history = AsyncMock(return_value=mock_history)
            clob.close = AsyncMock()
            MockClob.return_value = clob

            result = await backtest_prediction_setup(
                "Near 0.45 entry zone",
                category="crypto",
                price_threshold_min=0.4,
                price_threshold_max=0.5,
                forward_windows=["1d", "to_resolution"],
            )

        assert result["tool"] == "backtest_prediction_setup"
        assert result["sample_size"] == 1
        assert result["window_stats"]["1d"]["sample_size"] == 1
        assert result["window_stats"]["1d"]["avg_price_change"] == 0.15
        assert result["window_stats"]["to_resolution"]["avg_price_change"] == 0.55
        assert "structured price/liquidity filters only" in result["limitations"][0]


def _mock_gamma_event(
    *,
    title: str = "Fed decision in April?",
    slug: str = "fed-decision-in-april",
    tags: list[str] | None = None,
    markets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if tags is None:
        tags = ["economy", "politics"]
    if markets is None:
        markets = [_mock_gamma_market()]
    return {
        "id": "12345",
        "slug": slug,
        "title": title,
        "description": "Will the Fed change rates?",
        "category": "",
        "start_date": "2026-04-01T00:00:00Z",
        "end_date": "2026-04-30T00:00:00Z",
        "active": True,
        "closed": False,
        "volume": 6_800_000,
        "liquidity": 2_000_000,
        "open_interest": None,
        "tags": tags,
        "markets": markets,
        "comment_count": 42,
    }


class TestExploreTrendingMarkets:
    @pytest.mark.asyncio
    async def test_trending_returns_events_with_markets(self):
        event = _mock_gamma_event()

        with patch("src.tools.discovery.GammaClient") as MockGamma:
            instance = AsyncMock()
            instance.search_events = AsyncMock(return_value=[event])
            instance.close = AsyncMock()
            MockGamma.return_value = instance

            result = await explore_trending_markets(limit=5)

        assert result["tool"] == "explore_trending_markets"
        assert result["count"] == 1
        assert result["events"][0]["title"] == "Fed decision in April?"
        assert result["events"][0]["event_url"] == (
            "https://polymarket.com/event/fed-decision-in-april"
        )
        assert result["events"][0]["market_count"] == 1
        nested_market = result["events"][0]["markets"][0]
        assert nested_market["market_url"] == (
            "https://polymarket.com/event/fed-decision-in-april"
        )

    @pytest.mark.asyncio
    async def test_trending_passes_tag_slug(self):
        event = _mock_gamma_event(
            title="Bitcoin above $100K?",
            slug="bitcoin-above-100k",
            tags=["bitcoin", "crypto"],
        )

        with patch("src.tools.discovery.GammaClient") as MockGamma:
            instance = AsyncMock()
            instance.search_events = AsyncMock(return_value=[event])
            instance.close = AsyncMock()
            MockGamma.return_value = instance

            result = await explore_trending_markets(
                tag_slug="bitcoin", limit=5,
            )

        instance.search_events.assert_called_once_with(
            limit=5,
            active=True,
            order="volume24hr",
            ascending=False,
            tag_slug="bitcoin",
        )
        assert result["count"] == 1
        assert result["tag_slug"] == "bitcoin"

    @pytest.mark.asyncio
    async def test_trending_caps_limit_at_20(self):
        with patch("src.tools.discovery.GammaClient") as MockGamma:
            instance = AsyncMock()
            instance.search_events = AsyncMock(return_value=[])
            instance.close = AsyncMock()
            MockGamma.return_value = instance

            await explore_trending_markets(limit=50)

        instance.search_events.assert_called_once()
        assert instance.search_events.call_args.kwargs["limit"] == 20


class TestSearchFetchLimit:
    """Search fetches a broad set for client-side filtering."""

    @pytest.mark.asyncio
    async def test_search_fetches_more_than_requested_limit(self):
        mock_market = _mock_gamma_market()

        with patch("src.tools.discovery.GammaClient") as MockGamma:
            instance = AsyncMock()
            instance.search_markets = AsyncMock(return_value=[mock_market])
            instance.close = AsyncMock()
            MockGamma.return_value = instance

            await search_prediction_markets("bitcoin", limit=5)

        # Should fetch at least _SEARCH_FETCH_LIMIT (100), not 5
        call_kwargs = instance.search_markets.call_args.kwargs
        assert call_kwargs["limit"] >= 100

    @pytest.mark.asyncio
    async def test_search_trims_results_to_requested_limit(self):
        # Create 10 matching markets
        markets = [
            _mock_gamma_market()
            | {"condition_id": f"0x{i}", "question": f"Bitcoin question {i}"}
            for i in range(10)
        ]

        with patch("src.tools.discovery.GammaClient") as MockGamma:
            instance = AsyncMock()
            instance.search_markets = AsyncMock(return_value=markets)
            instance.close = AsyncMock()
            MockGamma.return_value = instance

            result = await search_prediction_markets("bitcoin", limit=3)

        assert result["count"] == 3
