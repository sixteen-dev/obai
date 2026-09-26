"""Deterministic package selection and sizing at a decision (ADR 0002 §8, design §9.2).

Called at DEC for a due ENTRY or ROLL_OPEN (``campaign.decide_flat`` owns flatness, schedule,
final-session and campaign-cap gates; a replacement skips the schedule and keeps the
conditions). Everything is read through the decision's as-of view; the outcome records every
candidate considered, so the same inputs in any order give the same order and digest.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Context, Decimal, Inexact, InvalidOperation, localcontext
from fractions import Fraction
from itertools import pairwise
from math import floor
from types import MappingProxyType
from typing import Final

from options_backtest.data.asof import AsOfView
from options_backtest.data.records import (
    ContractVersion,
    QuoteObservation,
    QuoteStatus,
    TradingSession,
)
from options_backtest.engine.clock import QUOTE_MAX_AGE_NS, SPOT_MAX_AGE_NS
from options_backtest.engine.fees import AssumedFlatFeeSchedule, trade_fees
from options_backtest.engine.funding import expiry_bounds, funding_headroom
from options_backtest.engine.journal import apply_entry
from options_backtest.engine.orders import (
    Order,
    OrderLeg,
    OrderPurpose,
    natural_price,
    order_id,
    package_debit,
    usable_quote,
)
from options_backtest.engine.trades import book_option_trade
from options_backtest.errors import (
    LedgerInvariantError,
    SimulationInvariantError,
    UnsupportedLifecycle,
)
from options_backtest.models.artifacts import (
    CandidateDecision,
    CandidateRejection,
    ConditionCheck,
    DecisionReason,
    ExpiryCandidate,
    ExpirySkip,
    LegCandidate,
    PackageCandidate,
    PackageScore,
    PackageVerdict,
)
from options_backtest.models.ledger import CASH_KINDS, LedgerState, LegFill
from options_backtest.models.market import (
    ContractTerms,
    ExerciseStyle,
    OptionType,
    Quote,
    SettlementType,
    require_id,
)
from options_backtest.models.strategy import (
    Condition,
    DeltaStrike,
    Leg,
    Liquidity,
    MoneynessStrike,
    RiskBudget,
    SequentialRoll,
    StrategySpec,
    StrikeOffset,
    TargetDteExpiry,
)
from options_backtest.models.strategy_checks import (
    CONDOR_ROLES,
    LONG_PUT_CALL_ROLES,
    PremiumDirection,
    ValidatedStrategy,
)
from options_backtest.money import EXACT, ZERO_USD, Price, Usd
from options_backtest.pricing.european import spot_delta
from options_backtest.pricing.features import feature_id
from options_backtest.pricing.iv import ParityPair, implied_vol, parity_forward
from options_backtest.reference.calendars import dte, slot_times
from options_backtest.reference.products import ProductRules, contract_id, product_rules
from options_backtest.reference.rates import CMT_CURVE_ID

MAX_PACKAGE_EVALUATIONS: Final = 10_000
"""Reaching this many package evaluations stops the search: SELECTION_BUDGET_EXCEEDED."""

_WIDE: Final = Context(prec=1100, traps=[Inexact, InvalidOperation])
"""Exact context for float lifts: any float's expansion minus a 9-place target fits 1100 digits."""
_NS_PER_DAY: Final = 86_400 * 10**9
_NS_PER_YEAR: Final = 365 * _NS_PER_DAY
_MAX_LEGS: Final = 4
_GENERATION_ID: Final = re.compile(r"c[1-9][0-9]*\.g[1-9][0-9]*")
_ASCENDING_ROLES: Final = MappingProxyType(
    {
        "single_long": (),
        "vertical": (),
        "iron_condor": CONDOR_ROLES,
        "long_strangle": LONG_PUT_CALL_ROLES,
    }
)
"""(side, option type) roles whose strikes must strictly ascend, per structure; none for a
single leg or a vertical, and a straddle's strikes are equal instead."""


@dataclass(frozen=True, slots=True)
class SelectionContext:
    """Decision inputs besides the strategy and the view.

    Attributes:
        decision_id: Event id of the DEC phase-5 event this decision produces.
        session: Decision session; the view is at its DEC.
        prior_session: Previous table session, whose features the conditions read; None on
            the table's first session.
        settles_on: Next table session (the preview's settlement date).
        purpose: ENTRY or ROLL_OPEN.
        campaign_id: Generation id the order opens (``campaign.opening_campaign_id``).
        state: Ledger state at DEC (flat: no option lots).
        schedule: Fee schedule.

    """

    decision_id: str
    session: TradingSession
    prior_session: date | None
    settles_on: date
    purpose: OrderPurpose
    campaign_id: str
    state: LedgerState
    schedule: AssumedFlatFeeSchedule

    def __post_init__(self) -> None:
        """Refuse a closing purpose, a held account and dates out of session order."""
        require_id(self.decision_id, "SelectionContext.decision_id")
        if not isinstance(self.session, TradingSession):
            raise TypeError(f"SelectionContext.session must be a TradingSession: {self.session}")
        if not isinstance(self.purpose, OrderPurpose) or not self.purpose.opening:
            raise ValueError(f"SelectionContext.purpose must be opening, got {self.purpose!r}")
        today = self.session.session_date
        if type(self.settles_on) is not date:
            raise TypeError(f"SelectionContext.settles_on must be a date: {self.settles_on!r}")
        if self.settles_on <= today:
            raise ValueError(f"SelectionContext.settles_on {self.settles_on} is not after {today}")
        if self.prior_session is not None and not self.prior_session < today:
            raise ValueError(f"SelectionContext.prior_session {self.prior_session} >= {today}")
        if _GENERATION_ID.fullmatch(self.campaign_id) is None:
            raise ValueError(
                f"SelectionContext.campaign_id must be c{{n}}.g{{k}}: {self.campaign_id}"
            )
        if not isinstance(self.state, LedgerState):
            raise TypeError("SelectionContext.state must be a LedgerState")
        held = sorted(cid for cid in self.state.lots if cid in self.state.contracts)
        if held:
            raise ValueError(f"a selection needs a flat account; option lots held: {held}")
        if not isinstance(self.schedule, AssumedFlatFeeSchedule):
            raise TypeError("SelectionContext.schedule must be an AssumedFlatFeeSchedule")


