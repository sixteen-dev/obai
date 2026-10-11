"""Artifact records, campaign P&L and deterministic digests (ADR 0002 §6, §17 items 9, 34, 42)."""

import hashlib
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from selection_builders import MON, TUE, WED, flat_result, usd

from options_backtest.data.manifest import table_digest
from options_backtest.engine.clock import Phase
from options_backtest.engine.orders import ExitTrigger, OrderPurpose
from options_backtest.engine.trades import book_deposit
from options_backtest.models.artifacts import (
    ARTIFACT_TABLES,
    AccountPoint,
    ArtifactBundle,
    CampaignOutcome,
    CampaignRecord,
    CandidateDecision,
    ConditionCheck,
    DecisionReason,
    EventSummary,
    ExpiryCandidate,
    LiquidationDetail,
    PackageCandidate,
    PackageScore,
    PackageVerdict,
    SimEvent,
    SimEventKind,
)
from options_backtest.models.result import CalculationStatus
from options_backtest.money import ZERO_USD
from options_backtest.reference.calendars import Slot

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
UNFINISHED = [CampaignOutcome.OPEN, CampaignOutcome.INCOMPLETE]
FINISHED = [CampaignOutcome.CLOSED, CampaignOutcome.SETTLED]
MAR_12 = date(2024, 3, 12)
FAILED_CONDITION = ConditionCheck("SPX:underlying.return_20s", "gt", Decimal(0), None, None, False)

type Tables = dict[str, tuple[Any, ...]]


# --- campaign records -----------------------------------------------------------------------------


def _record(**changes: Any) -> CampaignRecord:
    record = CampaignRecord(
        campaign_id="c1",
        generations=("c1.g1", "c1.g2"),
        start_session=MON,
        end_session=MAR_12,
        basis=usd("90.00"),
        realized_pnl=usd("60.00"),
        fees=usd("8.00"),
        rolls=1,
        outcome=CampaignOutcome.CLOSED,
        exit_trigger=ExitTrigger.TAKE_PROFIT,
    )
    return replace(record, **changes)


def test_campaign_net_pnl_is_realized_less_fees() -> None:
    assert _record().net_pnl == usd("52.00")  # G09
    assert _record(realized_pnl=usd("-50.00"), fees=usd("4.00")).net_pnl == usd("-54.00")


@pytest.mark.parametrize("outcome", UNFINISHED)
def test_an_unfinished_campaign_has_no_end_and_no_trigger(outcome: CampaignOutcome) -> None:
    record = _record(outcome=outcome, end_session=None, exit_trigger=None)

    assert (record.end_session, record.exit_trigger) == (None, None)
    with pytest.raises(ValueError, match="end_session"):
        _record(outcome=outcome, exit_trigger=None)
    with pytest.raises(ValueError, match="exit_trigger"):
        _record(outcome=outcome, end_session=None)


@pytest.mark.parametrize("outcome", FINISHED)
def test_a_finished_campaign_has_an_end_and_a_trigger(outcome: CampaignOutcome) -> None:
    with pytest.raises(ValueError, match="end_session"):
        _record(outcome=outcome, end_session=None)
    with pytest.raises(ValueError, match="exit_trigger"):
        _record(outcome=outcome, exit_trigger=None)


def test_only_a_settled_campaign_ends_by_settlement() -> None:
    settled = _record(outcome=CampaignOutcome.SETTLED, exit_trigger=ExitTrigger.SETTLEMENT)

    assert settled.exit_trigger is ExitTrigger.SETTLEMENT
    with pytest.raises(ValueError, match="SETTLEMENT"):
        _record(outcome=CampaignOutcome.SETTLED, exit_trigger=ExitTrigger.TIME_EXIT)
    with pytest.raises(ValueError, match="SETTLEMENT"):
        _record(outcome=CampaignOutcome.CLOSED, exit_trigger=ExitTrigger.SETTLEMENT)


def test_a_campaign_links_its_generations_in_order_one_roll_each() -> None:
    assert _record(generations=("c1.g1",), rolls=0).rolls == 0
    with pytest.raises(ValueError, match="generations"):
        _record(generations=("c1.g2", "c1.g1"))
    with pytest.raises(ValueError, match="generations"):
        _record(generations=("c2.g1", "c2.g2"))
    with pytest.raises(ValueError, match="generations"):
        _record(generations=(), rolls=-1)
    with pytest.raises(ValueError, match="rolls"):
        _record(rolls=0)


def test_a_campaign_has_a_positive_basis_and_ends_after_it_starts() -> None:
    with pytest.raises(ValueError, match="basis"):
        _record(basis=ZERO_USD)
    with pytest.raises(ValueError, match="end_session"):
        _record(start_session=WED, end_session=TUE)


