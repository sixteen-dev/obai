"""Lifecycle at OPEN and CUT: re-versioned held contracts and PM cash settlement (ADR 0002 §7).

The engine builders' vertical (short 4900 / long 4895 SPXW puts of generation ``c1.g1``)
expires Wednesday 2024-03-06 at 16:00; its CUT is 23:59:59 that day and its dues settle on
Thursday. G02: settlement 4,897 between the strikes costs the short put 100 · 3 = 300.
"""

from dataclasses import replace
from datetime import date

import pytest
from data_builders import (
    TUE,
    WED,
    contract_version,
    local_ns,
    option_terms,
    settlement_obs,
    slot_ns,
)
from engine_builders import (
    GENERATION,
    LONG,
    LONG_ID,
    MON_S,
    SCHEDULE,
    SHORT,
    SHORT_ID,
    THU,
    TUE_S,
    WED_S,
    funded,
    held,
    market,
    price,
    usd,
)

from options_backtest.data.asof import AsOfView
from options_backtest.data.manifest import FrozenDataset
from options_backtest.engine.fees import AssumedFlatFeeSchedule, lifecycle_fees, trade_fees
from options_backtest.engine.journal import apply_entry
from options_backtest.engine.lifecycle import (
    ExpirySettlement,
    expiring_contracts,
    revised_contracts,
    settle_expiring,
)
from options_backtest.engine.settlement import book_cash_settlement
from options_backtest.engine.trades import book_deposit, book_option_trade
from options_backtest.errors import SimulationInvariantError
from options_backtest.models.ledger import (
    AccountKey,
    AccountKind,
    EntryKind,
    FeeEvent,
    LedgerState,
    LegFill,
    Lot,
)
from options_backtest.models.market import OptionType, SettlementType

SETTLEMENT_ID = "s:SPX_PM:2024-03-06:c0"
VERSIONS = {SHORT_ID: f"{SHORT_ID}@v1", LONG_ID: f"{LONG_ID}@v1"}
PACKAGE = (LONG_ID, SHORT_ID)  # sorted
WITH_SETTLEMENT_FEE = AssumedFlatFeeSchedule(
    schedule_id="flat_with_settlement_fee",
    trade_per_contract=usd("1.00"),
    exercise_assignment_per_contract=usd("0"),
    cash_settlement_per_contract=usd("0.25"),
)


def at_cut(dataset: FrozenDataset, session_date: date = WED) -> AsOfView:
    session = {TUE: TUE_S, WED: WED_S}[session_date]
    return AsOfView(dataset, session.cutoff_ns)


def settle(
    state: LedgerState,
    dataset: FrozenDataset,
    schedule: AssumedFlatFeeSchedule = SCHEDULE,
) -> ExpirySettlement | None:
    return settle_expiring(
        state,
        at_cut(dataset),
        schedule,
        event_id="2024-03-06:CUT:6:1",
        session=WED_S,
        settles_on=THU,
    )


# --- revised_contracts ---------------------------------------------------------------------


def test_held_contracts_on_their_traded_version_are_not_revised() -> None:
    view = AsOfView(market(), TUE_S.open_ns)

    assert revised_contracts(view, VERSIONS) == ()


def test_a_held_contract_with_a_new_version_at_open_is_revised() -> None:
    # TermsRevision: v1 ends at Tuesday's open and v2 takes effect then.
    tue_open = TUE_S.open_ns
    contracts = (
        contract_version(SHORT, effective_to_ns=tue_open),
        contract_version(SHORT, version=2, effective_from_ns=tue_open),
        contract_version(LONG),
    )
    dataset = market(contracts=contracts)

    assert revised_contracts(AsOfView(dataset, MON_S.open_ns), VERSIONS) == ()
    assert revised_contracts(AsOfView(dataset, tue_open), VERSIONS) == (SHORT_ID,)


def test_a_held_contract_without_a_version_is_revised_and_the_result_is_sorted() -> None:
    view = AsOfView(market(contracts=()), TUE_S.open_ns)

    assert revised_contracts(view, VERSIONS) == PACKAGE


def test_nothing_held_nothing_revised() -> None:
    assert revised_contracts(AsOfView(market(), TUE_S.open_ns), {}) == ()


# --- expiring_contracts --------------------------------------------------------------------


def test_held_contracts_expire_in_the_session_holding_their_expiry() -> None:
    state = held()

    assert expiring_contracts(state, WED_S) == PACKAGE
    assert expiring_contracts(state, TUE_S) == ()


def test_a_flat_account_has_nothing_expiring() -> None:
    assert expiring_contracts(funded(), WED_S) == ()


def test_deposited_stock_is_not_an_expiring_contract() -> None:
    stock = Lot("stock-1", "SPY", 10, usd("500.00"), None, MON_S.open_ns)
    deposit = book_deposit(
        event_id="deposit", at_ns=MON_S.open_ns, cash=usd("1.00"), stock=(stock,)
    )
    state = apply_entry(LedgerState.empty(), deposit)

    assert expiring_contracts(state, WED_S) == ()