@dataclass(frozen=True, slots=True)
class SelectionOutcome:
    """An order, or none, with the full record of the decision.

    Attributes:
        order: The order to submit; None when no package was chosen.
        decision: Candidates, verdicts and the reason when there is no order.

    """

    order: Order | None
    decision: CandidateDecision


# --- private working values -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Expiry:
    """One step-2-eligible (root, expiry) with its tradable contracts, sorted by id."""

    root: str
    rules: ProductRules
    expiry: date
    dte: int
    expiry_error: int
    expires_at_ns: int
    contracts: tuple[ContractVersion, ...]


@dataclass(frozen=True, slots=True)
class _Inputs:
    """An expiry's pricing inputs; None where unavailable."""

    spot: Price | None
    df: float | None
    forward: float | None


@dataclass(frozen=True, slots=True)
class _Gate:
    """A contract's quote after the quote filters (step 4, before the selector error)."""

    observation: QuoteObservation | None
    quote: Quote | None
    spread: Usd | None
    rejection: CandidateRejection | None


@dataclass(frozen=True, slots=True)
class _Candidate:
    """An eligible leg candidate: its record, order leg and the facts the search needs."""

    record: LegCandidate
    leg: OrderLeg
    strike: Decimal
    quote: Quote
    size: int
    error: Decimal
    spread: Usd


@dataclass(frozen=True, slots=True)
class _LegPlan:
    """One leg's candidates; an offset leg takes the one at its anchor's strike plus offset."""

    records: tuple[LegCandidate, ...]
    eligible: tuple[_Candidate, ...]
    anchor: int | None
    offset: Decimal | None
    by_strike: Mapping[Decimal, _Candidate]


@dataclass(frozen=True, slots=True)
class _Shape:
    """The strategy facts every package is judged against."""

    structure: str
    roles: tuple[tuple[str, str], ...]
    direction: int


@dataclass(frozen=True, slots=True)
class _Sizer:
    """Step-6 inputs: trial sizes in order and the account limits of this decision."""

    trials: tuple[int, ...]
    cap: int | None
    participation: Fraction
    risk_limit: Decimal
    provision_rate: Usd
    at_ns: int
    ctx: SelectionContext


@dataclass(frozen=True, slots=True)
class _Sized:
    """A size that fits, with its whole-order debit at the decision naturals."""

    packages: int
    debit: Usd


@dataclass(slots=True)
class _Search:
    """Evaluated packages and the best eligible one of one ``select`` call."""

    limit: int
    packages: list[PackageCandidate] = field(default_factory=list)
    best: int | None = None
    best_legs: tuple[OrderLeg, ...] = ()
    best_size: _Sized | None = None
    exceeded: bool = False

    def best_error(self) -> int | None:
        """Return the best eligible package's expiry error; None before one is found."""
        return None if self.best is None else self.packages[self.best].score.expiry_error

    def record(
        self, package: PackageCandidate, legs: tuple[OrderLeg, ...], sized: _Sized | None
    ) -> None:
        """Append one evaluation, keep the minimum score, stop at the evaluation budget."""
        self.packages.append(package)
        best = None if self.best is None else self.packages[self.best].score
        if sized is not None and (best is None or package.score < best):
            self.best, self.best_legs, self.best_size = len(self.packages) - 1, legs, sized
        if len(self.packages) >= self.limit:
            self.exceeded = True


# --- step 1: conditions ---------------------------------------------------------------------------


def evaluate_conditions(
    strategy: ValidatedStrategy, view: AsOfView, prior_session: date | None
) -> tuple[ConditionCheck, ...]:
    """Judge every entry condition on the prior session's feature (ADR 0002 §8 step 1).

    Each reads ``view.feature(feature_id(product.underlying_symbol, condition.feature),
    prior_session)``; ``gt``/``gte``/``lt``/``lte`` compare exact ``Decimal``s. No prior
    session, an invisible observation or a None value is unknown, and unknown is false.

    Args:
        strategy: The strategy.
        view: As-of view at DEC.
        prior_session: Previous table session, or None.

    Returns:
        One check per condition, in spec order; () when there are none (which is true).

    Raises:
        TypeError: If an argument has the wrong type.

    """
    if not isinstance(strategy, ValidatedStrategy) or not isinstance(view, AsOfView):
        raise TypeError("evaluate_conditions needs a ValidatedStrategy and an AsOfView")
    if prior_session is not None and type(prior_session) is not date:
        raise TypeError(f"prior_session must be a date or None, got {prior_session!r}")
    underlying = strategy.spec.product.underlying_symbol
    return tuple(
        _condition(underlying, condition, view, prior_session)
        for condition in strategy.spec.entry.all_conditions
    )


def _condition(
    underlying: str, condition: Condition, view: AsOfView, prior_session: date | None
) -> ConditionCheck:
    """Judge one condition; an unknown feature value is false."""
    fid = feature_id(underlying, condition.feature)
    observation = None if prior_session is None else view.feature(fid, prior_session)
    value = None if observation is None else observation.value
    holds = value is not None and _compare(condition.operator, value, condition.value)
    return ConditionCheck(fid, condition.operator, condition.value, prior_session, value, holds)


