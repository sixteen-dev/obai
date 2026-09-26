"""Run artifacts: events, account curve, positions, campaigns, candidates, quality (ADR 0002 §6).

Each artifact table's digest is ``data.manifest.table_digest`` of its rows. Money is ``Usd``;
``payable`` values are positive amounts owed (``-ΣPAYABLE``), matching ``R1Campaign.pay``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Final

from options_backtest.data.manifest import table_digest
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
_CAMPAIGN_ID: Final = re.compile(r"c[1-9][0-9]*")


class SimEventKind(StrEnum):
    """What an event records, with its slot and phase.

    DEPOSIT (OPEN 1, first window session, seq 1); SETTLE_DUE (OPEN 1, when anything is due,
    before an INVALIDATED of the same phase); INVALIDATED (any slot; detail = the Issue; the
    loop stops after it); MARKED (DEC 3, only when a position is held and every held leg has a
    ``usable_quote`` for its closing side, with the liquidation P&L at those natural marks;
    CLOSE 3 mid and natural marks when a position is held: the α ``quote.ok`` witnesses);
    ORDER_SUBMITTED, EXIT_DEFERRED, ENTRY_SKIPPED, CAMPAIGN_ENDED (DEC 5, at most one per
    session); FILLED, NOT_FILLED, ORDER_CANCELLED (F1-F3 4; at F3 the seq order is NOT_FILLED,
    ORDER_CANCELLED, then CAMPAIGN_ENDED for a cancelled ROLL_OPEN); SETTLED (CUT 6); SNAPSHOT
    (CUT 7, with the account point).
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


@dataclass(frozen=True, slots=True)
class LiquidationDetail:
    """The held generation's liquidation P&L at a DEC, with each component (design §10.4).

    Carried by the DEC 3 MARKED event; take-profit compares ``liquidation_pnl >= fraction ·
    basis`` and stop-loss ``liquidation_pnl <= -multiple · basis`` (ADR 0002 §17 items 35, 49).

    Attributes:
        realized_prior: P&L of the campaign's earlier generations, fees included.
        entry_debit_incl_fees: The held generation's opening ``D`` plus its fees.
        close_debit: Whole-order ``D`` of the close at the DEC naturals.
        exit_fees: Σ trade fees of that close (the estimated exit fees).
        basis: The campaign's ``|D|`` at entry, before fees.
        liquidation_pnl: ``realized_prior - entry_debit_incl_fees - close_debit - exit_fees``.

    """

    realized_prior: Usd
    entry_debit_incl_fees: Usd
    close_debit: Usd
    exit_fees: Usd
    basis: Usd
    liquidation_pnl: Usd

    def __post_init__(self) -> None:
        """Refuse a non-positive basis, negative fees and a P&L that is not its components'.

        Raises:
            ValueError: As stated, naming the field.

        """
        if self.basis.amount <= 0:
            raise ValueError(f"LiquidationDetail.basis must be positive, got {self.basis}")
        if self.exit_fees.amount < 0:
            raise ValueError(f"LiquidationDetail.exit_fees must be >= 0, got {self.exit_fees}")
        want = self.realized_prior - self.entry_debit_incl_fees - self.close_debit - self.exit_fees
        if self.liquidation_pnl != want:
            raise ValueError(f"LiquidationDetail.liquidation_pnl must be {want} (its components)")


