"""FIFO relief and the quantity journal (ADR 0001 §5 ``engine/positions.py``, design §11.1).

Lots within one instrument are relieved oldest first, deterministically; a relief carries the
lot's sign and ``|quantity| x unit_cost``; the unrelieved remainder opens a new lot.
"""

import dataclasses
from decimal import Decimal

import pytest

from options_backtest.engine.positions import (
    apply_quantity_events,
    fifo_relief,
    held_contract,
    open_remainder,
)
from options_backtest.errors import LedgerInvariantError
from options_backtest.models.ledger import LedgerState, Lot, LotRelief, QuantityEvent, QuantityKind
from options_backtest.models.market import (
    ContractTerms,
    Deliverable,
    DeliverableComponent,
    ExerciseStyle,
    OptionType,
    SettlementType,
)
from options_backtest.money import Price, Usd

INSTRUMENT = "SPXW-P100"


def usd(text: str) -> Usd:
    return Usd(Decimal(text))


def lot(lot_id: str, quantity: int, unit_cost: str = "2.00", opened_at_ns: int = 1) -> Lot:
    return Lot(
        lot_id=lot_id,
        instrument_id=INSTRUMENT,
        quantity=quantity,
        unit_cost=usd(unit_cost),
        campaign_id="c1",
        opened_at_ns=opened_at_ns,
    )


LONG_LOTS = (lot("a", 2, "1.00"), lot("b", 3, "2.00"), lot("c", 1, "3.00"))


# --- fifo_relief -----------------------------------------------------------------------------


@pytest.mark.parametrize("delta", [1, 5])
def test_a_delta_with_the_held_sign_relieves_nothing(delta: int) -> None:
    assert fifo_relief(LONG_LOTS, delta) == ()


def test_nothing_held_relieves_nothing() -> None:
    assert fifo_relief((), -3) == ()


def test_relief_takes_the_oldest_lots_first_and_splits_only_the_last() -> None:
    assert fifo_relief(LONG_LOTS, -4) == (
        LotRelief("a", 2, usd("2.00")),
        LotRelief("b", 2, usd("4.00")),
    )


def test_relieving_more_than_held_relieves_every_lot_whole() -> None:
    assert fifo_relief(LONG_LOTS, -9) == (
        LotRelief("a", 2, usd("2.00")),
        LotRelief("b", 3, usd("6.00")),
        LotRelief("c", 1, usd("3.00")),
    )


def test_a_short_lot_relief_carries_the_lots_negative_sign_and_a_positive_cost() -> None:
    short = (lot("s", -3, "2.50"),)

    assert fifo_relief(short, 2) == (LotRelief("s", -2, usd("5.00")),)


def test_zero_delta_is_rejected() -> None:
    with pytest.raises(ValueError, match="nonzero"):
        fifo_relief(LONG_LOTS, 0)


def test_a_non_int_delta_is_rejected() -> None:
    with pytest.raises(TypeError, match="int"):
        fifo_relief(LONG_LOTS, True)


def test_lots_of_both_signs_are_an_invalid_state() -> None:
    with pytest.raises(LedgerInvariantError, match="both signs"):
        fifo_relief((lot("a", 1), lot("b", -1)), -1)


# --- open_remainder --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("delta", "reliefs", "expected"),
    [
        pytest.param(-4, (LotRelief("a", 2, usd("2")), LotRelief("b", 2, usd("4"))), 0, id="close"),
        pytest.param(-9, (LotRelief("a", 6, usd("6")),), -3, id="reverse"),
        pytest.param(2, (), 2, id="open"),
        pytest.param(5, (LotRelief("s", -3, usd("6")),), 2, id="reverse-short"),
    ],
)
def test_open_remainder_is_the_unrelieved_part_of_delta(
    delta: int, reliefs: tuple[LotRelief, ...], expected: int
) -> None:
    assert open_remainder(delta, reliefs) == expected


# --- apply_quantity_events -------------------------------------------------------------------


def event(
    delta: int, *, reliefs: tuple[LotRelief, ...] = (), opened: Lot | None = None
) -> QuantityEvent:
    return QuantityEvent(INSTRUMENT, QuantityKind.CLOSE, delta, opened, reliefs)


def test_an_opening_event_appends_its_lot() -> None:
    new = lot("d", 2, opened_at_ns=9)

    after = apply_quantity_events({INSTRUMENT: LONG_LOTS}, (event(2, opened=new),))

    assert after[INSTRUMENT] == (*LONG_LOTS, new)