def _compare(operator: str, value: Decimal, threshold: Decimal) -> bool:
    """Return ``value <operator> threshold`` for gt, gte, lt or lte."""
    match operator:
        case "gt":
            return value > threshold
        case "gte":
            return value >= threshold
        case "lt":
            return value < threshold
        case "lte":
            return value <= threshold
    raise ValueError(f"unknown condition operator {operator!r}")


# --- select ---------------------------------------------------------------------------------------


def select(strategy: ValidatedStrategy, view: AsOfView, ctx: SelectionContext) -> SelectionOutcome:
    """Select, size and price one package, or explain why none (ADR 0002 §8).

    1. Conditions: ``evaluate_conditions``; any false gives reason CONDITIONS_FALSE, no search.
    2. Expiries: per allowed root with ``product_rules(root).family == product.family``, each
       expiry date of ``view.listed(root)`` whose contracts are EUROPEAN, CASH and of the rules'
       settlement series, with ``min_dte <= dte <= max_dte``, ``dte > exit_dte``, ``dte >
       trigger_dte`` when ``roll.mode`` is sequential, and ``last_tradable_at_ns >= f3``;
       sorted by ``(|dte - target_dte|, expires_at_ns, root)``.
    3. Inputs per expiry: spot ``view.index_value(underlying_id, max_age_ns=SPOT_MAX_AGE_NS)``;
       DF ``view.curve(UST_CMT).df((expires_at_ns - at_ns)/86_400e9)``; F ``parity_forward``
       over the strikes whose call and put quotes (``QUOTE_MAX_AGE_NS``) are both VALID. Any
       missing: skip PRICING_INPUT_UNAVAILABLE.
    4. Leg candidates, per leg in ``leg_order`` (every leg shares the ``target_dte`` leg's
       expiry): contracts of the leg's type, rejected, in order, for no quote within
       ``QUOTE_MAX_AGE_NS`` (QUOTE_UNAVAILABLE), a status the side cannot use
       (``usable_quote``; QUOTE_STATUS), NO_BID under ``require_positive_bid_for_entry``
       (NO_BID_FOR_ENTRY), ``ask - bid`` above both ``max_absolute_spread_price_units`` and
       ``max_relative_spread · mid`` (SPREAD, exact), cumulative volume absent or below a
       positive ``min_cumulative_volume`` (VOLUME), no IV for a delta leg (PRICING_INPUT) and
       an error above ``tolerance`` (OUT_OF_TOLERANCE). Errors, exact ``Decimal``: delta
       ``|Decimal(spot_delta(F, S, K, t, DF, iv, right)) - target_delta|`` with ``iv`` from the
       mid and ``t`` ACT/365F from DEC; moneyness ``|Decimal(float(K)/float(S)) -
       target_strike_to_spot|``. A ``strike_offset`` leg's one candidate is the contract at the
       anchor's strike plus the offset, error 0 (NO_OFFSET_STRIKE if absent), under the same
       quote filters. Sorted by ``(error, spread_usd, strike, contract_id)``.
    5. Search: depth-first in ``leg_order``; each complete package is one evaluation, judged
       in order DUPLICATE_CONTRACT, DELIVERABLE_MISMATCH (the legs' versions deliver different
       deliverables), STRIKE_ORDER (condor lp < sp < sc < lc, strangle put < call, straddle
       equal), PREMIUM_DIRECTION (``Q·D(1) > 0`` at decision naturals), NO_SIZE
       (step 6), else ELIGIBLE with score ``(expiry_error, Σerror, Σspread_usd, ids)``; the
       minimum is CHOSEN. An expiry whose ``expiry_error`` exceeds the best score's, and every
       later one, is PRUNED before its inputs are read (spot, DF, F None; no leg candidates).
       Reaching ``MAX_PACKAGE_EVALUATIONS`` stops with no order, reason
       SELECTION_BUDGET_EXCEEDED. No eligible package: NO_PACKAGE.
    6. Sizing at decision naturals: ``n`` fits when ``-expiry_bounds(((terms_i, ratio_i·n)),
       0, -D(n) - fees(n)).min_value + Σ|ratio_i|·n·max(trade, cash_settlement rate) <=
       max_campaign_risk_fraction · mid NLV`` (exact products; mid NLV of the flat state is
       CASH + ΣRECEIVABLE + ΣPAYABLE), the ``book_option_trade`` preview at DEC keeps
       ``funding_headroom >= 0``, and ``n <= floor(min_i(side size_i/|ratio_i|) ·
       participation_fraction)`` at the DEC quotes. ``fixed_contracts``: ``contracts`` or
       nothing; ``risk_budget``: the largest fitting ``n`` from ``min(max_contracts,
       max_contracts_per_order)`` down to 1. The order's limit is ``D(n) +
       price_allowance_usd``.

    Args:
        strategy: The strategy.
        view: As-of view at the session's DEC.
        ctx: Decision inputs.

    Returns:
        The order (legs in ``leg_order``, ratio +1 buy / -1 sell, trigger None, submitted at
        DEC) or None, and the decision record.

    Raises:
        TypeError: If an argument has the wrong type.
        ValueError: If the view is not at ``ctx.session``'s DEC or the ledger is later than it.
        SimulationInvariantError: If the ledger cannot book a sized package's preview (a
            contract traded before its ``TermsRevision``, ADR 0002 §17 item 50).

    """
    _check_call(strategy, view, ctx)
    conditions = evaluate_conditions(strategy, view, ctx.prior_session)
    search = _Search(MAX_PACKAGE_EVALUATIONS)
    sizer = _sizer(strategy.spec, view, ctx)
    expiries: tuple[ExpiryCandidate, ...] = ()
    if all(check.holds for check in conditions):
        expiries = _search_expiries(strategy, view, sizer, search)
    return _outcome(
        view,
        ctx,
        spec=strategy.spec,
        sizer=sizer,
        conditions=conditions,
        expiries=expiries,
        search=search,
    )


