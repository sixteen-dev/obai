"""Physical exercise and assignment branches beyond F02/F03 (ADR 0001 §5, design §12.2).

s = position sign, e = +1 call / -1 put, n contracts: shares change s·e·units·n and cash
-s·e·AEA·n. F02 covers the short put and F03 the short call; here the two long rows, the guards
and the deliverables WP1 cannot book.
"""

import dataclasses
from decimal import Decimal

import pytest
from ledger_cases import (
    EXPIRES_AT_NS,
    PAYABLE,
    RECEIVABLE,
    SETTLES_ON,
    funded,
    option,
    option_cost,
    realized,
    stock_cost,
    stock_lot,
    trade,
    usd,
)

from options_backtest.engine.exercise import book_physical_exercise
from options_backtest.engine.journal import apply_entry
from options_backtest.errors import ErrorCode, LedgerInvariantError, UnsupportedLifecycle
from options_backtest.models.ledger import LedgerEntry, LedgerState, Lot, QuantityKind
from options_backtest.models.market import (
    ContractTerms,
    Deliverable,
    DeliverableComponent,
    ExerciseStyle,
    OptionType,
)

CALL = option(OptionType.CALL, "100", physical=True)
PUT = option(OptionType.PUT, "100", physical=True)
EUROPEAN_CALL = dataclasses.replace(
    CALL, contract_id="XYZ-C100-european", exercise_style=ExerciseStyle.EUROPEAN
)


def exercise(
    state: LedgerState, terms: ContractTerms, contracts: int = 1, at_ns: int | None = None
) -> LedgerEntry:
    return book_physical_exercise(
        state,
        event_id="exercise",
        at_ns=state.last_at_ns + 1 if at_ns is None else at_ns,
        contract_id=terms.contract_id,
        contracts=contracts,
        fees=(),
        settles_on=SETTLES_ON,
    )


def postings(entry: LedgerEntry) -> dict[object, object]:
    return {posting.account: posting.amount for posting in entry.postings}


def test_long_call_exercise_pays_the_aea_for_the_shares() -> None:
    state = trade(funded(), (CALL, 1, "2.00"))  # cost 200, then its payable
    entry = exercise(state, CALL)
    after = apply_entry(state, entry)

    assert postings(entry) == {
        PAYABLE: usd("-10000.00"),
        stock_cost("XYZ"): usd("10000.00"),
        option_cost(CALL.contract_id): usd("-200.00"),
        realized(CALL.contract_id): usd("200.00"),
    }
    assert [(e.instrument_id, e.kind, e.delta) for e in entry.quantity_events] == [
        (CALL.contract_id, QuantityKind.EXERCISE, -1),
        ("XYZ", QuantityKind.DELIVERY, 100),
    ]
    assert after.lots["XYZ"] == (Lot("exercise:XYZ", "XYZ", 100, usd("100.00"), "c1", 2),)
    assert CALL.contract_id not in after.lots
    assert entry.campaign_id == "c1"


def test_long_put_exercise_delivers_held_stock_for_the_aea() -> None:
    state = trade(funded(stock=(stock_lot(100, "90.00"),)), (PUT, 1, "1.00"))
    entry = exercise(state, PUT)
    after = apply_entry(state, entry)

    assert postings(entry) == {
        RECEIVABLE: usd("10000.00"),
        stock_cost("XYZ"): usd("-9000.00"),
        realized("XYZ"): usd("-1000.00"),
        option_cost(PUT.contract_id): usd("-100.00"),
        realized(PUT.contract_id): usd("100.00"),
    }
    assert "XYZ" not in after.lots


def test_partial_exercise_leaves_the_remaining_contracts() -> None:
    state = trade(funded(), (CALL, 3, "2.00"))

    after = apply_entry(state, exercise(state, CALL, 2))

    assert [lot.quantity for lot in after.lots[CALL.contract_id]] == [1]
    assert [lot.quantity for lot in after.lots["XYZ"]] == [200]


def test_an_unregistered_contract_cannot_be_exercised() -> None:
    with pytest.raises(LedgerInvariantError, match="registered"):
        exercise(funded(), CALL)