type EventDetail = (
    FillDetail | NonfillDetail | SettlementDetail | DecisionDetail | LiquidationDetail | Issue
)


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
            DecisionDetail; MARKED at DEC LiquidationDetail; INVALIDATED the Issue; None
            otherwise.

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

    def __post_init__(self) -> None:
        """Refuse a record whose ids, rolls, basis, end or trigger contradict its outcome.

        Raises:
            ValueError: If the generations are not ``c{n}.g1`` .. ``c{n}.gk`` (k >= 1), rolls is
                not k - 1, the basis is not positive, an unfinished campaign has an end or a
                trigger (a finished one lacks either), the end precedes the start, or
                SETTLEMENT is not exactly the trigger of a SETTLED campaign.

        """
        if _CAMPAIGN_ID.fullmatch(self.campaign_id) is None:
            raise ValueError(f"campaign_id must be c{{n}}, got {self.campaign_id!r}")
        expected = tuple(f"{self.campaign_id}.g{k}" for k in range(1, len(self.generations) + 1))
        if not self.generations or self.generations != expected:
            raise ValueError(f"generations must be {self.campaign_id}.g1.. in order and non-empty")
        if self.rolls != len(self.generations) - 1:
            raise ValueError(f"rolls {self.rolls} must be one less than the generation count")
        if self.basis.amount <= 0:
            raise ValueError(f"basis must be positive, got {self.basis}")
        self._check_end()

    def _check_end(self) -> None:
        finished = self.outcome in (CampaignOutcome.CLOSED, CampaignOutcome.SETTLED)
        if finished == (self.end_session is None):
            raise ValueError(f"end_session is stated exactly when a campaign is finished: {self}")
        if finished == (self.exit_trigger is None):
            raise ValueError(f"exit_trigger is stated exactly when a campaign is finished: {self}")
        if self.end_session is not None and self.end_session < self.start_session:
            raise ValueError(f"end_session {self.end_session} precedes {self.start_session}")
        settled = self.outcome is CampaignOutcome.SETTLED
        if finished and settled != (self.exit_trigger is ExitTrigger.SETTLEMENT):
            raise ValueError(
                f"exit trigger SETTLEMENT is exactly that of a SETTLED campaign: {self.outcome} "
                f"with {self.exit_trigger}"
            )

    @property
    def net_pnl(self) -> Usd:
        """Return the campaign P&L, ``realized_pnl - fees`` (= -ΣREALIZED_PNL - ΣFEES)."""
        return self.realized_pnl - self.fees


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
    """The judgment of one evaluated package, in check order.

    DELIVERABLE_MISMATCH: the legs' contract versions deliver different deliverables (a
    ``TermsRevision`` of one leg), so the package is not an R1 package (design §9.1 item 1).
    """

    CHOSEN = "CHOSEN"
    ELIGIBLE = "ELIGIBLE"
    DUPLICATE_CONTRACT = "DUPLICATE_CONTRACT"
    DELIVERABLE_MISMATCH = "DELIVERABLE_MISMATCH"
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
        candidate_set_digest: ``table_digest`` of ``expiries`` followed by ``packages``;
            computed by the constructor, never passed (ADR 0002 §17 item 52).

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
    candidate_set_digest: str = field(init=False)

    def __post_init__(self) -> None:
        """Refuse a record whose choice, reason or counts contradict each other; digest it.

        Raises:
            ValueError: If the purpose is not opening, ``conditions_hold`` is not the
                conditions' conjunction, failed conditions carry candidates or another reason
                than CONDITIONS_FALSE, ``evaluations`` is not the package count, ``chosen`` is
                not the one CHOSEN package, ``reason`` is not stated exactly without a choice,
                ``budget_exceeded`` is not exactly SELECTION_BUDGET_EXCEEDED, or ``cap_bound``
                has no choice.

        """
        if not self.purpose.opening:
            raise ValueError(f"purpose must be ENTRY or ROLL_OPEN, got {self.purpose}")
        if self.conditions_hold != all(check.holds for check in self.conditions):
            raise ValueError("conditions_hold must be the conjunction of the conditions")
        failed = self.reason is DecisionReason.CONDITIONS_FALSE
        if failed != (not self.conditions_hold) or (failed and self.expiries + self.packages):
            raise ValueError(
                "failed conditions record no candidates and reason CONDITIONS_FALSE, and only they"
            )
        if self.evaluations != len(self.packages):
            raise ValueError(f"evaluations {self.evaluations} must count the packages evaluated")
        self._check_choice()
        digest = table_digest((*self.expiries, *self.packages))
        object.__setattr__(self, "candidate_set_digest", digest)

    def _check_choice(self) -> None:
        chosen = [i for i, p in enumerate(self.packages) if p.verdict is PackageVerdict.CHOSEN]
        if chosen != ([] if self.chosen is None else [self.chosen]):
            raise ValueError(f"chosen {self.chosen} must index the one CHOSEN package: {chosen}")
        if (self.reason is None) != (self.chosen is not None):
            raise ValueError(f"reason is stated exactly when nothing is chosen: {self.reason}")
        exceeded = self.reason is DecisionReason.SELECTION_BUDGET_EXCEEDED
        if self.budget_exceeded != exceeded:
            raise ValueError("budget_exceeded goes exactly with reason SELECTION_BUDGET_EXCEEDED")
        if self.cap_bound and self.chosen is None:
            raise ValueError("cap_bound needs a chosen package")


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
        digests: (table, ``table_digest``) per ``ARTIFACT_TABLES`` entry, in that order;
            computed by the constructor, never passed (ADR 0002 §17 item 52).

    """

    result: SimulationResult
    events: tuple[SimEvent, ...]
    journal: tuple[LedgerEntry, ...]
    positions: tuple[PositionRow, ...]
    account_curve: tuple[AccountPoint, ...]
    campaigns: tuple[CampaignRecord, ...]
    candidate_decisions: tuple[CandidateDecision, ...]
    quality: tuple[QualityFinding, ...]
    digests: tuple[tuple[str, str], ...] = field(init=False)

    def __post_init__(self) -> None:
        """Refuse a result of another type, then digest every table once.

        Raises:
            TypeError: If ``result`` is not a ``SimulationResult``, or a table cannot be encoded.

        """
        if not isinstance(self.result, SimulationResult):
            raise TypeError(f"ArtifactBundle.result must be a SimulationResult: {self.result!r}")
        digests = tuple((name, table_digest(getattr(self, name))) for name in ARTIFACT_TABLES)
        object.__setattr__(self, "digests", digests)