def _check_call(strategy: ValidatedStrategy, view: AsOfView, ctx: SelectionContext) -> None:
    """Refuse arguments of the wrong type, a view not at ``ctx``'s DEC, a later ledger."""
    if not isinstance(strategy, ValidatedStrategy):
        raise TypeError(f"select needs a ValidatedStrategy, got {type(strategy).__name__}")
    if not isinstance(view, AsOfView) or not isinstance(ctx, SelectionContext):
        raise TypeError("select needs an AsOfView and a SelectionContext")
    if view.session != ctx.session:
        raise ValueError(f"the view's session is not the context session {ctx.session}")
    if view.at_ns != slot_times(ctx.session).dec:
        raise ValueError(f"select runs at the session's DEC, not at {view.at_ns}")
    if ctx.state.last_at_ns > view.at_ns:
        raise ValueError("the ledger state is later than the decision")


def _outcome(  # noqa: PLR0913 — the decision's parts, assembled once
    view: AsOfView,
    ctx: SelectionContext,
    *,
    spec: StrategySpec,
    sizer: _Sizer,
    conditions: tuple[ConditionCheck, ...],
    expiries: tuple[ExpiryCandidate, ...],
    search: _Search,
) -> SelectionOutcome:
    """Mark the chosen package and build the order and the decision record."""
    hold = all(check.holds for check in conditions)
    chosen = None if search.exceeded else search.best
    packages = list(search.packages)
    order = None
    if chosen is not None and search.best_size is not None:
        packages[chosen] = replace(packages[chosen], verdict=PackageVerdict.CHOSEN)
        order = _order(view, ctx, spec, search.best_legs, search.best_size)
    reason = _reason(hold, search.exceeded, chosen)
    decision = CandidateDecision(
        decision_id=ctx.decision_id,
        session_date=ctx.session.session_date,
        at_ns=view.at_ns,
        purpose=ctx.purpose,
        campaign_id=ctx.campaign_id,
        conditions=conditions,
        conditions_hold=hold,
        expiries=expiries,
        packages=tuple(packages),
        chosen=chosen,
        cap_bound=order is not None and order.packages == sizer.cap,
        evaluations=len(packages),
        budget_exceeded=search.exceeded,
        reason=reason,
    )
    return SelectionOutcome(order, decision)


def _reason(hold: bool, exceeded: bool, chosen: int | None) -> DecisionReason | None:
    """Return why no order: failed conditions, the budget, or no eligible package."""
    if not hold:
        return DecisionReason.CONDITIONS_FALSE
    if exceeded:
        return DecisionReason.SELECTION_BUDGET_EXCEEDED
    return DecisionReason.NO_PACKAGE if chosen is None else None


def _order(
    view: AsOfView,
    ctx: SelectionContext,
    spec: StrategySpec,
    legs: tuple[OrderLeg, ...],
    sized: _Sized,
) -> Order:
    """Return the opening order: limit ``D(n) + price_allowance_usd``, submitted at DEC."""
    day = ctx.session.session_date
    return Order(
        order_id=order_id(day, ctx.purpose),
        campaign_id=ctx.campaign_id,
        purpose=ctx.purpose,
        legs=legs,
        packages=sized.packages,
        limit_usd=sized.debit + spec.execution.price_allowance_usd,
        trigger=None,
        submitted_at_ns=view.at_ns,
        session_date=day,
    )


# --- step 2: expiries -----------------------------------------------------------------------------


def _eligible_expiries(spec: StrategySpec, view: AsOfView) -> list[_Expiry]:
    """Return the step-2 expiries in search order."""
    window = _target_window(spec)
    floor_dte = _dte_floor(spec)
    session = view.session
    f3 = slot_times(session).f3
    groups: dict[tuple[str, date], list[ContractVersion]] = {}
    for root in spec.product.allowed_option_roots:
        rules = product_rules(root)
        if rules.family != spec.product.family:
            continue
        for version in _tradable(view.listed(root), rules, f3):
            groups.setdefault((root, _expiry_date(version)), []).append(version)
    expiries = [
        _expiry(root, day, versions, session.session_date, window.target_dte)
        for (root, day), versions in groups.items()
    ]
    kept = [
        expiry
        for expiry in expiries
        if window.min_dte <= expiry.dte <= window.max_dte and expiry.dte > floor_dte
    ]
    return sorted(kept, key=lambda expiry: (expiry.expiry_error, expiry.expires_at_ns, expiry.root))


def _target_window(spec: StrategySpec) -> TargetDteExpiry:
    """Return the one ``target_dte`` expiry selection (``check_strategy`` guarantees one)."""
    windows = [
        leg.expiry_selection
        for leg in spec.legs
        if isinstance(leg.expiry_selection, TargetDteExpiry)
    ]
    if len(windows) != 1:
        raise ValueError(f"a strategy needs exactly one target_dte leg, got {len(windows)}")
    return windows[0]


def _dte_floor(spec: StrategySpec) -> int:
    """Return the dte an expiry must exceed: ``exit_dte`` and, when rolling, ``trigger_dte``."""
    roll = spec.roll
    if isinstance(roll, SequentialRoll):
        return max(spec.exits.exit_dte, roll.trigger_dte)
    return spec.exits.exit_dte


def _tradable(
    versions: Iterable[ContractVersion], rules: ProductRules, f3: int
) -> tuple[ContractVersion, ...]:
    """Return the European cash contracts of the rules' series still tradable at F3."""
    return tuple(
        version
        for version in versions
        if version.terms.exercise_style is ExerciseStyle.EUROPEAN
        and version.terms.settlement_type is SettlementType.CASH
        and version.settlement_series == rules.settlement_series
        and version.last_tradable_at_ns >= f3
    )


