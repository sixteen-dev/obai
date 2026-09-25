"""C16: fixture F05, a synthetic reverse split that changes the deliverable but not the multiplier.

Design §18.3 (F05), §12.5 (transform contract identity and deliverable exactly as mapped,
preserving basis and campaign); ADR 0001 §3 (multiplier only in premiums and marks) and §5
(``book_deliverable_adjustment``: lots move one-for-one, OPTION_COST moves with them, the old
contract is retired; any other change raises ``UNSUPPORTED_CORPORATE_ACTION``).

Hand check. Before: 100 shares at 50 = 5,000 against AEA 6,000, put intrinsic 1,000. After:
50 shares at 100 = 5,000 against the same AEA, intrinsic 1,000. The premium multiplier stays
100, so one long contract marked at 10.00 is worth 1 x 100 x 10 = 1,000 before and after.
"""

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from builders import (
    STOCK_ASSET,
    ZERO,
    account,
    balance,
    cash_like,
    fixture,
    moment,
    posting_map,
    price,
    settle_day,
    stock_option,
    usd,
)

from options_backtest.engine.adjustments import book_deliverable_adjustment
from options_backtest.engine.journal import apply_entry
from options_backtest.engine.trades import book_deposit, book_option_trade
from options_backtest.engine.valuation import MarkBasis, reconcile, value_account
from options_backtest.errors import ErrorCode, UnsupportedLifecycle
from options_backtest.models.ledger import (
    AccountKind,
    LedgerEntry,
    LedgerState,
    LegFill,
    QuantityKind,
)
from options_backtest.models.market import (
    ContractTerms,
    ExerciseStyle,
    OptionType,
    Quote,
    SettlementType,
)

CAMPAIGN = "F05"
SETUP_CASH = "1000.00"  # F05 states no cash; any deposit funds the long put's premium
ACTION_REF = "f05-synthetic-reverse-split"


@dataclass(frozen=True)
class F05Run:
    """The F05 position before and after its deliverable adjustment."""

    data: Mapping[str, Any]
    before_terms: ContractTerms
    after_terms: ContractTerms
    held: LedgerState
    adjustment: LedgerEntry
    adjusted: LedgerState

    @property
    def expected(self) -> Mapping[str, Any]:
        """Return the fixture's expected values."""
        return dict(self.data["expected"])

    def phase(self, name: str) -> Mapping[str, Any]:
        """Return the fixture's ``before`` or ``after`` terms and marks."""
        return dict(self.data[name])

    def quotes(self, name: str, terms: ContractTerms) -> dict[str, Quote]:
        """Return a quote whose mid is the phase's option premium mark."""
        mark = price(self.phase(name)["option_premium_mark"])
        return {terms.contract_id: Quote(mark, mark)}


def _terms(data: Mapping[str, Any], name: str) -> ContractTerms:
    phase = data[name]
    return stock_option(
        OptionType(data["option_type"]),
        phase["listed_strike"],
        shares=phase["deliverable_shares"],
        aggregate=phase["aggregate_exercise_amount_usd"],
        multiplier=phase["premium_multiplier"],
    )


def _hold(data: Mapping[str, Any], terms: ContractTerms) -> LedgerState:
    funded = apply_entry(
        LedgerState.empty(),
        book_deposit(event_id="f05-deposit", at_ns=moment(0), cash=usd(SETUP_CASH)),
    )
    mark = price(data["before"]["option_premium_mark"])
    entry = book_option_trade(
        funded,
        event_id="f05-entry",
        at_ns=moment(0, 1),
        campaign_id=CAMPAIGN,
        legs=(LegFill(terms, data["position_quantity"], mark),),
        fees=(),
        settles_on=settle_day(1),
    )
    return apply_entry(funded, entry)


def _adjust(state: LedgerState, old: ContractTerms, new: ContractTerms) -> LedgerEntry:
    return book_deliverable_adjustment(
        state,
        event_id="f05-adjustment",
        at_ns=moment(2),
        old_contract_id=old.contract_id,
        new_terms=new,
        action_ref=ACTION_REF,
    )


@pytest.fixture(scope="module")
def f05() -> F05Run:
    data = fixture("F05")
    before_terms, after_terms = _terms(data, "before"), _terms(data, "after")
    held = _hold(data, before_terms)
    adjustment = _adjust(held, before_terms, after_terms)
    return F05Run(data, before_terms, after_terms, held, adjustment, apply_entry(held, adjustment))


def test_the_fixture_terms_differ_only_in_contract_id_and_deliverable(f05: F05Run) -> None:
    assert f05.after_terms.contract_id != f05.before_terms.contract_id
    assert (
        dataclasses.replace(
            f05.after_terms,
            contract_id=f05.before_terms.contract_id,
            deliverable=f05.before_terms.deliverable,
        )
        == f05.before_terms
    )