# --- liquidation P&L at a held DEC ---------------------------------------------------------------


def _liquidation(**changes: Any) -> LiquidationDetail:
    """G01's Tuesday: 0 - (-90 + 2) - 80 - 2 = 6 on a basis of 90."""
    detail = LiquidationDetail(
        realized_prior=ZERO_USD,
        entry_debit_incl_fees=usd("-88.00"),
        close_debit=usd("80.00"),
        exit_fees=usd("2.00"),
        basis=usd("90.00"),
        liquidation_pnl=usd("6.00"),
    )
    return replace(detail, **changes)


def test_a_liquidation_detail_is_the_sum_of_its_components() -> None:
    assert _liquidation(realized_prior=usd("-10.00"), liquidation_pnl=usd("-4.00")).basis == usd(
        "90.00"
    )
    with pytest.raises(ValueError, match="liquidation_pnl"):
        _liquidation(liquidation_pnl=usd("6.01"))
    with pytest.raises(ValueError, match="basis"):
        _liquidation(basis=ZERO_USD)
    with pytest.raises(ValueError, match="exit_fees"):
        _liquidation(exit_fees=usd("-2.00"), liquidation_pnl=usd("10.00"))


# --- candidate decisions --------------------------------------------------------------------------


def _expiry() -> ExpiryCandidate:
    return ExpiryCandidate(
        root="SPXW",
        expiry=date(2024, 3, 15),
        dte=11,
        expiry_error=0,
        spot=None,
        discount_factor=None,
        forward=None,
        skip_reason=None,
        legs=(),
    )


def _package(verdict: PackageVerdict = PackageVerdict.CHOSEN) -> PackageCandidate:
    ids = ("SPXW:2024-03-15:P:4900", "SPXW:2024-03-15:P:4895")
    return PackageCandidate(
        root="SPXW",
        expiry=date(2024, 3, 15),
        contract_ids=ids,
        net_debit=usd("-90.00"),
        packages=1,
        score=PackageScore(0, Decimal("1.8E-17"), usd("30.00"), ids),
        verdict=verdict,
    )


def _decision(**changes: Any) -> CandidateDecision:
    fields: dict[str, Any] = {
        "decision_id": "2024-03-04:DEC:5:1",
        "session_date": MON,
        "at_ns": 1_709_584_200_000_000_000,
        "purpose": OrderPurpose.ENTRY,
        "campaign_id": "c1.g1",
        "conditions": (),
        "conditions_hold": True,
        "expiries": (_expiry(),),
        "packages": (_package(),),
        "chosen": 0,
        "cap_bound": False,
        "evaluations": 1,
        "budget_exceeded": False,
        "reason": None,
    }
    fields.update(changes)
    return CandidateDecision(**fields)


def _no_order(**changes: Any) -> CandidateDecision:
    eligible = (_package(PackageVerdict.ELIGIBLE),)
    return _decision(**{"packages": eligible, "chosen": None, **changes})


def test_a_candidate_decision_digests_its_expiries_then_its_packages() -> None:
    decision = _decision()

    assert decision.candidate_set_digest == table_digest((_expiry(), _package()))
    assert decision.candidate_set_digest != table_digest((_package(), _expiry()))


def test_a_candidate_decision_derives_its_digest_and_takes_none() -> None:
    with pytest.raises(TypeError, match="candidate_set_digest"):
        _decision(candidate_set_digest=table_digest((_expiry(), _package())))


def test_a_candidate_decision_without_an_order_states_why() -> None:
    with pytest.raises(ValueError, match="reason"):
        _no_order(reason=None)
    assert _no_order(reason=DecisionReason.NO_PACKAGE).chosen is None


def test_a_candidate_decision_with_an_order_has_no_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        _decision(reason=DecisionReason.NO_PACKAGE)


def test_a_candidate_decision_points_at_its_one_chosen_package() -> None:
    with pytest.raises(ValueError, match="chosen"):
        _decision(packages=(_package(PackageVerdict.ELIGIBLE),))
    with pytest.raises(ValueError, match="chosen"):
        _decision(chosen=1)
    with pytest.raises(ValueError, match="chosen"):
        _decision(packages=(_package(), _package()), chosen=0, evaluations=2)
    with pytest.raises(ValueError, match="chosen"):
        _no_order(packages=(_package(),), reason=DecisionReason.NO_PACKAGE)


def test_a_candidate_decision_counts_one_evaluation_per_package() -> None:
    with pytest.raises(ValueError, match="evaluations"):
        _decision(evaluations=2)


