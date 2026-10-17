"""Data-quality flags on live option quotes.

The quote vocabulary and its precedence mirror the options-backtest engine's
``QuoteStatus`` (negative, zero ask, crossed, locked, no bid), and the stale
bound is inclusive like its ``max_age_ns`` (120 s passes, 121 s fails).
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.config import Settings
from src.data_quality import QuoteStatus, annotate_contracts, quote_flags, quote_status
from src.response_filters import filter_option_quote, filter_option_trade
from src.tools.options import get_latest_option_quote, get_option_chain_snapshot

NOW_NS = 1_792_100_000_000_000_000
SECOND_NS = 1_000_000_000
MAX_AGE_S = 120


def _contract(**overrides: Any) -> dict[str, Any]:
    """A filtered chain contract that passes every check."""
    contract: dict[str, Any] = {
        "ticker": "O:XSP261026P00767000",
        "strike_price": 767,
        "contract_type": "put",
        "last_quote": {
            "bid": 2.94,
            "ask": 3.05,
            "bid_size": 10,
            "ask_size": 12,
            "last_updated": NOW_NS - 5 * SECOND_NS,
        },
        "greeks": {"delta": -0.2955, "gamma": 0.03, "theta": -0.4, "vega": 0.3},
        "implied_volatility": 0.1176,
        "underlying_price": 774.41,
        "underlying_timeframe": "REAL-TIME",
    }
    contract.update(overrides)
    return contract


def _quote(**overrides: Any) -> dict[str, Any]:
    quote = dict(_contract()["last_quote"])
    quote.update(overrides)
    return quote


class TestQuoteStatus:
    """First match wins, in the backtest engine's order."""

    @pytest.mark.parametrize(
        ("bid", "ask", "expected"),
        [
            (2.94, 3.05, QuoteStatus.VALID),
            (-0.01, 3.05, QuoteStatus.NEGATIVE),
            (0.0, 0.0, QuoteStatus.ZERO_ASK),
            (3.10, 3.05, QuoteStatus.CROSSED),
            (3.05, 3.05, QuoteStatus.LOCKED),
            (0.0, 0.07, QuoteStatus.NO_BID),
            (None, 3.05, QuoteStatus.MISSING),
            (2.94, None, QuoteStatus.MISSING),
        ],
    )
    def test_classifies_bid_and_ask(
        self, bid: float | None, ask: float | None, expected: QuoteStatus
    ) -> None:
        assert quote_status(bid, ask, bid_size=1, ask_size=1) is expected

    def test_a_negative_size_is_negative(self) -> None:
        assert quote_status(2.94, 3.05, bid_size=-1, ask_size=1) is QuoteStatus.NEGATIVE


class TestQuoteFlags:
    def test_a_fresh_valid_quote_has_no_flags(self) -> None:
        assert quote_flags(_quote(), now_ns=NOW_NS, max_quote_age_s=MAX_AGE_S) == []

    def test_status_other_than_valid_is_flagged(self) -> None:
        flags = quote_flags(_quote(bid=3.10), now_ns=NOW_NS, max_quote_age_s=MAX_AGE_S)
        assert flags == ["quote_crossed"]

    def test_stale_bound_is_inclusive(self) -> None:
        at_bound = _quote(last_updated=NOW_NS - MAX_AGE_S * SECOND_NS)
        past_bound = _quote(last_updated=NOW_NS - (MAX_AGE_S + 1) * SECOND_NS)

        assert quote_flags(at_bound, now_ns=NOW_NS, max_quote_age_s=MAX_AGE_S) == []
        assert quote_flags(past_bound, now_ns=NOW_NS, max_quote_age_s=MAX_AGE_S) == ["quote_stale"]

    def test_missing_timestamp_is_flagged_not_assumed_fresh(self) -> None:
        flags = quote_flags(_quote(last_updated=None), now_ns=NOW_NS, max_quote_age_s=MAX_AGE_S)
        assert flags == ["quote_time_unknown"]

    def test_rejects_a_non_positive_age_bound(self) -> None:
        with pytest.raises(ValueError, match="max_quote_age_s"):
            quote_flags(_quote(), now_ns=NOW_NS, max_quote_age_s=0)


