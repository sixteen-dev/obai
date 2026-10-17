"""Ledger records and canonical postings (ADR 0001 §5 ``models/ledger.py``, design §11.1)."""

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from options_backtest.models.ledger import (
    AccountKey,
    AccountKind,
    EntryKind,
    FeeEvent,
    FeeLine,
    LedgerEntry,
    LedgerState,
    LegFill,
    Lot,
    LotRelief,
    Posting,
    QuantityEvent,
    QuantityKind,
    due_cash,
    fee_amounts,
    leg_amounts,
    merge_postings,
)
from options_backtest.models.market import (
    ContractTerms,
    Deliverable,
    DeliverableComponent,
    ExerciseStyle,
    OptionType,
    SettlementType,
)
from options_backtest.money import Price, Usd

SETTLES_ON = date(2026, 9, 22)
CASH = AccountKey(AccountKind.CASH, "")
CAPITAL = AccountKey(AccountKind.CAPITAL, "")


def usd(text: str) -> Usd:
    return Usd(Decimal(text))


def terms() -> ContractTerms:
    deliverable = Deliverable("SPX-x100", (DeliverableComponent("SPX", Decimal(100)),), usd("0"))
    return ContractTerms(
        contract_id="SPXW-P100",
        option_type=OptionType.PUT,
        strike=Price(Decimal(100)),
        exercise_style=ExerciseStyle.EUROPEAN,
        settlement_type=SettlementType.CASH,
        premium_multiplier=Decimal(100),
        deliverable=deliverable,
        aggregate_exercise_amount=usd("10000"),
        expires_at_ns=1_000,
    )


def lot(**overrides: Any) -> Lot:
    fields: dict[str, Any] = {
        "lot_id": "l1",
        "instrument_id": "SPXW-P100",
        "quantity": -2,
        "unit_cost": usd("200.00"),
        "campaign_id": "c1",
        "opened_at_ns": 5,
    }
    fields.update(overrides)
    return Lot(**fields)


def entry(**overrides: Any) -> LedgerEntry:
    fields: dict[str, Any] = {
        "event_id": "e1",
        "sequence": 1,
        "kind": EntryKind.DEPOSIT,
        "at_ns": 0,
        "campaign_id": None,
        "postings": (Posting(CASH, usd("1")), Posting(CAPITAL, usd("-1"))),
        "quantity_events": (),
        "contracts": (),
        "fee_lines": (),
        "input_refs": (),
    }
    fields.update(overrides)
    return LedgerEntry(**fields)


# --- account keys ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "ref"),
    [
        (AccountKind.CASH, ""),
        (AccountKind.CAPITAL, ""),
        (AccountKind.RECEIVABLE, "2026-09-22"),
        (AccountKind.PAYABLE, "2026-09-22"),
        (AccountKind.OPTION_COST, "SPXW-P100"),
        (AccountKind.STOCK_COST, "XYZ"),
        (AccountKind.REALIZED_PNL, "XYZ"),
        (AccountKind.FEES, "flat:trade"),
    ],
)
def test_each_kind_accepts_its_reference(kind: AccountKind, ref: str) -> None:
    assert AccountKey(kind, ref).ref == ref


@pytest.mark.parametrize(
    ("kind", "ref"),
    [
        (AccountKind.CASH, "x"),
        (AccountKind.CAPITAL, "x"),
        (AccountKind.RECEIVABLE, "20260922"),
        (AccountKind.PAYABLE, "2026-09-22T00:00:00"),
        (AccountKind.PAYABLE, "tomorrow"),
        (AccountKind.OPTION_COST, ""),
        (AccountKind.FEES, ""),
    ],
)
def test_each_kind_rejects_a_foreign_reference(kind: AccountKind, ref: str) -> None:
    with pytest.raises(ValueError, match="ref"):
        AccountKey(kind, ref)


def test_account_keys_sort_by_kind_then_reference() -> None:
    keys = [
        AccountKey(AccountKind.PAYABLE, "2026-09-23"),
        CASH,
        AccountKey(AccountKind.PAYABLE, "2026-09-22"),
    ]

    assert sorted(keys) == [CASH, keys[2], keys[0]]


def test_account_key_rejects_a_non_enum_kind() -> None:
    with pytest.raises(TypeError, match="AccountKind"):
        AccountKey("cash", "")  # type: ignore[arg-type]


# --- lots, reliefs and quantity events -------------------------------------------------------