@pytest.mark.parametrize("name", ["before", "after"])
def test_deliverable_value_and_put_intrinsic_are_conserved(f05: F05Run, name: str) -> None:
    terms = f05.before_terms if name == "before" else f05.after_terms
    prices = {STOCK_ASSET: price(f05.phase(name)["stock_mark"])}

    assert terms.deliverable.value_usd(prices) == usd(f05.expected[f"{name}_deliverable_value_usd"])
    assert terms.intrinsic_usd(prices) == usd(f05.expected[f"{name}_put_intrinsic_usd"])


def test_option_market_value_is_conserved_through_the_ledger(f05: F05Run) -> None:
    before = value_account(f05.held, f05.quotes("before", f05.before_terms), {}, MarkBasis.MID)
    after = value_account(f05.adjusted, f05.quotes("after", f05.after_terms), {}, MarkBasis.MID)

    assert before.nlv - cash_like(f05.held) == usd(f05.expected["before_option_market_value_usd"])
    assert after.nlv - cash_like(f05.adjusted) == usd(f05.expected["after_option_market_value_usd"])
    assert after.nlv == before.nlv
    assert reconcile(after, f05.adjusted) == ZERO


def test_adjustment_moves_option_cost_without_cash_or_realized_pnl(f05: F05Run) -> None:
    cost = usd("1000.00")  # 1 x multiplier 100 x premium 10.00

    assert posting_map(f05.adjustment) == {
        account(AccountKind.OPTION_COST, f05.before_terms.contract_id): -cost,
        account(AccountKind.OPTION_COST, f05.after_terms.contract_id): cost,
    }
    assert {
        (event.instrument_id, event.kind, event.delta) for event in f05.adjustment.quantity_events
    } == {
        (f05.before_terms.contract_id, QuantityKind.ADJUST_OUT, -1),
        (f05.after_terms.contract_id, QuantityKind.ADJUST_IN, 1),
    }


def test_lots_move_one_for_one_keeping_cost_campaign_and_open_time(f05: F05Run) -> None:
    def economics(state: LedgerState, terms: ContractTerms) -> list[tuple[Any, ...]]:
        return [
            (lot.quantity, lot.unit_cost, lot.campaign_id, lot.opened_at_ns)
            for lot in state.lots.get(terms.contract_id, ())
        ]

    moved = economics(f05.held, f05.before_terms)
    assert moved == [(1, usd("1000.00"), CAMPAIGN, moment(0, 1))]
    assert economics(f05.adjusted, f05.after_terms) == moved
    assert economics(f05.adjusted, f05.before_terms) == []
    assert all(
        lot.instrument_id == f05.after_terms.contract_id
        for lot in f05.adjusted.lots[f05.after_terms.contract_id]
    )
    assert (
        balance(f05.adjusted, account(AccountKind.OPTION_COST, f05.before_terms.contract_id))
        == ZERO
    )


def test_old_contract_is_retired_and_the_multiplier_is_unchanged(f05: F05Run) -> None:
    registered = f05.adjusted.contracts[f05.after_terms.contract_id]

    assert f05.before_terms.contract_id in f05.adjusted.retired
    assert registered == f05.after_terms
    assert registered.premium_multiplier == f05.before_terms.premium_multiplier
    assert registered.premium_multiplier == Decimal(f05.phase("after")["premium_multiplier"])


def _changed(f05: F05Run, field: str) -> ContractTerms:
    """Return the fixture's after-terms with one field the ADR requires to be kept changed."""
    replacements: dict[str, Any] = {
        "expires_at_ns": f05.after_terms.expires_at_ns + 1,
        "premium_multiplier": Decimal(10),
        "option_type": OptionType.CALL,
        "exercise_style": ExerciseStyle.EUROPEAN,
        "settlement_type": SettlementType.CASH,
        "contract_id": f05.before_terms.contract_id,
    }
    return dataclasses.replace(f05.after_terms, **{field: replacements[field]})


@pytest.mark.parametrize(
    "field",
    [
        "expires_at_ns",
        "premium_multiplier",
        "option_type",
        "exercise_style",
        "settlement_type",
        "contract_id",
    ],
)
def test_an_adjustment_changing_anything_but_the_deliverable_raises(
    f05: F05Run, field: str
) -> None:
    with pytest.raises(UnsupportedLifecycle) as caught:
        _adjust(f05.held, f05.before_terms, _changed(f05, field))
    assert caught.value.code is ErrorCode.UNSUPPORTED_CORPORATE_ACTION