def _expiry_date(version: ContractVersion) -> date:
    """Return the expiry date spelled in the contract id ``{root}:{YYYY-MM-DD}:{C|P}:{K}``."""
    return date.fromisoformat(version.terms.contract_id.split(":")[1])


def _expiry(
    root: str, day: date, versions: list[ContractVersion], session_date: date, target: int
) -> _Expiry:
    """Return one (root, expiry) group; its contracts must share one expiration instant."""
    instants = {version.terms.expires_at_ns for version in versions}
    if len(instants) != 1:
        raise ValueError(f"{root} {day} contracts expire at several instants: {sorted(instants)}")
    days = dte(session_date, day)
    ordered = tuple(sorted(versions, key=lambda version: version.terms.contract_id))
    return _Expiry(
        root, product_rules(root), day, days, abs(days - target), instants.pop(), ordered
    )


# --- steps 3-5: inputs, candidates, search --------------------------------------------------------


def _search_expiries(
    strategy: ValidatedStrategy, view: AsOfView, sizer: _Sizer, search: _Search
) -> tuple[ExpiryCandidate, ...]:
    """Search the expiries in order; prune after a better score, stop at the budget."""
    records: list[ExpiryCandidate] = []
    for expiry in _eligible_expiries(strategy.spec, view):
        if search.exceeded:
            break  # the budget stopped the search: later expiries were never considered
        best_error = search.best_error()
        if best_error is not None and expiry.expiry_error > best_error:
            records.append(_expiry_record(expiry, _Inputs(None, None, None), ExpirySkip.PRUNED))
            continue
        records.append(_search_expiry(strategy, view, sizer, expiry, search))
    return tuple(records)


def _shape(strategy: ValidatedStrategy) -> _Shape:
    """Return the structure, the (side, type) role per ``leg_order`` leg and ``Q``."""
    legs = {leg.leg_id: leg for leg in strategy.spec.legs}
    roles = tuple((legs[i].side, legs[i].option_type) for i in strategy.leg_order)
    direction = 1 if strategy.premium_direction is PremiumDirection.DEBIT else -1
    return _Shape(strategy.spec.structure, roles, direction)


def _search_expiry(
    strategy: ValidatedStrategy,
    view: AsOfView,
    sizer: _Sizer,
    expiry: _Expiry,
    search: _Search,
) -> ExpiryCandidate:
    """Read the inputs, judge the leg candidates and evaluate this expiry's packages."""
    shape = _shape(strategy)
    inputs = _pricing_inputs(view, expiry)
    if inputs.spot is None or inputs.df is None or inputs.forward is None:
        return _expiry_record(expiry, inputs, ExpirySkip.PRICING_INPUT_UNAVAILABLE)
    plans = _leg_plans(strategy, view, expiry, inputs)
    for package in _packages(plans, ()):
        _evaluate(shape, sizer, expiry, package, search)
        if search.exceeded:
            break
    records = tuple(record for plan in plans for record in plan.records)
    return _expiry_record(expiry, inputs, None, records)


def _expiry_record(
    expiry: _Expiry,
    inputs: _Inputs,
    skip: ExpirySkip | None,
    legs: tuple[LegCandidate, ...] = (),
) -> ExpiryCandidate:
    """Return the expiry's record; floats are lifted with ``Decimal(float)``."""
    return ExpiryCandidate(
        root=expiry.root,
        expiry=expiry.expiry,
        dte=expiry.dte,
        expiry_error=expiry.expiry_error,
        spot=inputs.spot,
        discount_factor=None if inputs.df is None else Decimal(inputs.df),
        forward=None if inputs.forward is None else Decimal(inputs.forward),
        skip_reason=skip,
        legs=legs,
    )


def _pricing_inputs(view: AsOfView, expiry: _Expiry) -> _Inputs:
    """Return spot, DF and parity forward, each read independently; F needs spot and DF."""
    observation = view.index_value(expiry.rules.underlying_id, max_age_ns=SPOT_MAX_AGE_NS)
    spot = None if observation is None else observation.value
    curve = view.curve(CMT_CURVE_ID)
    t_days = (expiry.expires_at_ns - view.at_ns) / _NS_PER_DAY
    df = None if curve is None else curve.df(t_days)
    if spot is None or df is None:
        return _Inputs(spot, df, None)
    pairs = _parity_pairs(view, expiry.contracts)
    return _Inputs(spot, df, parity_forward(pairs, df, float(spot.value)).value)


def _parity_pairs(view: AsOfView, contracts: Iterable[ContractVersion]) -> list[ParityPair]:
    """Return a pair per strike whose call and put quotes are both VALID and fresh."""
    mids: dict[tuple[Decimal, OptionType], float] = {}
    for version in contracts:
        row = view.quote(version.terms.contract_id, max_age_ns=QUOTE_MAX_AGE_NS)
        if row is None or row.status() is not QuoteStatus.VALID:
            continue
        mids[(version.terms.strike.value, version.terms.option_type)] = _mid(row.bid, row.ask)
    strikes = sorted({strike for strike, _ in mids})
    return [
        ParityPair(float(strike), mids[(strike, OptionType.CALL)], mids[(strike, OptionType.PUT)])
        for strike in strikes
        if (strike, OptionType.CALL) in mids and (strike, OptionType.PUT) in mids
    ]


def _mid(bid: Decimal, ask: Decimal) -> float:
    """Return a quote mid in float64: the exact sum, halved."""
    with localcontext(EXACT):
        total = bid + ask
    return float(total) / 2