def test_a_cash_settled_contract_is_not_physically_exercised() -> None:
    cash_call = option(OptionType.CALL, "100")
    state = trade(funded(), (cash_call, 1, "2.00"))

    with pytest.raises(LedgerInvariantError, match="physical"):
        exercise(state, cash_call)


@pytest.mark.parametrize("contracts", [2, 5])
def test_exercise_cannot_exceed_the_held_contracts(contracts: int) -> None:
    state = trade(funded(), (CALL, 1, "2.00"))

    with pytest.raises(LedgerInvariantError, match="held"):
        exercise(state, CALL, contracts)


def test_a_flat_contract_cannot_be_exercised() -> None:
    state = trade(trade(funded(), (CALL, 1, "2.00")), (CALL, -1, "2.00"))

    with pytest.raises(LedgerInvariantError, match="held"):
        exercise(state, CALL)


@pytest.mark.parametrize("contracts", [0, -1])
def test_the_exercised_count_is_positive(contracts: int) -> None:
    state = trade(funded(), (CALL, 1, "2.00"))

    with pytest.raises(ValueError, match="> 0"):
        exercise(state, CALL, contracts)


def _adjusted_call(components: tuple[tuple[str, str], ...], cash: str, aea: str) -> ContractTerms:
    parts = tuple(DeliverableComponent(asset, Decimal(units)) for asset, units in components)
    return option(
        OptionType.CALL,
        "100",
        physical=True,
        deliverable=Deliverable("adjusted", parts, usd(cash)),
        aggregate=aea,
    )


@pytest.mark.parametrize(
    "terms",
    [
        pytest.param(_adjusted_call((("XYZ", "100"), ("ABC", "10")), "0", "10000"), id="multi"),
        pytest.param(_adjusted_call((("XYZ", "100"),), "5.00", "10000"), id="cash-component"),
        pytest.param(_adjusted_call((("XYZ", "100.5"),), "0", "10000"), id="fractional-units"),
        pytest.param(_adjusted_call((("XYZ", "3"),), "0", "10000"), id="inexact-unit-cost"),
        pytest.param(_adjusted_call((("XYZ", "1024"),), "0", "1.00"), id="sub-9dp-unit-cost"),
    ],
)
def test_deliverables_wp1_cannot_book_are_unsupported(terms: ContractTerms) -> None:
    state = trade(funded(), (terms, 1, "2.00"))

    with pytest.raises(UnsupportedLifecycle) as caught:
        exercise(state, terms)
    assert caught.value.code is ErrorCode.UNSUPPORTED_CORPORATE_ACTION


def test_exercised_lots_must_share_one_campaign() -> None:
    state = trade(
        trade(funded(), (CALL, 1, "2.00"), campaign="c1"), (CALL, 1, "2.00"), campaign="c2"
    )

    with pytest.raises(UnsupportedLifecycle) as caught:
        exercise(state, CALL, 2)
    assert caught.value.code is ErrorCode.UNSUPPORTED_ACCOUNT_STATE
    assert exercise(state, CALL, 1).campaign_id == "c1"


def test_the_exercised_count_is_an_int() -> None:
    state = trade(funded(), (CALL, 1, "2.00"))

    with pytest.raises(TypeError, match="int"):
        exercise(state, CALL, True)


def test_a_european_contract_is_not_exercised_before_expiry() -> None:
    state = trade(funded(), (EUROPEAN_CALL, 1, "2.00"))

    with pytest.raises(LedgerInvariantError, match="expir"):
        apply_entry(state, exercise(state, EUROPEAN_CALL, at_ns=EXPIRES_AT_NS - 1))


def test_a_european_contract_is_exercised_at_expiry() -> None:
    state = trade(funded(), (EUROPEAN_CALL, 1, "2.00"))

    after = apply_entry(state, exercise(state, EUROPEAN_CALL, at_ns=EXPIRES_AT_NS))

    assert [lot.quantity for lot in after.lots["XYZ"]] == [100]


def test_acquired_stock_cannot_reuse_a_held_lot_id() -> None:
    # The exercise names its lot "exercise:XYZ", which the deposited lot already holds.
    deposited = stock_lot(100, "90.00", lot_id="exercise:XYZ")
    state = trade(funded(stock=(deposited,)), (CALL, 1, "2.00"))

    with pytest.raises(LedgerInvariantError, match="lot id"):
        apply_entry(state, exercise(state, CALL))
