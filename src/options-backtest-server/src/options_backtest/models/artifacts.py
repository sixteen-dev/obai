"""Run artifacts: events, account curve, positions, campaigns, candidates, quality (ADR 0002 §6).

Each artifact table's digest is ``data.manifest.table_digest`` of its rows. Money is ``Usd``;
``payable`` values are positive amounts owed (``-ΣPAYABLE``), matching ``R1Campaign.pay``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Final

from options_backtest.engine.clock import Phase
from options_backtest.engine.orders import ExitTrigger, NonfillReason, OrderPurpose
from options_backtest.errors import Issue
from options_backtest.models.ledger import LedgerEntry
from options_backtest.models.result import CalculationStatus, SimulationResult
from options_backtest.money import Price, Usd
from options_backtest.reference.calendars import Slot

ARTIFACT_TABLES: Final = (
    "events",
    "journal",
    "positions",
    "account_curve",
    "campaigns",
    "candidate_decisions",
    "quality",
)
"""Artifact tables, in ``ArtifactBundle.digests`` order."""


class SimEventKind(StrEnum):
    """What an event records, with its slot and phase.

    DEPOSIT (OPEN 1, first window session, seq 1); SETTLE_DUE (OPEN 1, when anything is due,
    before an INVALIDATED of the same phase); INVALIDATED (any slot; detail = the Issue; the
    loop stops after it); MARKED (DEC 3, only when a position is held and every held leg has a
    ``usable_quote`` for its closing side, the natural marks; CLOSE 3 mid and natural marks
    when a position is held: the α ``quote.ok`` witnesses); ORDER_SUBMITTED, EXIT_DEFERRED,
    ENTRY_SKIPPED, CAMPAIGN_ENDED (DEC 5, at most one per session); FILLED, NOT_FILLED,
    ORDER_CANCELLED (F1-F3 4; at F3 the seq order is NOT_FILLED, ORDER_CANCELLED, then
    CAMPAIGN_ENDED for a cancelled ROLL_OPEN); SETTLED (CUT 6); SNAPSHOT (CUT 7, with the
    account point).
    """

    DEPOSIT = "deposit"
    SETTLE_DUE = "settle_due"
    INVALIDATED = "invalidated"
    MARKED = "marked"
    ORDER_SUBMITTED = "order_submitted"
    EXIT_DEFERRED = "exit_deferred"
    ENTRY_SKIPPED = "entry_skipped"
    CAMPAIGN_ENDED = "campaign_ended"
    FILLED = "filled"
    NOT_FILLED = "not_filled"
    ORDER_CANCELLED = "order_cancelled"
    SETTLED = "settled"
    SNAPSHOT = "snapshot"


class DecisionReason(StrEnum):
    """Why a due decision submitted no order."""

    FINAL_SESSION = "FINAL_SESSION"
    CAMPAIGN_CAP = "CAMPAIGN_CAP"
    DATA_COVERAGE_GAP = "DATA_COVERAGE_GAP"
    CONDITIONS_FALSE = "CONDITIONS_FALSE"
    NO_PACKAGE = "NO_PACKAGE"
    SELECTION_BUDGET_EXCEEDED = "SELECTION_BUDGET_EXCEEDED"
    ROLL_OPEN_CANCELLED = "ROLL_OPEN_CANCELLED"
    DECISION_QUOTE_INVALID = "DECISION_QUOTE_INVALID"


@dataclass(frozen=True, slots=True)
class OrderSnapshot:
    """The live order as ``R1Campaign.order`` sees it.

    Attributes:
        order_id: Order id.
        purpose: Purpose.
        packages: Package count.
        limit_usd: Limit; None is market-style (``order.market``).

    """

    order_id: str
    purpose: OrderPurpose
    packages: int
    limit_usd: Usd | None


@dataclass(frozen=True, slots=True)
class EventSummary:
    """Account and campaign state right after an event: the α image of ``R1Campaign``.

    Attributes:
        cash: Balance of CASH.
        receivable: ΣRECEIVABLE, >= 0.
        payable: -ΣPAYABLE, >= 0.
        reserve: Σ ``campaign_encumbrances`` (settlement + fee provision).
        held: (contract_id, signed quantity) of every held contract, sorted by contract id.
        order: The order live after the event; None when none.
        status: Run status after the event.

    """

    cash: Usd
    receivable: Usd
    payable: Usd
    reserve: Usd
    held: tuple[tuple[str, int], ...]
    order: OrderSnapshot | None
    status: CalculationStatus


@dataclass(frozen=True, slots=True)
class FilledLeg:
    """One leg of a fill.

    Attributes:
        contract_id: Contract.
        contracts: Signed contracts traded: + bought, - sold.
        price: Natural price paid or received.
        quote_id: Observation the price came from.

    """

    contract_id: str
    contracts: int
    price: Price
    quote_id: str


@dataclass(frozen=True, slots=True)
class FillDetail:
    """A committed fill.

    Attributes:
        order_id: Order filled.
        purpose: Its purpose.
        packages: Packages filled (all or none).
        legs: Filled legs, in the order's leg order.
        net_debit: Whole-order ``D`` at the fill (negative for a credit), before fees.
        fees: Σ trade fee lines.
        limit_usd: The order's limit; None for FINAL.

    """

    order_id: str
    purpose: OrderPurpose
    packages: int
    legs: tuple[FilledLeg, ...]
    net_debit: Usd
    fees: Usd
    limit_usd: Usd | None


@dataclass(frozen=True, slots=True)
class NonfillDetail:
    """A failed fill attempt; the order stays live until its F3 attempt fails.

    Attributes:
        order_id: Order not filled.
        purpose: Its purpose.
        reason: First failed check.
        contract_id: Leg that failed a per-leg check (first in leg order); None otherwise.
        net_debit: Whole-order ``D`` when it was computed (checks 3 on); None before.

    """

    order_id: str
    purpose: OrderPurpose
    reason: NonfillReason
    contract_id: str | None
    net_debit: Usd | None


@dataclass(frozen=True, slots=True)
class SettlementDetail:
    """A package's cash settlement.

    Attributes:
        series: Settlement series.
        observation_id: Settlement observation (the ledger entry's ``settlement_ref``).
        value: Settlement value used.
        contract_ids: Contracts settled and retired, sorted.
        net_cash: Σ quantity × intrinsic: + received, - paid, 0 when all out of the money.
        fees: Σ settlement fee lines.

    """

    series: str
    observation_id: str
    value: Price
    contract_ids: tuple[str, ...]
    net_cash: Usd
    fees: Usd


@dataclass(frozen=True, slots=True)
class DecisionDetail:
    """A DEC phase-5 outcome.

    EXIT_DEFERRED carries ``(EXIT, DECISION_QUOTE_INVALID, the exit trigger)`` when an exit
    trigger holds, else ``(ROLL_CLOSE, DECISION_QUOTE_INVALID, None)``.

    Attributes:
        purpose: Order purpose submitted or attempted; None when no order was due.
        reason: Why no order was submitted; None for ORDER_SUBMITTED.
        trigger: Exit trigger of the submitted or deferred exit; None otherwise.

    """

    purpose: OrderPurpose | None
    reason: DecisionReason | None
    trigger: ExitTrigger | None


type EventDetail = FillDetail | NonfillDetail | SettlementDetail | DecisionDetail | Issue


@dataclass(frozen=True, slots=True)
class SimEvent:
    """One state transition or disclosure of a run.

    Attributes:
        event_id: ``{session}:{slot}:{phase}:{seq}``; also its ledger entry's id.
        at_ns: The slot's instant.
        session_date: Session.
        slot: Slot.
        phase: Phase.
        seq: 1-based count within (session, slot, phase).
        kind: What happened.
        campaign_id: Generation ``c{n}.g{k}`` concerned; None for account-wide events.
        input_refs: Observation ids used (quotes of a fill or mark, the settlement), in leg
            order for orders and fills (a FINAL order lists the DEC observation ids that
            exist, possibly none), sorted for marks.
        summary: State after the event.
        detail: Payload per kind: FILLED FillDetail; NOT_FILLED NonfillDetail; SETTLED
            SettlementDetail; ORDER_SUBMITTED, EXIT_DEFERRED, ENTRY_SKIPPED, CAMPAIGN_ENDED
            DecisionDetail; INVALIDATED the Issue; None otherwise.

    """

    event_id: str
    at_ns: int
    session_date: date
    slot: Slot
    phase: Phase
    seq: int
    kind: SimEventKind
    campaign_id: str | None
    input_refs: tuple[str, ...]
    summary: EventSummary
    detail: EventDetail | None


@dataclass(frozen=True, slots=True)
class AccountPoint:
    """The authoritative daily account snapshot (design §10.1), taken at CUT phase 7.

    Attributes:
        session_date: Session.
        market_valuation_at_ns: The session's CLOSE instant (marks are CLOSE quotes).
        ledger_cutoff_at_ns: The session's CUT instant.
        cash: Balance of CASH.
        receivable: ΣRECEIVABLE.
        payable: -ΣPAYABLE.
        encumbrance: Σ ``campaign_encumbrances`` (settlement + fee provision).
        headroom: ``funding_headroom``: cash - payable - encumbrance.
        mid_nlv: ``value_account(MID)`` at CLOSE quotes; flat: cash + receivable - payable.
            None only on a settle-only session after the window with a position held.
        natural_nlv: As ``mid_nlv`` at NATURAL marks (bid for longs, ask for shorts).

    """

    session_date: date
    market_valuation_at_ns: int
    ledger_cutoff_at_ns: int
    cash: Usd
    receivable: Usd
    payable: Usd
    encumbrance: Usd
    headroom: Usd
    mid_nlv: Usd | None
    natural_nlv: Usd | None


@dataclass(frozen=True, slots=True)
class PositionRow:
    """One held contract at a session's CUT, after settlement.

    Attributes:
        session_date: Session.
        contract_id: Contract.
        campaign_id: Generation holding it.
        quantity: Signed contracts.
        mid_mark: CLOSE quote mid; None on a settle-only session.
        natural_mark: CLOSE natural (bid if long, ask if short); None as ``mid_mark``.

    """

    session_date: date
    contract_id: str
    campaign_id: str
    quantity: int
    mid_mark: Price | None
    natural_mark: Price | None


class CampaignOutcome(StrEnum):
    """How a campaign ended.

    CLOSED: its last generation was closed by a fill (or a roll close was not reopened).
    SETTLED: its last generation was cash-settled. OPEN: held at the end under
    ``mark_open_positions``. INCOMPLETE: held at the end under ``liquidate_at_final_session``,
    or when the run stopped invalid.
    """

    CLOSED = "closed"
    SETTLED = "settled"
    OPEN = "open"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True)
class CampaignRecord:
    """One linked campaign: its generations and realized result.

    Attributes:
        campaign_id: ``c{n}``; n counts filled entries from 1.
        generations: ``c{n}.g1``, ``c{n}.g2`` ... in order; each roll adds one.
        start_session: Entry fill session (campaign session 1).
        end_session: Session its last position was closed or settled; None when OPEN or
            INCOMPLETE.
        basis: |entry package premium before fees| (whole order), the rule basis.
        realized_pnl: -ΣREALIZED_PNL posted by entries of its generations.
        fees: ΣFEES posted by entries of its generations.
        rolls: Successful replacements.
        outcome: How it ended.
        exit_trigger: Trigger of the closing order that ended it, SETTLEMENT when settled,
            ROLL_NOT_REOPENED when a roll close was not reopened; None when OPEN or INCOMPLETE.

    """

    campaign_id: str
    generations: tuple[str, ...]
    start_session: date
    end_session: date | None
    basis: Usd
    realized_pnl: Usd
    fees: Usd
    rolls: int
    outcome: CampaignOutcome
    exit_trigger: ExitTrigger | None

    @property
    def net_pnl(self) -> Usd:
        """Return the campaign P&L, ``realized_pnl - fees`` (= -ΣREALIZED_PNL - ΣFEES)."""
        raise NotImplementedError


class CandidateRejection(StrEnum):
    """Why a leg candidate was rejected (design §8.4, §9.2)."""

    QUOTE_UNAVAILABLE = "QUOTE_UNAVAILABLE"
    QUOTE_STATUS = "QUOTE_STATUS"
    NO_BID_FOR_ENTRY = "NO_BID_FOR_ENTRY"
    SPREAD = "SPREAD"
    VOLUME = "VOLUME"
    PRICING_INPUT = "PRICING_INPUT"
    OUT_OF_TOLERANCE = "OUT_OF_TOLERANCE"
    NO_OFFSET_STRIKE = "NO_OFFSET_STRIKE"


class ExpirySkip(StrEnum):
    """Why an eligible expiry produced no package candidates."""

    PRICING_INPUT_UNAVAILABLE = "PRICING_INPUT_UNAVAILABLE"
    PRUNED = "PRUNED"


class PackageVerdict(StrEnum):
    """The judgment of one evaluated package, in check order."""

    CHOSEN = "CHOSEN"
    ELIGIBLE = "ELIGIBLE"
    DUPLICATE_CONTRACT = "DUPLICATE_CONTRACT"
    STRIKE_ORDER = "STRIKE_ORDER"
    PREMIUM_DIRECTION = "PREMIUM_DIRECTION"
    NO_SIZE = "NO_SIZE"


@dataclass(frozen=True, slots=True)
class ConditionCheck:
    """One entry condition, judged on the prior session's feature.

    Attributes:
        feature_id: ``{underlying_symbol}:{feature}``.
        operator: ``gt``, ``gte``, ``lt`` or ``lte``.
        threshold: The condition's value.
        session_date: Prior table session read; None on the table's first session.
        value: Feature value; None when unknown.
        holds: ``value <operator> threshold``; False when unknown.

    """

    feature_id: str
    operator: str
    threshold: Decimal
    session_date: date | None
    value: Decimal | None
    holds: bool


@dataclass(frozen=True, slots=True)
class LegCandidate:
    """One contract considered for one leg.

    Attributes:
        leg_id: Strategy leg.
        contract_id: Contract.
        quote_id: Decision observation; None when unavailable.
        implied_vol: ``Decimal(float)`` IV from the mid; delta legs only, else None.
        spot_delta: ``Decimal(float)`` normalized spot delta; delta legs only, else None.
        error: Selector error, exact ``Decimal``; 0 for ``strike_offset``; None if rejected
            before it was computed.
        spread_usd: ``premium_usd(ask - bid, 1)``; None without a quote.
        rejection: Why rejected; None for an eligible candidate.

    """

    leg_id: str
    contract_id: str
    quote_id: str | None
    implied_vol: Decimal | None
    spot_delta: Decimal | None
    error: Decimal | None
    spread_usd: Usd | None
    rejection: CandidateRejection | None


@dataclass(frozen=True, slots=True)
class ExpiryCandidate:
    """One step-2-eligible (root, expiry), in search order.

    Attributes:
        root: Option root.
        expiry: Expiry date.
        dte: Days to expiry.
        expiry_error: ``|dte - target_dte|``.
        spot: Index value used; None when unavailable.
        discount_factor: ``Decimal(float)`` DF at the exact time to expiry; None when
            unavailable.
        forward: ``Decimal(float)`` parity forward; None when unavailable.
        skip_reason: Why it produced no packages; None when searched.
        legs: Leg candidates considered, per leg in ``leg_order``, each in candidate order.

    """

    root: str
    expiry: date
    dte: int
    expiry_error: int
    spot: Price | None
    discount_factor: Decimal | None
    forward: Decimal | None
    skip_reason: ExpirySkip | None
    legs: tuple[LegCandidate, ...]


@dataclass(frozen=True, slots=True, order=True)
class PackageScore:
    """Lexicographic package score (design §9.2 step 5); the minimum wins.

    Attributes:
        expiry_error: ``|dte - target_dte|``.
        error_sum: Σ leg selector errors.
        spread_sum: Σ leg ``spread_usd``.
        contract_ids: Contract ids in ``leg_order``.

    """

    expiry_error: int
    error_sum: Decimal
    spread_sum: Usd
    contract_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PackageCandidate:
    """One evaluated package.

    Attributes:
        root: Option root.
        expiry: Expiry date.
        contract_ids: Contracts in ``leg_order``.
        net_debit: ``D`` of one package at the decision naturals.
        packages: Size found by sizing; 0 when not sized or none fits.
        score: Its score.
        verdict: Its judgment.

    """

    root: str
    expiry: date
    contract_ids: tuple[str, ...]
    net_debit: Usd
    packages: int
    score: PackageScore
    verdict: PackageVerdict


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    """Everything one entry or replacement selection saw and decided (design §9.2 step 6).

    One record per ``selector.select`` call; a skip decided before selection (final session,
    campaign cap, GAP) records none.

    Attributes:
        decision_id: Event id of the DEC phase-5 event it produced.
        session_date: Decision session.
        at_ns: Decision instant (DEC).
        purpose: ENTRY or ROLL_OPEN.
        campaign_id: Generation id the order would open.
        conditions: Entry conditions judged, in spec order.
        conditions_hold: All hold (true when there are none).
        expiries: Eligible expiries, in search order; () when the conditions failed.
        packages: Evaluated packages, in evaluation order.
        chosen: Index of the CHOSEN package in ``packages``; None when no order.
        cap_bound: The chosen size equals ``min(max_contracts, max_contracts_per_order)`` under
            ``risk_budget``; False for ``fixed_contracts`` or no order.
        evaluations: Package evaluations performed.
        budget_exceeded: The 10,000th evaluation was reached (SELECTION_BUDGET_EXCEEDED).
        reason: Why no order; None when one was chosen.
        candidate_set_digest: ``table_digest`` of ``expiries`` followed by ``packages``.

    """

    decision_id: str
    session_date: date
    at_ns: int
    purpose: OrderPurpose
    campaign_id: str
    conditions: tuple[ConditionCheck, ...]
    conditions_hold: bool
    expiries: tuple[ExpiryCandidate, ...]
    packages: tuple[PackageCandidate, ...]
    chosen: int | None
    cap_bound: bool
    evaluations: int
    budget_exceeded: bool
    reason: DecisionReason | None
    candidate_set_digest: str


class QualityCode(StrEnum):
    """Quote-quality findings (design §8.4, §11.2)."""

    MARK_OUT_OF_RANGE = "MARK_OUT_OF_RANGE"


@dataclass(frozen=True, slots=True)
class QualityFinding:
    """A data-quality observation; reported, never repaired.

    Attributes:
        code: Finding code.
        session_date: Session.
        at_ns: Instant observed.
        refs: Observation or contract ids concerned.
        message: Values and bounds, human-readable.

    """

    code: QualityCode
    session_date: date
    at_ns: int
    refs: tuple[str, ...]
    message: str


@dataclass(frozen=True, slots=True)
class ArtifactBundle:
    """Everything a run produces (ADR 0002 §6, design §15.2 artifacts).

    Attributes:
        result: The result envelope.
        events: Events in key order.
        journal: Committed ledger entries in sequence order.
        positions: Position rows, per session in contract order.
        account_curve: One point per session that reached CUT, including settle-only ones.
        campaigns: Campaign records in campaign order.
        candidate_decisions: Selection records in decision order.
        quality: Quality findings in emission order.
        digests: (table, ``table_digest``) per ``ARTIFACT_TABLES`` entry, in that order.

    """

    result: SimulationResult
    events: tuple[SimEvent, ...]
    journal: tuple[LedgerEntry, ...]
    positions: tuple[PositionRow, ...]
    account_curve: tuple[AccountPoint, ...]
    campaigns: tuple[CampaignRecord, ...]
    candidate_decisions: tuple[CandidateDecision, ...]
    quality: tuple[QualityFinding, ...]
    digests: tuple[tuple[str, str], ...]