def test_the_budget_flag_and_its_reason_go_together() -> None:
    exceeded = _no_order(budget_exceeded=True, reason=DecisionReason.SELECTION_BUDGET_EXCEEDED)

    assert exceeded.budget_exceeded
    with pytest.raises(ValueError, match="budget_exceeded"):
        _no_order(budget_exceeded=True, reason=DecisionReason.NO_PACKAGE)
    with pytest.raises(ValueError, match="budget_exceeded"):
        _no_order(reason=DecisionReason.SELECTION_BUDGET_EXCEEDED)


def test_a_bound_cap_needs_a_chosen_size() -> None:
    assert _decision(cap_bound=True).cap_bound
    with pytest.raises(ValueError, match="cap_bound"):
        _no_order(cap_bound=True, reason=DecisionReason.NO_PACKAGE)


def test_false_conditions_record_no_candidates() -> None:
    decision = _decision(
        conditions=(FAILED_CONDITION,),
        conditions_hold=False,
        expiries=(),
        packages=(),
        chosen=None,
        evaluations=0,
        reason=DecisionReason.CONDITIONS_FALSE,
    )

    assert decision.candidate_set_digest == EMPTY_SHA256
    with pytest.raises(ValueError, match="conditions_hold"):
        _decision(conditions=(FAILED_CONDITION,))
    with pytest.raises(ValueError, match="conditions_hold"):
        _decision(conditions_hold=False)
    with pytest.raises(ValueError, match="CONDITIONS_FALSE"):
        _no_order(
            conditions=(FAILED_CONDITION,), conditions_hold=False, reason=DecisionReason.NO_PACKAGE
        )


def test_a_candidate_decision_is_an_opening_decision() -> None:
    with pytest.raises(ValueError, match="purpose"):
        _decision(purpose=OrderPurpose.EXIT)


# --- the bundle -----------------------------------------------------------------------------------


def _event() -> SimEvent:
    summary = EventSummary(
        cash=usd("10000.00"),
        receivable=ZERO_USD,
        payable=ZERO_USD,
        reserve=ZERO_USD,
        held=(),
        order=None,
        status=CalculationStatus.VALID,
    )
    return SimEvent(
        event_id="2024-03-04:OPEN:1:1",
        at_ns=0,
        session_date=MON,
        slot=Slot.OPEN,
        phase=Phase.SETTLE_DUE,
        seq=1,
        kind=SimEventKind.DEPOSIT,
        campaign_id=None,
        input_refs=(),
        summary=summary,
        detail=None,
    )


def _point(day: date, cash: str) -> AccountPoint:
    return AccountPoint(
        session_date=day,
        market_valuation_at_ns=1,
        ledger_cutoff_at_ns=2,
        cash=usd(cash),
        receivable=ZERO_USD,
        payable=ZERO_USD,
        encumbrance=ZERO_USD,
        headroom=usd(cash),
        mid_nlv=usd(cash),
        natural_nlv=usd(cash),
    )


def _tables(*curve: AccountPoint) -> Tables:
    deposit = book_deposit(event_id="2024-03-04:OPEN:1:1", at_ns=0, cash=usd("10000.00"))
    return {
        "events": (_event(),),
        "journal": (deposit,),
        "positions": (),
        "account_curve": curve or (_point(MON, "10000.00"), _point(TUE, "10000.00")),
        "campaigns": (_record(),),
        "candidate_decisions": (_decision(),),
        "quality": (),
    }


def _digests(tables: Tables) -> tuple[tuple[str, str], ...]:
    return tuple((name, table_digest(tables[name])) for name in ARTIFACT_TABLES)


def _bundle(tables: Tables) -> ArtifactBundle:
    return ArtifactBundle(result=flat_result(), **tables)


def test_a_bundle_carries_each_table_digest_in_artifact_table_order() -> None:
    tables = _tables()

    bundle = _bundle(tables)

    assert bundle.digests == _digests(tables)
    assert [name for name, _ in bundle.digests] == list(ARTIFACT_TABLES)
    assert dict(bundle.digests)["positions"] == EMPTY_SHA256
    assert dict(bundle.digests)["events"] == table_digest(tables["events"])


def test_bundle_digests_are_deterministic_and_track_every_row() -> None:
    first, second = _tables(), _tables()
    changed = _tables(_point(MON, "10000.00"), _point(WED, "10000.01"))

    assert _digests(first) == _digests(second)
    assert dict(_digests(changed))["account_curve"] != dict(_digests(first))["account_curve"]
    assert dict(_digests(changed))["events"] == dict(_digests(first))["events"]


def test_a_bundle_derives_its_digests_and_takes_none() -> None:
    tables = _tables()

    with pytest.raises(TypeError, match="digests"):
        ArtifactBundle(result=flat_result(), digests=_digests(tables), **tables)  # type: ignore[call-arg]