# --- settle_expiring -----------------------------------------------------------------------


def test_a_package_settles_in_one_entry_at_the_final_value_t_plus_1() -> None:
    state = held()
    dataset = market(settlements=(settlement_obs("SPX_PM", WED, "4897"),))

    outcome = settle(state, dataset, WITH_SETTLEMENT_FEE)

    fees = lifecycle_fees(WITH_SETTLEMENT_FEE, FeeEvent.CASH_SETTLEMENT, 2)
    expected = book_cash_settlement(
        state,
        event_id="2024-03-06:CUT:6:1",
        at_ns=WED_S.cutoff_ns,
        contract_ids=PACKAGE,
        settlement={"SPX": price("4897")},
        fees=fees,
        settles_on=THU,
        settlement_ref=SETTLEMENT_ID,
    )
    assert outcome == ExpirySettlement(GENERATION, PACKAGE, "SPX_PM", SETTLEMENT_ID, expected)
    assert outcome.entry is not None
    assert (outcome.entry.kind, outcome.entry.input_refs) == (
        EntryKind.CASH_SETTLEMENT,
        (SETTLEMENT_ID,),
    )
    postings = {posting.account: posting.amount for posting in outcome.entry.postings}
    # -100 · (4900 - 4897) for the short put, 0 for the long one, plus 2 · 0.25 settlement fees.
    assert postings[AccountKey(AccountKind.PAYABLE, THU.isoformat())] == usd("-300.50")
    assert apply_entry(state, outcome.entry).retired >= set(PACKAGE)


def test_an_xsp_package_settles_on_its_own_deliverable_asset() -> None:
    # G19-like: XSP contracts deliver XSP index units, so the value keys the XSP asset.
    short = option_terms(WED, OptionType.PUT, "452", root="XSP", underlying="XSP")
    long = option_terms(WED, OptionType.PUT, "451", root="XSP", underlying="XSP")
    state = _holding((LegFill(short, -1, price("1.00")), LegFill(long, 1, price("0.50"))))
    contracts = (contract_version(short, root="XSP"), contract_version(long, root="XSP"))
    dataset = market(contracts=contracts, settlements=(settlement_obs("XSP_PM", WED, "451.24"),))

    outcome = settle(state, dataset)

    assert outcome is not None
    assert outcome.entry is not None
    assert (outcome.series, outcome.observation_id) == ("XSP_PM", "s:XSP_PM:2024-03-06:c0")
    postings = {posting.account: posting.amount for posting in outcome.entry.postings}
    # 452 put: AEA 100 · 452 - 100 · 451.24 = 76.00 owed; the 451 put expires worthless.
    assert postings[AccountKey(AccountKind.PAYABLE, THU.isoformat())] == usd("-76.00")


def test_a_package_on_several_deliverable_assets_is_an_invariant_error() -> None:
    other = option_terms(WED, OptionType.PUT, "4895", underlying="SPX2")
    state = _holding((LegFill(SHORT, -1, price("2.00")), LegFill(other, 1, price("1.10"))))
    contracts = (
        contract_version(SHORT),
        replace(contract_version(other), settlement_series="SPX_PM"),
    )
    dataset = market(contracts=contracts, settlements=(settlement_obs("SPX_PM", WED, "4897"),))

    with pytest.raises(SimulationInvariantError, match="asset"):
        settle(state, dataset)


def test_a_ledger_rejection_of_the_engines_own_settlement_is_an_invariant_error() -> None:
    # WP1 cash-settles only cash-settled contracts; a physical leg is an engine defect here.
    physical = replace(LONG, settlement_type=SettlementType.PHYSICAL)
    state = _holding((LegFill(SHORT, -1, price("2.00")), LegFill(physical, 1, price("1.10"))))
    contracts = (contract_version(SHORT), contract_version(physical))
    dataset = market(contracts=contracts, settlements=(settlement_obs("SPX_PM", WED, "4897"),))

    with pytest.raises(SimulationInvariantError, match="ledger"):
        settle(state, dataset)


def test_an_outcome_has_an_entry_exactly_when_it_has_its_observation() -> None:
    dataset = market(settlements=(settlement_obs("SPX_PM", WED, "4897"),))
    outcome = settle(held(), dataset)
    assert outcome is not None

    with pytest.raises(ValueError, match="entry"):
        replace(outcome, entry=None)
    with pytest.raises(ValueError, match="entry"):
        replace(outcome, observation_id=None)
    with pytest.raises(ValueError, match="entry"):
        replace(outcome, observation_id="s:SPX_PM:2024-03-06:c1")