def _leg_plans(
    strategy: ValidatedStrategy, view: AsOfView, expiry: _Expiry, inputs: _Inputs
) -> tuple[_LegPlan, ...]:
    """Judge every leg's candidates in ``leg_order``; offset legs follow their anchors."""
    order = strategy.leg_order
    if not 0 < len(order) <= _MAX_LEGS:
        raise ValueError(f"a package has 1 to {_MAX_LEGS} legs, got {len(order)}")
    legs = {leg.leg_id: leg for leg in strategy.spec.legs}
    liquidity = strategy.spec.liquidity
    plans: list[_LegPlan] = []
    for leg_id in order:
        leg = legs[leg_id]
        selection = leg.strike_selection
        if isinstance(selection, StrikeOffset):
            anchor = order.index(selection.anchor_leg_id)
            anchored = (anchor, plans[anchor])
            offset = _offset_plan(
                leg, selection, anchored, view=view, liquidity=liquidity, expiry=expiry
            )
            plans.append(offset)
            continue
        judged = [
            _judge(leg, version, view, liquidity=liquidity, expiry=expiry, inputs=inputs)
            for version in expiry.contracts
            if version.terms.option_type.value == leg.option_type
        ]
        plans.append(_plan(judged, None, None))
    return tuple(plans)


def _offset_plan(  # noqa: PLR0913 — the offset leg, its anchor and where to look
    leg: Leg,
    selection: StrikeOffset,
    anchor: tuple[int, _LegPlan],
    *,
    view: AsOfView,
    liquidity: Liquidity,
    expiry: _Expiry,
) -> _LegPlan:
    """Judge the contracts at each eligible anchor strike plus the offset; never rounded."""
    anchor_index, anchor_plan = anchor
    right = OptionType(leg.option_type)
    listed = {v.terms.strike.value: v for v in expiry.contracts if v.terms.option_type is right}
    with localcontext(EXACT):
        wanted = {c.strike + selection.offset_price_units for c in anchor_plan.eligible}
    judged: list[tuple[LegCandidate, _Candidate | None]] = []
    # A negative strike cannot be listed or spelled as a contract id: no candidate at all.
    for strike in sorted(strike for strike in wanted if strike >= 0):
        version = listed.get(strike)
        if version is None:
            judged.append((_missing_offset(leg, expiry, right, strike), None))
            continue
        judged.append(_judge(leg, version, view, liquidity=liquidity, expiry=expiry, inputs=None))
    return _plan(judged, anchor_index, selection.offset_price_units)


def _missing_offset(leg: Leg, expiry: _Expiry, right: OptionType, strike: Decimal) -> LegCandidate:
    """Return the NO_OFFSET_STRIKE record of an offset strike that is not listed."""
    missing = contract_id(expiry.root, expiry.expiry, right, Price(strike))
    return LegCandidate(
        leg_id=leg.leg_id,
        contract_id=missing,
        quote_id=None,
        implied_vol=None,
        spot_delta=None,
        error=None,
        spread_usd=None,
        rejection=CandidateRejection.NO_OFFSET_STRIKE,
    )


def _plan(
    judged: Sequence[tuple[LegCandidate, _Candidate | None]],
    anchor: int | None,
    offset: Decimal | None,
) -> _LegPlan:
    """Order a leg's candidates: eligible by (error, spread, strike, id), then rejected by id."""
    eligible = sorted(
        (candidate for _, candidate in judged if candidate is not None),
        key=lambda c: (c.error, c.spread, c.strike, c.record.contract_id),
    )
    rejected = sorted(
        (record for record, candidate in judged if candidate is None),
        key=lambda record: record.contract_id,
    )
    records = (*(candidate.record for candidate in eligible), *rejected)
    by_strike = MappingProxyType({candidate.strike: candidate for candidate in eligible})
    return _LegPlan(records, tuple(eligible), anchor, offset, by_strike)


def _signed_ratio(leg: Leg) -> int:
    """Return the leg's signed ratio: + for a buy, - for a sell."""
    return leg.ratio if leg.side == "buy" else -leg.ratio


def _judge(  # noqa: PLR0913 — one contract judged for one leg, inputs explicit
    leg: Leg,
    version: ContractVersion,
    view: AsOfView,
    *,
    liquidity: Liquidity,
    expiry: _Expiry,
    inputs: _Inputs | None,
) -> tuple[LegCandidate, _Candidate | None]:
    """Judge one contract for one leg: quote filters, then the selector error and tolerance."""
    terms, ratio = version.terms, _signed_ratio(leg)
    gate = _quote_gate(terms, ratio, view, liquidity)
    quote_id = None if gate.observation is None else gate.observation.observation_id
    if gate.rejection is not None or gate.observation is None or gate.quote is None:
        record = LegCandidate(
            leg.leg_id, terms.contract_id, quote_id, None, None, None, gate.spread, gate.rejection
        )
        return record, None
    vol, delta, error, rejection = _selector_error(
        leg.strike_selection, terms, gate.quote, (expiry, inputs), view.at_ns
    )
    record = LegCandidate(
        leg.leg_id, terms.contract_id, quote_id, vol, delta, error, gate.spread, rejection
    )
    if rejection is not None or error is None or gate.spread is None:
        return record, None
    size = gate.observation.ask_size if ratio > 0 else gate.observation.bid_size
    order_leg = OrderLeg(terms, version.version_id, ratio)
    candidate = _Candidate(
        record, order_leg, terms.strike.value, gate.quote, size, error, gate.spread
    )
    return record, candidate