def test_a_partial_close_keeps_the_split_lots_identity_cost_and_open_time() -> None:
    lots = {INSTRUMENT: LONG_LOTS}

    after = apply_quantity_events(lots, (event(-4, reliefs=fifo_relief(LONG_LOTS, -4)),))

    assert after[INSTRUMENT] == (dataclasses.replace(LONG_LOTS[1], quantity=1), LONG_LOTS[2])
    assert lots == {INSTRUMENT: LONG_LOTS}, "the input mapping is not mutated"


def test_closing_everything_removes_the_instrument() -> None:
    after = apply_quantity_events(
        {INSTRUMENT: LONG_LOTS, "other": (lot("x", 1),)},
        (event(-6, reliefs=fifo_relief(LONG_LOTS, -6)),),
    )

    assert INSTRUMENT not in after
    assert "other" in after


def test_a_reversal_relieves_every_lot_and_opens_the_remainder() -> None:
    short = Lot("r", INSTRUMENT, -2, usd("4.00"), "c1", 9)

    after = apply_quantity_events(
        {INSTRUMENT: LONG_LOTS}, (event(-8, reliefs=fifo_relief(LONG_LOTS, -8), opened=short),)
    )

    assert after[INSTRUMENT] == (short,)


def test_events_apply_in_order() -> None:
    first = lot("d", 1, opened_at_ns=9)
    second = lot("e", 2, opened_at_ns=10)

    after = apply_quantity_events({}, (event(1, opened=first), event(2, opened=second)))

    assert after[INSTRUMENT] == (first, second)


@pytest.mark.parametrize(
    "reliefs",
    [
        pytest.param((), id="missing"),
        pytest.param((LotRelief("b", 2, usd("4.00")), LotRelief("a", 2, usd("2.00"))), id="lifo"),
        pytest.param((LotRelief("a", 2, usd("2.01")), LotRelief("b", 2, usd("4.00"))), id="cost"),
    ],
)
def test_reliefs_other_than_the_recomputed_fifo_are_rejected(
    reliefs: tuple[LotRelief, ...],
) -> None:
    with pytest.raises(LedgerInvariantError, match="FIFO"):
        apply_quantity_events({INSTRUMENT: LONG_LOTS}, (event(-4, reliefs=reliefs),))


@pytest.mark.parametrize(
    ("delta", "opened"),
    [
        pytest.param(-6, lot("x", -1), id="flat-close-with-a-lot"),
        pytest.param(-8, None, id="reversal-without-a-lot"),
        pytest.param(-8, lot("x", -3), id="wrong-remainder"),
    ],
)
def test_the_opened_lot_must_be_exactly_the_unrelieved_remainder(
    delta: int, opened: Lot | None
) -> None:
    reliefs = fifo_relief(LONG_LOTS, delta)

    with pytest.raises(LedgerInvariantError, match="opened lot"):
        apply_quantity_events(
            {INSTRUMENT: LONG_LOTS}, (event(delta, reliefs=reliefs, opened=opened),)
        )


# --- held_contract ---------------------------------------------------------------------------


def _terms() -> ContractTerms:
    deliverable = Deliverable("SPX-x100", (DeliverableComponent("SPX", Decimal(100)),), usd("0"))
    return ContractTerms(
        contract_id=INSTRUMENT,
        option_type=OptionType.PUT,
        strike=Price(Decimal(100)),
        exercise_style=ExerciseStyle.EUROPEAN,
        settlement_type=SettlementType.CASH,
        premium_multiplier=Decimal(100),
        deliverable=deliverable,
        aggregate_exercise_amount=usd("10000"),
        expires_at_ns=1_000,
    )


def _state(*, lots: tuple[Lot, ...], retired: bool = False) -> LedgerState:
    held = {INSTRUMENT: lots} if lots else {}
    return LedgerState(
        1, 0, {}, held, {INSTRUMENT: _terms()}, frozenset({INSTRUMENT} if retired else ())
    )


def test_held_contract_returns_the_terms_and_lots() -> None:
    assert held_contract(_state(lots=LONG_LOTS), INSTRUMENT) == (_terms(), LONG_LOTS)


@pytest.mark.parametrize(
    ("state", "contract_id", "match"),
    [
        pytest.param(_state(lots=LONG_LOTS), "SPXW-P95", "not registered", id="unregistered"),
        pytest.param(_state(lots=(), retired=True), INSTRUMENT, "retired", id="retired"),
        pytest.param(_state(lots=()), INSTRUMENT, "not held", id="flat"),
    ],
)
def test_held_contract_requires_a_registered_live_held_contract(
    state: LedgerState, contract_id: str, match: str
) -> None:
    with pytest.raises(LedgerInvariantError, match=match):
        held_contract(state, contract_id)