def test_signed_costs_carry_the_quantity_sign() -> None:
    assert lot().signed_cost == usd("-400.00")
    assert lot(quantity=3).signed_cost == usd("600.00")
    assert LotRelief("l1", -2, usd("400.00")).signed_cost == usd("-400.00")
    assert LotRelief("l1", 2, usd("400.00")).signed_cost == usd("400.00")


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        pytest.param({"quantity": 0}, ValueError, id="zero-quantity"),
        pytest.param({"quantity": True}, TypeError, id="bool-quantity"),
        pytest.param({"unit_cost": usd("-1")}, ValueError, id="negative-cost"),
        pytest.param({"lot_id": ""}, ValueError, id="empty-id"),
        pytest.param({"campaign_id": ""}, ValueError, id="empty-campaign"),
        pytest.param({"opened_at_ns": 1.0}, TypeError, id="float-time"),
        pytest.param({"unit_cost": Decimal(1)}, TypeError, id="decimal-cost"),
    ],
)
def test_lot_guards(overrides: dict[str, Any], error: type[Exception]) -> None:
    with pytest.raises(error):
        lot(**overrides)


@pytest.mark.parametrize(
    ("args", "error"),
    [
        pytest.param(("l1", 0, usd("1")), ValueError, id="zero"),
        pytest.param(("l1", 1, usd("-1")), ValueError, id="negative-cost"),
    ],
)
def test_lot_relief_guards(args: tuple[Any, ...], error: type[Exception]) -> None:
    with pytest.raises(error):
        LotRelief(*args)


@pytest.mark.parametrize(
    ("delta", "opened", "match"),
    [
        pytest.param(0, None, "nonzero", id="zero-delta"),
        pytest.param(2, lot(), "sign", id="opened-sign"),
        pytest.param(-2, lot(instrument_id="other"), "instrument", id="opened-instrument"),
    ],
)
def test_quantity_event_guards(delta: int, opened: Lot | None, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        QuantityEvent("SPXW-P100", QuantityKind.OPEN, delta, opened, ())


def test_quantity_event_reliefs_must_be_a_tuple_of_reliefs() -> None:
    with pytest.raises(TypeError, match="reliefs"):
        QuantityEvent("SPXW-P100", QuantityKind.CLOSE, -1, None, [LotRelief("l", 1, usd("1"))])  # type: ignore[arg-type]


# --- fee lines and leg fills -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "error"),
    [
        pytest.param(("", FeeEvent.TRADE, 1, usd("1"), usd("1")), ValueError, id="empty-id"),
        pytest.param(("f", FeeEvent.TRADE, 0, usd("1"), usd("0")), ValueError, id="no-contracts"),
        pytest.param(("f", FeeEvent.TRADE, 1, usd("-1"), usd("0")), ValueError, id="rate"),
        pytest.param(("f", FeeEvent.TRADE, 1, usd("1"), usd("-1")), ValueError, id="amount"),
        pytest.param(("f", "trade", 1, usd("1"), usd("1")), TypeError, id="event-type"),
    ],
)
def test_fee_line_guards(args: tuple[Any, ...], error: type[Exception]) -> None:
    with pytest.raises(error):
        FeeLine(*args)


@pytest.mark.parametrize(
    ("contracts", "error"),
    [pytest.param(0, ValueError, id="zero"), pytest.param(False, TypeError, id="bool")],
)
def test_leg_fill_needs_a_nonzero_int_contract_count(
    contracts: int, error: type[Exception]
) -> None:
    with pytest.raises(error):
        LegFill(terms(), contracts, Price(Decimal("1")))


# --- entries and state -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        pytest.param({"event_id": ""}, ValueError, id="empty-event-id"),
        pytest.param({"sequence": 0}, ValueError, id="sequence-zero"),
        pytest.param({"at_ns": -1}, ValueError, id="negative-time"),
        pytest.param({"kind": "deposit"}, TypeError, id="kind-type"),
        pytest.param({"postings": [Posting(CASH, usd("1"))]}, TypeError, id="postings-list"),
        pytest.param({"contracts": ("SPXW-P100",)}, TypeError, id="contracts-items"),
        pytest.param({"input_refs": ("",)}, ValueError, id="empty-input-ref"),
    ],
)
def test_ledger_entry_guards(overrides: dict[str, Any], error: type[Exception]) -> None:
    with pytest.raises(error):
        entry(**overrides)


def test_empty_state_has_nothing() -> None:
    state = LedgerState.empty()

    assert (state.entry_count, state.last_at_ns) == (0, 0)
    assert (dict(state.balances), dict(state.lots), dict(state.contracts)) == ({}, {}, {})
    assert state.retired == frozenset()