def _quote_gate(terms: ContractTerms, ratio: int, view: AsOfView, liquidity: Liquidity) -> _Gate:
    """Apply the quote filters in order: freshness, status, entry bid, spread, volume."""
    observation = view.quote(terms.contract_id, max_age_ns=QUOTE_MAX_AGE_NS)
    if observation is None:
        return _Gate(None, None, None, CandidateRejection.QUOTE_UNAVAILABLE)
    quote = usable_quote(observation, ratio)
    if quote is None:
        return _Gate(observation, None, None, CandidateRejection.QUOTE_STATUS)
    with localcontext(EXACT):
        width = quote.ask.value - quote.bid.value
        relative = liquidity.max_relative_spread * (quote.bid.value + quote.ask.value) / 2
    spread = terms.premium_usd(Price(width), 1)
    if liquidity.require_positive_bid_for_entry and observation.status() is QuoteStatus.NO_BID:
        return _Gate(observation, quote, spread, CandidateRejection.NO_BID_FOR_ENTRY)
    if width > liquidity.max_absolute_spread_price_units.value and width > relative:
        return _Gate(observation, quote, spread, CandidateRejection.SPREAD)  # both gates fail
    if not _volume_ok(view, terms.contract_id, liquidity.min_cumulative_volume):
        return _Gate(observation, quote, spread, CandidateRejection.VOLUME)
    return _Gate(observation, quote, spread, None)


def _volume_ok(view: AsOfView, contract: str, minimum: int) -> bool:
    """Return whether a positive minimum is met by the session's cumulative volume."""
    if minimum <= 0:
        return True
    activity = view.activity(contract)
    return activity is not None and activity.cumulative_volume >= minimum


def _selector_error(
    selection: MoneynessStrike | DeltaStrike | StrikeOffset,
    terms: ContractTerms,
    quote: Quote,
    where: tuple[_Expiry, _Inputs | None],
    at_ns: int,
) -> tuple[Decimal | None, Decimal | None, Decimal | None, CandidateRejection | None]:
    """Return (IV, delta, error, rejection); offsets are exact, error 0."""
    if isinstance(selection, StrikeOffset):
        return None, None, Decimal(0), None
    expiry, inputs = where
    if inputs is None or inputs.spot is None:
        raise ValueError("a moneyness or delta leg needs the expiry's pricing inputs")
    if isinstance(selection, MoneynessStrike):
        ratio = float(terms.strike.value) / float(inputs.spot.value)
        error = _lifted_error(ratio, selection.target_strike_to_spot)
        return None, None, error, _tolerance(error, selection.tolerance)
    return _delta_error(selection, terms, quote, expiry=expiry, inputs=inputs, at_ns=at_ns)


def _delta_error(  # noqa: PLR0913 — the delta selector's inputs, explicit
    selection: DeltaStrike,
    terms: ContractTerms,
    quote: Quote,
    *,
    expiry: _Expiry,
    inputs: _Inputs,
    at_ns: int,
) -> tuple[Decimal | None, Decimal | None, Decimal | None, CandidateRejection | None]:
    """Return the delta leg's IV from the mid, its spot delta and ``|delta - target|``."""
    if inputs.spot is None or inputs.df is None or inputs.forward is None:
        raise ValueError("a delta leg needs spot, discount factor and forward")
    t = (expiry.expires_at_ns - at_ns) / _NS_PER_YEAR
    strike = float(terms.strike.value)
    right = terms.option_type
    mid = _mid(quote.bid.value, quote.ask.value)
    vol = implied_vol(mid, inputs.forward, strike, t, inputs.df, right).value
    if vol is None:
        return None, None, None, CandidateRejection.PRICING_INPUT
    spot = float(inputs.spot.value)
    delta = spot_delta(inputs.forward, spot, strike, t, inputs.df, vol, right)
    error = _lifted_error(delta, selection.target_delta)
    return Decimal(vol), Decimal(delta), error, _tolerance(error, selection.tolerance)


def _lifted_error(value: float, target: Decimal) -> Decimal:
    """Return ``|Decimal(value) - target|`` exactly (§17 items 37, 46)."""
    with localcontext(_WIDE):
        return abs(Decimal(value) - target)


def _tolerance(error: Decimal, tolerance: Decimal) -> CandidateRejection | None:
    """Return OUT_OF_TOLERANCE when ``error > tolerance``; equality passes."""
    return None if error <= tolerance else CandidateRejection.OUT_OF_TOLERANCE


def _packages(
    plans: tuple[_LegPlan, ...], chosen: tuple[_Candidate, ...]
) -> Iterator[tuple[_Candidate, ...]]:
    """Yield complete packages depth-first in ``leg_order``; depth <= ``_MAX_LEGS``."""
    if len(chosen) == len(plans):
        yield chosen
        return
    for candidate in _options(plans[len(chosen)], chosen):
        yield from _packages(plans, (*chosen, candidate))


def _options(plan: _LegPlan, chosen: tuple[_Candidate, ...]) -> tuple[_Candidate, ...]:
    """Return a leg's choices given the legs chosen so far: an offset leg has at most one."""
    if plan.anchor is None or plan.offset is None:
        return plan.eligible
    with localcontext(EXACT):
        strike = chosen[plan.anchor].strike + plan.offset
    found = plan.by_strike.get(strike)
    return () if found is None else (found,)


def _evaluate(
    shape: _Shape,
    sizer: _Sizer,
    expiry: _Expiry,
    package: tuple[_Candidate, ...],
    search: _Search,
) -> None:
    """Judge and score one complete package and record it."""
    legs = tuple(candidate.leg for candidate in package)
    ids = tuple(leg.terms.contract_id for leg in legs)
    quotes = {candidate.leg.terms.contract_id: candidate.quote for candidate in package}
    debit = package_debit(legs, 1, quotes)
    with localcontext(_WIDE):
        error_sum = sum((candidate.error for candidate in package), Decimal(0))
    spread_sum = sum((candidate.spread for candidate in package), start=ZERO_USD)
    score = PackageScore(expiry.expiry_error, error_sum, spread_sum, ids)
    verdict, sized = _verdict(shape, sizer, package, debit)
    size = 0 if sized is None else sized.packages
    record = PackageCandidate(expiry.root, expiry.expiry, ids, debit, size, score, verdict)
    search.record(record, legs, sized)