def test_settlement_fees_count_every_contract_of_the_package() -> None:
    state = held(packages=2)
    dataset = market(settlements=(settlement_obs("SPX_PM", WED, "4897"),))

    outcome = settle(state, dataset, WITH_SETTLEMENT_FEE)

    assert outcome is not None
    assert outcome.entry is not None
    assert [(line.event, line.contracts) for line in outcome.entry.fee_lines] == [
        (FeeEvent.CASH_SETTLEMENT, 4)
    ]


def test_an_all_out_of_the_money_package_settles_with_zero_flow_and_keeps_its_reference() -> None:
    # G03: settlement 5,000 above both strikes.
    state = held()
    dataset = market(settlements=(settlement_obs("SPX_PM", WED, "5000"),))

    outcome = settle(state, dataset)

    assert outcome is not None
    assert outcome.entry is not None
    assert outcome.entry.input_refs == (SETTLEMENT_ID,)
    dated = [p for p in outcome.entry.postings if p.account.kind is AccountKind.PAYABLE]
    assert dated == []
    after = apply_entry(state, outcome.entry)
    assert after.retired >= set(PACKAGE)
    assert dict(after.lots) == {}


@pytest.mark.parametrize(
    "settlements",
    [
        (),  # never published
        (settlement_obs("SPX_PM", WED, "4897", available_at_ns=local_ns(THU, 9)),),  # after CUT
        (settlement_obs("SPX_PM", WED, "4897", final=False),),  # preliminary only
    ],
)
def test_no_final_value_by_the_cutoff_is_a_missing_settlement(
    settlements: tuple[object, ...],
) -> None:
    outcome = settle(held(), market(settlements=settlements))  # type: ignore[arg-type]

    assert outcome == ExpirySettlement(GENERATION, PACKAGE, "SPX_PM", None, None)


def test_nothing_expiring_settles_nothing() -> None:
    dataset = market(settlements=(settlement_obs("SPX_PM", WED, "4897"),))

    outcome = settle_expiring(
        held(),
        at_cut(dataset, TUE),
        SCHEDULE,
        event_id="2024-03-05:CUT:6:1",
        session=TUE_S,
        settles_on=WED,
    )

    assert outcome is None


def _holding(legs: tuple[LegFill, ...]) -> LedgerState:
    """Return a funded state after one generation's opening trade of ``legs`` at Monday F1."""
    state = funded()
    entry = book_option_trade(
        state,
        event_id="2024-03-04:F1:4:1",
        at_ns=slot_ns(MON_S, "F1"),
        campaign_id=GENERATION,
        legs=legs,
        fees=trade_fees(SCHEDULE, legs),
        settles_on=TUE,
    )
    return apply_entry(state, entry)


def _two_generations() -> LedgerState:
    state = funded()
    for terms, contracts, campaign_id in ((SHORT, -1, "c1.g1"), (LONG, 1, "c2.g1")):
        legs = (LegFill(terms, contracts, price("1.00")),)
        entry = book_option_trade(
            state,
            event_id=f"trade-{campaign_id}",
            at_ns=slot_ns(MON_S, "F1"),
            campaign_id=campaign_id,
            legs=legs,
            fees=trade_fees(SCHEDULE, legs),
            settles_on=TUE,
        )
        state = apply_entry(state, entry)
    return state


def test_expiring_contracts_of_two_generations_are_an_invariant_error() -> None:
    dataset = market(settlements=(settlement_obs("SPX_PM", WED, "4897"),))

    with pytest.raises(SimulationInvariantError, match="generation"):
        settle(_two_generations(), dataset)


def test_expiring_contracts_of_two_series_are_an_invariant_error() -> None:
    contracts = (
        contract_version(SHORT),
        replace(contract_version(LONG), settlement_series="SPX_AM"),
    )
    dataset = market(contracts=contracts, settlements=(settlement_obs("SPX_PM", WED, "4897"),))

    with pytest.raises(SimulationInvariantError, match="series"):
        settle(held(), dataset)


def test_a_held_contract_without_a_version_at_cut_is_an_invariant_error() -> None:
    dataset = market(contracts=(), settlements=(settlement_obs("SPX_PM", WED, "4897"),))

    with pytest.raises(SimulationInvariantError, match="version"):
        settle(held(), dataset)


def test_settle_expiring_runs_at_the_sessions_cutoff_and_settles_after_it() -> None:
    dataset = market(settlements=(settlement_obs("SPX_PM", WED, "4897"),))
    at_close = AsOfView(dataset, WED_S.close_ns)

    with pytest.raises(ValueError, match="cutoff"):
        settle_expiring(held(), at_close, SCHEDULE, event_id="e", session=WED_S, settles_on=THU)
    with pytest.raises(ValueError, match="settles_on"):
        settle_expiring(
            held(), at_cut(dataset), SCHEDULE, event_id="e", session=WED_S, settles_on=WED
        )