def test_state_mappings_are_read_only_copies() -> None:
    balances = {CASH: usd("1"), CAPITAL: usd("-1")}
    state = LedgerState(1, 0, balances, {}, {}, frozenset())
    balances[CASH] = usd("2")

    assert state.balances[CASH] == usd("1")
    with pytest.raises(TypeError):
        state.balances[CASH] = usd("3")  # type: ignore[index]


def test_state_rejects_a_zero_balance() -> None:
    with pytest.raises(ValueError, match="zero balance"):
        LedgerState(1, 0, {CASH: usd("0")}, {}, {}, frozenset())


def test_state_rejects_an_empty_lot_list() -> None:
    with pytest.raises(ValueError, match="empty lot list"):
        LedgerState(1, 0, {}, {"SPXW-P100": ()}, {}, frozenset())


# --- canonical postings ----------------------------------------------------------------------


def test_merge_postings_sums_per_account_drops_zeros_and_sorts() -> None:
    payable = AccountKey(AccountKind.PAYABLE, "2026-09-22")
    fees = AccountKey(AccountKind.FEES, "flat:trade")

    merged = merge_postings(
        [
            (fees, usd("1")),
            (payable, usd("-1")),
            (CASH, usd("5")),
            (fees, usd("1")),
            (payable, usd("-1")),
            (CAPITAL, usd("0")),
            (CASH, usd("-5")),
        ]
    )

    assert merged == (Posting(fees, usd("2")), Posting(payable, usd("-2")))


@pytest.mark.parametrize(
    ("amount", "kind"),
    [
        pytest.param("90.00", AccountKind.RECEIVABLE, id="credit"),
        pytest.param("-80.00", AccountKind.PAYABLE, id="debit"),
        pytest.param("0", AccountKind.PAYABLE, id="zero"),
    ],
)
def test_due_cash_is_a_receivable_when_positive_else_a_payable(
    amount: str, kind: AccountKind
) -> None:
    assert due_cash(usd(amount), SETTLES_ON) == (AccountKey(kind, "2026-09-22"), usd(amount))


def test_fee_amounts_debit_fees_and_credit_the_dated_payable() -> None:
    line = FeeLine("flat:trade", FeeEvent.TRADE, 2, usd("1.00"), usd("2.00"))
    payable = AccountKey(AccountKind.PAYABLE, "2026-09-22")

    assert fee_amounts((line,), SETTLES_ON) == [
        (AccountKey(AccountKind.FEES, "flat:trade"), usd("2.00")),
        (payable, usd("-2.00")),
    ]


@pytest.mark.parametrize(
    "book",
    [
        pytest.param(lambda when: due_cash(usd("1.00"), when), id="due_cash"),
        pytest.param(lambda when: fee_amounts((), when), id="fee_amounts"),
    ],
)
def test_a_settlement_date_is_a_date_not_a_datetime(book: Callable[[date], object]) -> None:
    # datetime is a date subclass whose isoformat is no AccountKey date: fail at the call.
    with pytest.raises(TypeError, match="settles_on must be a date"):
        book(datetime(2026, 9, 22, 16, 0))


# --- per-leg amounts -------------------------------------------------------------------------


def test_cost_change_is_the_opened_cost_less_the_relieved_cost() -> None:
    reliefs = (LotRelief("a", 1, usd("100.00")),)
    reversal = QuantityEvent("SPXW-P100", QuantityKind.CLOSE, -3, lot(), reliefs)

    assert reversal.cost_change == usd("-500.00")
    assert QuantityEvent("SPXW-P100", QuantityKind.OPEN, -2, lot(), ()).cost_change == usd(
        "-400.00"
    )


def test_leg_amounts_realize_the_balancing_amount() -> None:
    # Close a long bought for 100 by selling it for 250: cost -100, realized -150 (a gain).
    close = QuantityEvent(
        "SPXW-P100", QuantityKind.CLOSE, -1, None, (LotRelief("a", 1, usd("100")),)
    )

    assert leg_amounts(close, AccountKind.OPTION_COST, usd("250")) == [
        (AccountKey(AccountKind.OPTION_COST, "SPXW-P100"), usd("-100")),
        (AccountKey(AccountKind.REALIZED_PNL, "SPXW-P100"), usd("-150")),
    ]


def test_leg_amounts_need_a_cost_account_kind() -> None:
    event = QuantityEvent("SPXW-P100", QuantityKind.OPEN, -2, lot(), ())

    with pytest.raises(ValueError, match="cost account"):
        leg_amounts(event, AccountKind.CASH, usd("0"))


def test_state_rejects_a_negative_entry_count() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        LedgerState(-1, 0, {}, {}, {}, frozenset())