class TestAnnotateContracts:
    def test_clean_contract_gains_no_field(self) -> None:
        contracts, summary = annotate_contracts(
            [_contract()], now_ns=NOW_NS, max_quote_age_s=MAX_AGE_S
        )

        assert "quality_flags" not in contracts[0]
        assert summary["contracts_checked"] == 1
        assert summary["contracts_flagged"] == 0
        assert summary["flag_counts"] == {}
        assert summary["max_quote_age_seconds"] == MAX_AGE_S

    def test_delayed_and_missing_underlying_are_flagged(self) -> None:
        delayed = _contract(underlying_timeframe="DELAYED")
        missing = _contract(underlying_price=None)

        contracts, _ = annotate_contracts(
            [delayed, missing], now_ns=NOW_NS, max_quote_age_s=MAX_AGE_S
        )

        assert contracts[0]["quality_flags"] == ["underlying_delayed"]
        assert "underlying_missing" in contracts[1]["quality_flags"]

    def test_empty_greeks_are_flagged(self) -> None:
        contracts, _ = annotate_contracts(
            [_contract(greeks=None, implied_volatility=None)],
            now_ns=NOW_NS,
            max_quote_age_s=MAX_AGE_S,
        )
        assert contracts[0]["quality_flags"] == ["greeks_missing"]

    def test_mid_below_intrinsic_is_flagged(self) -> None:
        # Put 800 with spot 774.41 is worth at least 25.59; a 20.00 mid is below that,
        # which is the case Massive cannot solve Greeks for.
        deep_itm = _contract(
            strike_price=800,
            last_quote=_quote(bid=19.9, ask=20.1),
            greeks=None,
            implied_volatility=None,
        )

        contracts, summary = annotate_contracts(
            [deep_itm], now_ns=NOW_NS, max_quote_age_s=MAX_AGE_S
        )

        assert contracts[0]["quality_flags"] == ["below_intrinsic", "greeks_missing"]
        assert summary["flag_counts"] == {"below_intrinsic": 1, "greeks_missing": 1}

    def test_input_contracts_are_not_mutated(self) -> None:
        original = _contract(underlying_timeframe="DELAYED")

        annotate_contracts([original], now_ns=NOW_NS, max_quote_age_s=MAX_AGE_S)

        assert "quality_flags" not in original


class TestQuoteFilterKeepsZeroBid:
    def test_zero_bid_stays_zero(self) -> None:
        quote = filter_option_quote({"bid_price": 0, "ask_price": 0.07, "sip_timestamp": 1})
        assert quote["bid_price"] == 0
        assert quote["ask_price"] == 0.07


def _response(payload: dict[str, Any]) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    return response


class TestToolsAttachDataQuality:
    @pytest.mark.asyncio
    async def test_chain_snapshot_carries_a_data_quality_block(
        self, mock_settings: Settings
    ) -> None:
        raw = {
            "results": [
                {
                    "details": {
                        "ticker": "O:XSP261026P00767000",
                        "strike_price": 767,
                        "contract_type": "put",
                    },
                    "last_quote": {
                        "bid": 3.10,
                        "ask": 3.05,
                        "bid_size": 1,
                        "ask_size": 1,
                        "last_updated": NOW_NS,
                    },
                    "greeks": {"delta": -0.3},
                    "implied_volatility": 0.12,
                    "underlying_asset": {"value": 774.41, "timeframe": "DELAYED"},
                }
            ]
        }
        with (
            patch("src.tools.options.get_settings", return_value=mock_settings),
            patch("src.tools.options.time.time_ns", return_value=NOW_NS),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = _response(raw)
            result = await get_option_chain_snapshot("XSP")

        assert result["contracts"][0]["quality_flags"] == ["quote_crossed", "underlying_delayed"]
        assert result["data_quality"]["contracts_flagged"] == 1

    @pytest.mark.asyncio
    async def test_latest_quote_carries_its_flags(self, mock_settings: Settings) -> None:
        raw = {
            "results": {
                "bid_price": 0,
                "ask_price": 0.07,
                "bid_size": 0,
                "ask_size": 5,
                "sip_timestamp": NOW_NS - 600 * SECOND_NS,
            }
        }
        with (
            patch("src.tools.options.get_settings", return_value=mock_settings),
            patch("src.tools.options.time.time_ns", return_value=NOW_NS),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = _response(raw)
            result = await get_latest_option_quote("O:XSP261026P00460000")

        assert result["quality_flags"] == ["quote_no_bid", "quote_stale"]
        assert result["max_quote_age_seconds"] == mock_settings.max_quote_age_seconds


class TestLastTradeAndQuoteShapes:
    """/v2/last/* answer with one-letter keys (captured live 2026-10-15)."""

    def test_last_nbbo_keys_are_read(self) -> None:
        raw = {
            "P": 3.05,
            "S": 48,
            "T": "O:XSP261026P00767000",
            "X": 302,
            "p": 2.94,
            "q": 1261329981,
            "s": 48,
            "t": 1792097999998040171,
            "x": 302,
        }

        quote = filter_option_quote(raw)

        assert quote == {
            "bid_price": 2.94,
            "bid_size": 48,
            "ask_price": 3.05,
            "ask_size": 48,
            "bid_exchange": 302,
            "ask_exchange": 302,
            "sip_timestamp": 1792097999998040171,
        }

    def test_last_trade_keys_are_read(self) -> None:
        raw = {
            "T": "O:XSP261026P00767000",
            "c": [232],
            "i": "",
            "p": 4.26,
            "q": 1144138614,
            "s": 10,
            "t": 1792008492361864838,
            "x": 302,
        }

        trade = filter_option_trade(raw)

        assert trade == {
            "price": 4.26,
            "size": 10,
            "exchange": 302,
            "sip_timestamp": 1792008492361864838,
            "conditions": [232],
        }

    def test_long_names_still_work(self) -> None:
        quote = filter_option_quote({"bid_price": 0, "ask_price": 0.07, "sip_timestamp": 1})
        assert (quote["bid_price"], quote["ask_price"], quote["sip_timestamp"]) == (0, 0.07, 1)
