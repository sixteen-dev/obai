"""Valuation branches beyond the fixtures (ADR 0001 §5 views, design §11.1)."""

from decimal import Decimal

import pytest
from ledger_cases import funded, option, price, stock_lot, trade, usd

from options_backtest.engine.valuation import MarkBasis, stock_value, value_account
from options_backtest.errors import MissingMarkError
from options_backtest.models.market import OptionType, Quote

PUT = option(OptionType.PUT, "100", physical=True)


def test_every_missing_mark_is_reported_in_instrument_order() -> None:
    state = trade(funded(stock=(stock_lot(100, "90.00"),)), (PUT, 1, "1.00"))

    with pytest.raises(MissingMarkError) as caught:
        value_account(state, {}, {}, MarkBasis.MID)
    assert caught.value.instrument_ids == ("XYZ", PUT.contract_id)


@pytest.mark.parametrize("basis", list(MarkBasis))
def test_stock_is_marked_at_its_price_under_every_basis(basis: MarkBasis) -> None:
    state = funded(cash="0", stock=(stock_lot(100, "90.00"),))

    valuation = value_account(state, {}, {"XYZ": price("95.50")}, basis)

    assert valuation.basis is basis
    assert valuation.nlv == usd("9550.00")
    assert valuation.unrealized == usd("550.00")


def test_marks_for_instruments_not_held_are_ignored() -> None:
    quotes = {PUT.contract_id: Quote(price("1.00"), price("1.10"))}

    assert value_account(funded(), quotes, {"XYZ": price("1")}, MarkBasis.MID).nlv == usd(
        "100000.00"
    )


def test_stock_value_is_shares_times_price() -> None:
    assert stock_value("XYZ", 3, price("33.333")) == usd("99.999")
    assert stock_value("XYZ", 0, price("33.333")) == usd("0")


def test_stock_value_rejects_short_stock() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        stock_value("XYZ", -1, price("1"))


def test_stock_value_needs_int_shares() -> None:
    with pytest.raises(TypeError, match="int"):
        stock_value("XYZ", Decimal(1), price("1"))  # type: ignore[arg-type]


def test_value_account_needs_a_mark_basis() -> None:
    with pytest.raises(TypeError, match="MarkBasis"):
        value_account(funded(), {}, {}, "mid")  # type: ignore[arg-type]