def _verdict(
    shape: _Shape, sizer: _Sizer, package: tuple[_Candidate, ...], debit: Usd
) -> tuple[PackageVerdict, _Sized | None]:
    """Return the first failed check, or ELIGIBLE with the size found."""
    ids = [candidate.leg.terms.contract_id for candidate in package]
    if len(set(ids)) != len(ids):
        return PackageVerdict.DUPLICATE_CONTRACT, None
    if len({candidate.leg.terms.deliverable for candidate in package}) != 1:
        return PackageVerdict.DELIVERABLE_MISMATCH, None
    if not _strike_order_ok(shape, tuple(candidate.strike for candidate in package)):
        return PackageVerdict.STRIKE_ORDER, None
    if shape.direction * debit.amount <= 0:
        return PackageVerdict.PREMIUM_DIRECTION, None
    sized = _size(sizer, package)
    if sized is None:
        return PackageVerdict.NO_SIZE, None
    return PackageVerdict.ELIGIBLE, sized


def _strike_order_ok(shape: _Shape, strikes: tuple[Decimal, ...]) -> bool:
    """Return whether the strikes keep the structure's order (straddle: all equal).

    Raises:
        SimulationInvariantError: If the structure has no strike-order rule here.

    """
    if shape.structure == "long_straddle":
        return len(set(strikes)) == 1
    roles = _ASCENDING_ROLES.get(shape.structure)
    if roles is None:
        raise SimulationInvariantError(
            f"the selector has no strike-order rule for structure {shape.structure!r}"
        )
    ordered = [strikes[shape.roles.index(role)] for role in roles]
    return all(low < high for low, high in pairwise(ordered))


# --- step 6: sizing -------------------------------------------------------------------------------


def _sizer(spec: StrategySpec, view: AsOfView, ctx: SelectionContext) -> _Sizer:
    """Return the trial sizes and the account limits of this decision."""
    sizing, execution = spec.sizing, spec.execution
    cap: int | None = None
    trials: tuple[int, ...]
    if isinstance(sizing, RiskBudget):
        cap = min(sizing.max_contracts, execution.max_contracts_per_order)
        trials = tuple(range(cap, 0, -1))
    else:
        trials = (sizing.contracts,)
    balances = ctx.state.balances
    cash = (amount for key, amount in balances.items() if key.kind in CASH_KINDS)
    mid_nlv = sum(cash, start=ZERO_USD)
    with localcontext(EXACT):
        risk_limit = spec.account.max_campaign_risk_fraction * mid_nlv.amount
    schedule = ctx.schedule
    return _Sizer(
        trials=trials,
        cap=cap,
        participation=Fraction(execution.participation_fraction),
        risk_limit=risk_limit,
        provision_rate=max(schedule.trade_per_contract, schedule.cash_settlement_per_contract),
        at_ns=view.at_ns,
        ctx=ctx,
    )


def _size(sizer: _Sizer, package: tuple[_Candidate, ...]) -> _Sized | None:
    """Return the first trial size within the decision capacity that passes the risk test."""
    per_package = min(Fraction(c.size, abs(c.leg.ratio)) for c in package)
    capacity = floor(per_package * sizer.participation)
    legs = tuple(candidate.leg for candidate in package)
    quotes = {candidate.leg.terms.contract_id: candidate.quote for candidate in package}
    for packages in sizer.trials:
        if packages > capacity:
            continue
        sized = _fits(sizer, legs, quotes, packages)
        if sized is not None:
            return sized
    return None


def _fits(
    sizer: _Sizer, legs: tuple[OrderLeg, ...], quotes: Mapping[str, Quote], packages: int
) -> _Sized | None:
    """Return the size if its risk fits the budget and its preview keeps headroom >= 0.

    Raises:
        SimulationInvariantError: If the ledger cannot book the package: a contract id the
            ledger registered with other terms (traded before a ``TermsRevision``; the WP1
            ledger keys contracts by id), ADR 0002 §17 item 50.

    """
    ctx = sizer.ctx
    debit = package_debit(legs, packages, quotes)
    fills = tuple(
        LegFill(
            leg.terms, leg.ratio * packages, natural_price(quotes[leg.terms.contract_id], leg.ratio)
        )
        for leg in legs
    )
    fees = trade_fees(ctx.schedule, fills)
    entry_cash = -debit - sum((line.amount for line in fees), start=ZERO_USD)
    holdings = tuple((leg.terms, leg.ratio * packages) for leg in legs)
    contracts = sum(abs(leg.ratio) * packages for leg in legs)
    try:
        bounds = expiry_bounds(holdings, 0, entry_cash)
        if bounds.min_value is None:
            return None
        risk = -bounds.min_value + sizer.provision_rate.scaled_by(contracts)
        if risk.amount > sizer.risk_limit:
            return None
        entry = book_option_trade(
            ctx.state,
            event_id=ctx.decision_id,
            at_ns=sizer.at_ns,
            campaign_id=ctx.campaign_id,
            legs=fills,
            fees=fees,
            settles_on=ctx.settles_on,
        )
        headroom = funding_headroom(apply_entry(ctx.state, entry), ctx.schedule)
    except (LedgerInvariantError, UnsupportedLifecycle) as e:
        raise SimulationInvariantError(
            f"decision {ctx.decision_id}: the ledger cannot book {len(legs)} leg(s): {e}"
        ) from e
    return None if headroom < ZERO_USD else _Sized(packages, debit)
