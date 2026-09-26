"""Exact reconciliation of one of our runs with its native LEAN replay (ADR 0002 §12, §16).

``replay_input`` turns our artifacts into the replay algorithm's input, the action script of
``probes/ProbeAction.cs`` that ``replay/ReplayAlgorithm.cs`` runs; ``reconcile`` compares our
artifacts with the records the replay wrote. Both are pure. Every comparison is exact: its
difference ``lean − ours`` must equal the one the known mismatches of §12 predict by formula,
zero unless one applies. Nothing is widened and no comparison is skipped.

The script (M1: fills are replayed at our instants, so timing, limits, retries, participation and
all-or-none stay ours):

- each FILLED event: a ``combo_market`` order at the fill instant, tagged with the event id, each
  leg's ratio its contracts per package; then a ``snapshot`` with the same tag;
- each order our run cancelled after F3, a closing order whose every attempt missed its limit:
  a ``combo_limit`` at its DEC submission, tagged with the order id, at the order's limit per
  package and per price unit, then a ``cancel`` at F3 (diagnostic; LEAN's strict limit, M6,
  never fills what we missed);
- each account point: a ``snapshot`` at the session's CLOSE, tagged with its ISO date.

The comparisons (``Check``); total cash is CASH + ΣRECEIVABLE + ΣPAYABLE against LEAN's cash
plus unsettled cash, settled cash CASH against LEAN's cash:

- per fill: legs filled; per leg fill time, price and quantity; the fill's fees; total cash
  and settled cash at the snapshot after it (settled cash differs by our T+1 dues, M2);
- per session at CLOSE: total and settled cash as after a fill, each contract's holding, and
  our mid NLV (at CUT, after settlement) against LEAN's total portfolio value at 16:00. LEAN
  marks each leg at its latest quote bar: the mid, or the nonzero side when a side is all zero
  (M13: a NO_BID leg at its ask), so each leg held at CUT adds ``q·m·(LEAN mark − mid)``. LEAN
  processes an expiry after 16:00 (M14), so a package our CUT settled adds ``q·m·LEAN mark`` per
  leg, at its last bar (F3: no expiry-day CLOSE quote), less the settlement's cash net of fees;
- per settlement: exercises; per contract the exercise time (M14: 01:00 America/New_York the
  next calendar day, against our CUT), the quantity and the index value LEAN exercised at
  against our settlement value (M3); fees; and cash. LEAN's exercise fill price is 0 and the
  intrinsic value a separate cash adjustment (M14), so LEAN's settlement cash is the sum of the
  cash changes at its exercise events;
- per missed limit: no LEAN fill, every leg cancelled;
- the run: no LEAN fill left unmatched, no action unexecuted, the final total cash.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from enum import StrEnum
from itertools import pairwise
from types import MappingProxyType
from typing import Final
from zoneinfo import ZoneInfo

from options_backtest.data.manifest import FrozenDataset
from options_backtest.data.records import QuoteObservation
from options_backtest.engine.orders import NonfillReason
from options_backtest.models.artifacts import (
    AccountPoint,
    ArtifactBundle,
    EventSummary,
    FillDetail,
    FilledLeg,
    NonfillDetail,
    OrderSnapshot,
    SettlementDetail,
    SimEvent,
    SimEventKind,
)
from options_backtest.models.market import require_type
from options_backtest.models.result import CalculationStatus
from options_backtest.money import EXACT

from .runner import JsonRecord

REPLAY_TYPE_NAME: Final = "ReplayAlgorithm"
"""The replay algorithm's class, ``replay/ReplayAlgorithm.cs``."""
RUN: Final = "run"
"""``Comparison.at`` of the whole-run comparisons."""
_NEW_YORK: Final = ZoneInfo("America/New_York")  # LEAN's algorithm time zone
_EXPIRY_PROCESSING: Final = time(1)  # M14: 01:00 New York on the next calendar day
_WALL_CLOCK: Final = "%Y-%m-%dT%H:%M:%S"  # ProbeAction.TimeFormat
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_SECOND_NS: Final = 10**9
_ZERO: Final = Decimal(0)
_FILLED: Final = frozenset({"Filled", "PartiallyFilled"})
_CANCELED: Final = "Canceled"
_EXERCISE: Final = "OptionExercise"
_RECORD_KINDS: Final = frozenset({"order_event", "snapshot"})


class Check(StrEnum):
    """What one comparison compares (the module docstring gives each one's prediction)."""

    FILL_COUNT = "fill_count"
    FILL_TIME = "fill_time_s"
    FILL_PRICE = "fill_price"
    FILL_QUANTITY = "fill_quantity"
    FILL_FEES = "fill_fees"
    TOTAL_CASH_AFTER_FILL = "total_cash_after_fill"
    SETTLED_CASH_AFTER_FILL = "settled_cash_after_fill"
    TOTAL_CASH_AT_CLOSE = "total_cash_at_close"
    SETTLED_CASH_AT_CLOSE = "settled_cash_at_close"
    HOLDING = "holding"
    MID_NLV = "mid_nlv"
    EXERCISE_COUNT = "exercise_count"
    EXERCISE_TIME = "exercise_time_s"
    EXERCISE_QUANTITY = "exercise_quantity"
    SETTLEMENT_VALUE = "settlement_value"
    SETTLEMENT_FEES = "settlement_fees"
    SETTLEMENT_CASH = "settlement_cash"
    DIAGNOSTIC_FILLS = "diagnostic_fills"
    DIAGNOSTIC_CANCELS = "diagnostic_cancels"
    UNMATCHED_FILLS = "unmatched_fills"
    UNEXECUTED_ACTIONS = "unexecuted_actions"
    FINAL_TOTAL_CASH = "final_total_cash"


class KnownMismatch(StrEnum):
    """The known mismatches of ADR 0002 §12 that predict a nonzero difference."""

    M2 = "M2"
    M13 = "M13"
    M14 = "M14"


@dataclass(frozen=True, slots=True)
class Comparison:
    """One exact comparison of our value with LEAN's.

    Attributes:
        check: What is compared.
        at: Our event id (fills, settlements), order id (missed limits), ISO session date
            (closes) or ``RUN``.
        subject: Contract id, or "" for the whole fill, session, settlement or run.
        ours: Our value; times are UTC epoch seconds.
        lean: LEAN's value, in the same unit.
        expected_difference: ``lean − ours`` the known mismatches predict; 0 when none applies.
        mismatches: The known mismatches in ``expected_difference``.

    """

    check: Check
    at: str
    subject: str
    ours: Decimal
    lean: Decimal
    expected_difference: Decimal
    mismatches: tuple[KnownMismatch, ...]

    @property
    def explained(self) -> bool:
        """Return whether ``lean − ours`` is exactly ``expected_difference``."""
        with localcontext(EXACT):
            return self.lean - self.ours == self.expected_difference


@dataclass(frozen=True, slots=True)
class _MissedLimit:
    """A closing order our run cancelled after F3, every attempt past its limit."""

    order_id: str
    submitted_at_ns: int
    cancelled_at_ns: int
    packages: int
    limit_usd: Decimal
    legs: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class _LeanFill:
    """A LEAN order event of status Filled or PartiallyFilled."""

    tag: str | None
    contract: str
    order_type: str
    utc_s: int
    price: Decimal
    quantity: Decimal
    fee: Decimal
    index_price: Decimal
    cash_change: Decimal


@dataclass(frozen=True, slots=True)
class _LeanSnapshot:
    """A LEAN snapshot or end record: cash, unsettled cash, portfolio value, nonzero holdings."""

    cash: Decimal
    unsettled: Decimal
    total_portfolio_value: Decimal
    holdings: Mapping[str, Decimal]


@dataclass(frozen=True, slots=True)
class _Replay:
    """The replay's records, typed."""

    fills: tuple[_LeanFill, ...]
    cancel_tags: tuple[str | None, ...]
    snapshots: Mapping[str, _LeanSnapshot]
    end: _LeanSnapshot
    unexecuted: int

    def snapshot(self, tag: str) -> _LeanSnapshot:
        """Return the snapshot with ``tag``; none is a replay that did not run its script."""
        found = self.snapshots.get(tag)
        if found is None:
            raise ValueError(f"LEAN wrote no snapshot tagged {tag}")
        return found


@dataclass(frozen=True, slots=True)
class _Marks:
    """The dataset's quotes and multipliers, for the marks LEAN takes at a close."""

    quotes: Mapping[str, tuple[QuoteObservation, ...]]
    multipliers: Mapping[str, Decimal]

    def latest(self, contract: str, at_ns: int) -> QuoteObservation:
        """Return the contract's latest quote at or before ``at_ns``: LEAN's last bar."""
        seen = [quote for quote in self.quotes.get(contract, ()) if quote.observed_at_ns <= at_ns]
        if not seen:
            raise ValueError(f"no quote of {contract} at or before {at_ns}: LEAN has no mark")
        return seen[-1]

    def lean_value(self, contract: str, quantity: int, at_ns: int) -> Decimal:
        """Return ``q·m·LEAN mark`` of a held leg at ``at_ns``."""
        mark = _lean_mark(self.latest(contract, at_ns))
        with localcontext(EXACT):
            return quantity * self.multipliers[contract] * mark

    def m13_term(self, contract: str, quantity: int, at_ns: int) -> Decimal:
        """Return ``q·m·(LEAN mark − mid)`` of a held leg at ``at_ns``: 0 unless a side is 0."""
        quote = self.latest(contract, at_ns)
        mark = _lean_mark(quote)
        with localcontext(EXACT):
            return quantity * self.multipliers[contract] * (mark - (quote.bid + quote.ask) / 2)


def replay_input(bundle: ArtifactBundle, dataset: FrozenDataset) -> JsonRecord:
    """Return the replay algorithm's input: the run's window, deposit, contracts and script.

    Args:
        bundle: A valid run's artifacts.
        dataset: The dataset it ran on (the multipliers of the missed limits).

    Returns:
        ``start_date``/``end_date`` (the account curve's first and last sessions), ``cash`` (the
        deposit), ``contracts`` (sorted) and ``actions`` sorted by time, stable within one.

    Raises:
        TypeError: If an argument has the wrong type.
        ValueError: If the run is not valid, a fill's legs are not whole ratios, or a cancelled
            order is not a closing limit order that missed its limit at every attempt.
        decimal.Inexact: If a limit is not exact per package and price unit.

    """
    _require_inputs(bundle, dataset)
    fills = _events_of(bundle, SimEventKind.FILLED)
    missed = _missed_limits(bundle.events)
    multipliers = _multipliers(dataset)
    actions = [
        *(action for event in fills for action in _fill_actions(event)),
        *(action for order in missed for action in _missed_limit_actions(order, multipliers)),
        *(
            _snapshot_action(p.market_valuation_at_ns, p.session_date.isoformat())
            for p in bundle.account_curve
        ),
    ]
    traded = {leg.contract_id for event in fills for leg in _detail(event, FillDetail).legs}
    return {
        "start_date": bundle.account_curve[0].session_date.isoformat(),
        "end_date": bundle.account_curve[-1].session_date.isoformat(),
        "cash": str(bundle.result.initial_equity_usd.amount),
        "contracts": sorted(traded | {contract for order in missed for contract, _ in order.legs}),
        "actions": sorted(actions, key=lambda action: str(action["at"])),
    }


def reconcile(
    bundle: ArtifactBundle, dataset: FrozenDataset, records: Sequence[JsonRecord]
) -> tuple[Comparison, ...]:
    """Compare a run with the records of its LEAN replay; see the module docstring.

    Args:
        bundle: A valid run's artifacts.
        dataset: The dataset it ran on (quotes and multipliers of LEAN's marks).
        records: The replay's records, in output order, the last the end record.

    Returns:
        Every comparison: fills, closes, settlements, missed limits, then the run's.

    Raises:
        TypeError: If an argument has the wrong type.
        ValueError: If the run is not valid, a record is malformed or missing (an untagged
            snapshot, no end record), or LEAN has no mark for a held leg.

    """
    _require_inputs(bundle, dataset)
    replay = _parse(records, bundle.result.initial_equity_usd.amount)
    marks = _Marks(_quotes_by_contract(dataset), _multipliers(dataset))
    settlements = _settlements_by_session(bundle)
    missed = _missed_limits(bundle.events)
    rows: list[Comparison] = []
    for event in _events_of(bundle, SimEventKind.FILLED):
        rows += _fill_rows(event, replay)
    for point in bundle.account_curve:
        rows += _close_rows(
            point, bundle.events, settlements.get(point.session_date, ()), marks, replay
        )
    for previous, event in pairwise(bundle.events):
        if event.kind is SimEventKind.SETTLED:
            rows += _settlement_rows(event, previous.summary, replay)
    for order in missed:
        rows += _missed_limit_rows(order, replay)
    return (*rows, *_run_rows(bundle, missed, replay))


def unexplained(comparisons: Iterable[Comparison]) -> tuple[Comparison, ...]:
    """Return the comparisons whose difference no known mismatch explains.

    Args:
        comparisons: ``reconcile``'s comparisons.

    Returns:
        Those not ``explained``, in order; () when the replay agrees.

    """
    return tuple(comparison for comparison in comparisons if not comparison.explained)


# --- our run --------------------------------------------------------------------------------


def _require_inputs(bundle: ArtifactBundle, dataset: FrozenDataset) -> None:
    require_type(bundle, ArtifactBundle, "LEAN replay bundle")
    require_type(dataset, FrozenDataset, "LEAN replay dataset")
    status = bundle.result.calculation_status
    if status is not CalculationStatus.VALID:
        raise ValueError(f"only a valid run is replayed in LEAN, this one is {status}")
    if not bundle.account_curve:
        raise ValueError("a valid run has an account point per session; this one has none")


def _events_of(bundle: ArtifactBundle, kind: SimEventKind) -> tuple[SimEvent, ...]:
    return tuple(event for event in bundle.events if event.kind is kind)


def _detail[D](event: SimEvent, kind: type[D]) -> D:
    detail = event.detail
    if not isinstance(detail, kind):
        raise TypeError(f"{event.event_id}: {event.kind} carries {type(detail).__name__}")
    return detail


def _live_order(event: SimEvent) -> OrderSnapshot:
    order = event.summary.order
    if order is None:
        raise ValueError(f"{event.event_id}: an ORDER_SUBMITTED event leaves no live order")
    return order


def _state_at(events: Sequence[SimEvent], at_ns: int) -> EventSummary:
    """Return the state after the last event at or before ``at_ns``."""
    before = [event for event in events if event.at_ns <= at_ns]
    if not before:
        raise ValueError(f"our run has no event at or before {at_ns}")
    return before[-1].summary


def _total_cash(state: EventSummary) -> Decimal:
    """Return CASH + ΣRECEIVABLE + ΣPAYABLE (``payable`` is the positive −ΣPAYABLE)."""
    with localcontext(EXACT):
        return state.cash.amount + state.receivable.amount - state.payable.amount


def _dues(state: EventSummary) -> Decimal:
    with localcontext(EXACT):
        return state.receivable.amount - state.payable.amount


def _ratio(contracts: int, packages: int, where: str) -> int:
    if contracts % packages:
        raise ValueError(f"{where}: {contracts} contracts are not a whole ratio of {packages}")
    return contracts // packages


def _missed_limits(events: Sequence[SimEvent]) -> tuple[_MissedLimit, ...]:
    """Return the orders cancelled after F3; each must be a closing limit that missed it."""
    submissions: dict[str, SimEvent] = {}
    reasons: defaultdict[str, list[NonfillReason]] = defaultdict(list)
    missed: list[_MissedLimit] = []
    for previous, event in pairwise(events):
        if event.kind is SimEventKind.ORDER_SUBMITTED:
            submissions[_live_order(event).order_id] = event
        elif event.kind is SimEventKind.NOT_FILLED:
            detail = _detail(event, NonfillDetail)
            reasons[detail.order_id].append(detail.reason)
        elif event.kind is SimEventKind.ORDER_CANCELLED:
            order_id = _detail(previous, NonfillDetail).order_id
            missed.append(_missed_limit(submissions[order_id], event, reasons[order_id]))
    return tuple(missed)


def _missed_limit(
    submission: SimEvent, cancel: SimEvent, reasons: Sequence[NonfillReason]
) -> _MissedLimit:
    order = _live_order(submission)
    if order.purpose.opening or order.limit_usd is None:
        raise ValueError(
            f"{order.order_id}: a cancelled {order.purpose} order is not replayed; only a "
            "closing limit order's legs follow from the held position"
        )
    if any(reason is not NonfillReason.LIMIT for reason in reasons):
        raise ValueError(
            f"{order.order_id}: cancelled after {[str(r) for r in reasons]}; LEAN's limit order "
            "reproduces only limit misses (M1, M5)"
        )
    legs = tuple(
        (contract, _ratio(-quantity, order.packages, order.order_id))
        for contract, quantity in submission.summary.held
    )
    return _MissedLimit(
        order.order_id,
        submission.at_ns,
        cancel.at_ns,
        order.packages,
        order.limit_usd.amount,
        legs,
    )


def _settlements_by_session(bundle: ArtifactBundle) -> dict[date, tuple[SettlementDetail, ...]]:
    found: defaultdict[date, list[SettlementDetail]] = defaultdict(list)
    for event in _events_of(bundle, SimEventKind.SETTLED):
        found[event.session_date].append(_detail(event, SettlementDetail))
    return {day: tuple(details) for day, details in found.items()}


# --- the dataset ----------------------------------------------------------------------------


def _multipliers(dataset: FrozenDataset) -> dict[str, Decimal]:
    """Return each contract's premium multiplier; its versions must agree."""
    found: dict[str, Decimal] = {}
    for version in dataset.contracts:
        terms = version.terms
        multiplier = found.setdefault(terms.contract_id, terms.premium_multiplier)
        if multiplier != terms.premium_multiplier:
            raise ValueError(f"{terms.contract_id}: versions disagree on the multiplier")
    return found


def _quotes_by_contract(dataset: FrozenDataset) -> dict[str, tuple[QuoteObservation, ...]]:
    found: defaultdict[str, list[QuoteObservation]] = defaultdict(list)
    for quote in dataset.quotes:
        found[quote.contract_id].append(quote)
    return {
        contract: tuple(sorted(quotes, key=lambda quote: quote.observed_at_ns))
        for contract, quotes in found.items()
    }


def _lean_mark(quote: QuoteObservation) -> Decimal:
    """Return LEAN's mark of a quote bar: the mid, or the nonzero side when one side is 0 (M13).

    LEAN drops a side whose prices are all zero, and a bar's close is the mid of the sides it
    keeps (``QuoteBar.cs``, ``export.LEAN_FORMAT``).
    """
    bid, ask = quote.bid, quote.ask
    if bid < 0 or ask < 0 or bid == ask == 0:
        raise ValueError(f"{quote.observation_id}: LEAN has no mark for bid {bid}, ask {ask}")
    with localcontext(EXACT):
        return bid + ask if _ZERO in (bid, ask) else (bid + ask) / 2


# --- the script -----------------------------------------------------------------------------


def _wall_clock(at_ns: int) -> str:
    """Return an instant as New York wall clock, the action time format."""
    if at_ns % _SECOND_NS:
        raise ValueError(f"{at_ns} ns is not a whole second; an action time is")
    instant = _EPOCH + timedelta(seconds=at_ns // _SECOND_NS)
    return instant.astimezone(_NEW_YORK).strftime(_WALL_CLOCK)


def _snapshot_action(at_ns: int, tag: str) -> JsonRecord:
    return {"at": _wall_clock(at_ns), "kind": "snapshot", "tag": tag}


def _fill_actions(event: SimEvent) -> list[JsonRecord]:
    detail = _detail(event, FillDetail)
    tag = event.event_id
    legs = [[leg.contract_id, _ratio(leg.contracts, detail.packages, tag)] for leg in detail.legs]
    order = {"at": _wall_clock(event.at_ns), "kind": "combo_market", "tag": tag}
    return [
        {**order, "legs": legs, "quantity": detail.packages},
        _snapshot_action(event.at_ns, tag),
    ]


def _missed_limit_actions(
    order: _MissedLimit, multipliers: Mapping[str, Decimal]
) -> list[JsonRecord]:
    """Return the combo limit at DEC, per package and price unit, and its cancel at F3."""
    units = {multipliers[contract] for contract, _ in order.legs}
    if len(units) != 1:
        raise ValueError(
            f"{order.order_id}: legs of multipliers {sorted(units)} have no unit price"
        )
    with localcontext(EXACT):
        limit = order.limit_usd / (units.pop() * order.packages)
    legs = [[contract, ratio] for contract, ratio in order.legs]
    return [
        {
            "at": _wall_clock(order.submitted_at_ns),
            "kind": "combo_limit",
            "tag": order.order_id,
            "legs": legs,
            "quantity": order.packages,
            "limit": str(limit),
        },
        {"at": _wall_clock(order.cancelled_at_ns), "kind": "cancel", "tag": order.order_id},
    ]


# --- LEAN's records -------------------------------------------------------------------------


def _text(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str):
        raise ValueError(f"LEAN record {record.get('kind')!r} has no text {key}: {value!r}")
    return value


def _number(record: Mapping[str, object], key: str) -> Decimal:
    text = _text(record, key)
    try:
        value = Decimal(text)
    except InvalidOperation as e:
        raise ValueError(f"LEAN record {record.get('kind')!r} {key} {text!r} is no number") from e
    if not value.is_finite():
        raise ValueError(f"LEAN record {record.get('kind')!r} {key} {text!r} is not finite")
    return value


def _utc_s(text: str) -> int:
    try:
        instant = datetime.strptime(text, _WALL_CLOCK).replace(tzinfo=UTC)
    except ValueError as e:
        raise ValueError(f"LEAN utc_time {text!r} is not {_WALL_CLOCK}") from e
    return (instant - _EPOCH) // timedelta(seconds=1)


def _parse(records: Sequence[JsonRecord], initial_cash: Decimal) -> _Replay:
    """Type the records; each fill's cash change is against the record before it."""
    if not all(isinstance(record, dict) for record in records):
        raise TypeError("every LEAN record must be a JSON object")
    if not records or records[-1].get("kind") != "end":
        raise ValueError("the LEAN records do not close with their end record")
    body = records[:-1]
    kinds = {_text(record, "kind") for record in body}
    if kinds - _RECORD_KINDS:
        raise ValueError(
            f"LEAN records of kind {', '.join(map(repr, sorted(kinds - _RECORD_KINDS)))}"
        )
    cash_before = [initial_cash, *(_number(record, "cash") for record in body)]
    events = [(r, cash_before[i]) for i, r in enumerate(body) if r["kind"] == "order_event"]
    unexecuted = records[-1].get("unexecuted")
    if not isinstance(unexecuted, list):
        raise ValueError(f"the LEAN end record lists no unexecuted actions: {unexecuted!r}")
    return _Replay(
        fills=tuple(_fill(r, cash) for r, cash in events if _text(r, "status") in _FILLED),
        cancel_tags=tuple(r.get("tag") for r, _ in events if _text(r, "status") == _CANCELED),
        snapshots=_snapshots([record for record in body if record["kind"] == "snapshot"]),
        end=_snapshot(records[-1]),
        unexecuted=len(unexecuted),
    )


def _fill(record: JsonRecord, cash_before: Decimal) -> _LeanFill:
    tag = record.get("tag")
    if tag is not None and not isinstance(tag, str):
        raise ValueError(f"LEAN order event tag {tag!r} is not text")
    with localcontext(EXACT):
        cash_change = _number(record, "cash") - cash_before
    return _LeanFill(
        tag=tag,
        contract=_text(record, "contract"),
        order_type=_text(record, "order_type"),
        utc_s=_utc_s(_text(record, "utc_time")),
        price=_number(record, "fill_price"),
        quantity=_number(record, "fill_quantity"),
        fee=_number(record, "fee"),
        index_price=_number(record, "index_price"),
        cash_change=cash_change,
    )


def _snapshots(records: Sequence[JsonRecord]) -> Mapping[str, _LeanSnapshot]:
    found: dict[str, _LeanSnapshot] = {}
    for record in records:
        tag = _text(record, "tag")
        if tag in found:
            raise ValueError(f"two LEAN snapshots are tagged {tag}")
        found[tag] = _snapshot(record)
    return MappingProxyType(found)


def _snapshot(record: JsonRecord) -> _LeanSnapshot:
    holdings = record.get("holdings")
    if not isinstance(holdings, list):
        raise ValueError(f"LEAN {record.get('kind')} {record.get('tag')!r} lists no holdings")
    quantities = {_text(held, "contract"): _number(held, "quantity") for held in holdings}
    return _LeanSnapshot(
        cash=_number(record, "cash"),
        unsettled=_number(record, "unsettled_cash"),
        total_portfolio_value=_number(record, "total_portfolio_value"),
        holdings=MappingProxyType({c: q for c, q in quantities.items() if q != _ZERO}),
    )


# --- comparisons ----------------------------------------------------------------------------


def _row(  # noqa: PLR0913 — one comparison's fields, each passed explicitly
    check: Check,
    at: str,
    subject: str,
    ours: Decimal | int,
    lean: Decimal | int,
    *,
    predicted: tuple[Decimal, tuple[KnownMismatch, ...]] = (_ZERO, ()),
) -> Comparison:
    expected, mismatches = predicted
    return Comparison(check, at, subject, Decimal(ours), Decimal(lean), expected, mismatches)


def _sum(values: Iterable[Decimal]) -> Decimal:
    with localcontext(EXACT):
        return sum(values, _ZERO)


def _epoch_s(at_ns: int) -> int:
    if at_ns % _SECOND_NS:
        raise ValueError(f"{at_ns} ns is not a whole second")
    return at_ns // _SECOND_NS


def _cash_rows(
    checks: tuple[Check, Check], at: str, ours: EventSummary, lean: _LeanSnapshot
) -> list[Comparison]:
    """Return total cash (equal) and settled cash (LEAN settled our T+1 dues at once, M2)."""
    total, settled = checks
    dues = _dues(ours)
    with localcontext(EXACT):
        lean_total = lean.cash + lean.unsettled
    return [
        _row(total, at, "", _total_cash(ours), lean_total),
        _row(
            settled,
            at,
            "",
            ours.cash.amount,
            lean.cash,
            predicted=(dues, (KnownMismatch.M2,) if dues != _ZERO else ()),
        ),
    ]


def _fill_rows(event: SimEvent, replay: _Replay) -> list[Comparison]:
    detail = _detail(event, FillDetail)
    tag = event.event_id
    lean = [fill for fill in replay.fills if fill.tag == tag]
    by_contract = {fill.contract: fill for fill in lean}
    rows = [_row(Check.FILL_COUNT, tag, "", len(detail.legs), len(lean))]
    for leg in detail.legs:
        rows += _leg_rows(tag, _epoch_s(event.at_ns), leg, by_contract.get(leg.contract_id))
    rows.append(_row(Check.FILL_FEES, tag, "", detail.fees.amount, _sum(f.fee for f in lean)))
    checks = (Check.TOTAL_CASH_AFTER_FILL, Check.SETTLED_CASH_AFTER_FILL)
    return rows + _cash_rows(checks, tag, event.summary, replay.snapshot(tag))


def _leg_rows(tag: str, our_s: int, leg: FilledLeg, fill: _LeanFill | None) -> list[Comparison]:
    if fill is None:
        return []  # FILL_COUNT reports the leg LEAN did not fill
    return [
        _row(Check.FILL_TIME, tag, leg.contract_id, our_s, fill.utc_s),
        _row(Check.FILL_PRICE, tag, leg.contract_id, leg.price.value, fill.price),
        _row(Check.FILL_QUANTITY, tag, leg.contract_id, leg.contracts, fill.quantity),
    ]


def _close_rows(
    point: AccountPoint,
    events: Sequence[SimEvent],
    settlements: tuple[SettlementDetail, ...],
    marks: _Marks,
    replay: _Replay,
) -> list[Comparison]:
    day = point.session_date.isoformat()
    at_close = _state_at(events, point.market_valuation_at_ns)
    at_cut = _state_at(events, point.ledger_cutoff_at_ns)
    lean = replay.snapshot(day)
    ours = dict(at_close.held)
    rows = _cash_rows((Check.TOTAL_CASH_AT_CLOSE, Check.SETTLED_CASH_AT_CLOSE), day, at_close, lean)
    rows += [
        _row(
            Check.HOLDING, day, contract, ours.get(contract, 0), lean.holdings.get(contract, _ZERO)
        )
        for contract in sorted(ours.keys() | lean.holdings.keys())
    ]
    rows.append(_nlv_row(point, (at_close, at_cut), settlements, marks, lean))
    return rows


def _nlv_row(
    point: AccountPoint,
    states: tuple[EventSummary, EventSummary],
    settlements: tuple[SettlementDetail, ...],
    marks: _Marks,
    lean: _LeanSnapshot,
) -> Comparison:
    """Return mid NLV against LEAN's portfolio value, predicted by M13 and M14 (module doc)."""
    day = point.session_date.isoformat()
    if point.mid_nlv is None:
        raise ValueError(f"{day}: our account point has no mid NLV to compare")
    at_close, at_cut = states
    close_ns = point.market_valuation_at_ns
    settled = {contract for settlement in settlements for contract in settlement.contract_ids}
    m13 = _sum(marks.m13_term(contract, q, close_ns) for contract, q in at_cut.held)
    still_held = _sum(marks.lean_value(c, q, close_ns) for c, q in at_close.held if c in settled)
    settled_cash = _sum(s.net_cash.amount - s.fees.amount for s in settlements)
    with localcontext(EXACT):
        expected = m13 + still_held - settled_cash
    mismatches: tuple[KnownMismatch, ...] = (KnownMismatch.M13,) if m13 != _ZERO else ()
    mismatches += (KnownMismatch.M14,) if settlements else ()
    ours, lean_value = point.mid_nlv.amount, lean.total_portfolio_value
    return _row(Check.MID_NLV, day, "", ours, lean_value, predicted=(expected, mismatches))


def _settlement_rows(event: SimEvent, before: EventSummary, replay: _Replay) -> list[Comparison]:
    detail = _detail(event, SettlementDetail)
    tag = event.event_id
    held = dict(before.held)
    if not held.keys() >= set(detail.contract_ids):
        raise ValueError(f"{tag} settles {detail.contract_ids}, held before it: {sorted(held)}")
    exercises = [
        fill
        for fill in replay.fills
        if fill.order_type == _EXERCISE and fill.contract in detail.contract_ids
    ]
    by_contract = {fill.contract: fill for fill in exercises}
    next_day = datetime.combine(
        event.session_date + timedelta(days=1), _EXPIRY_PROCESSING, _NEW_YORK
    )
    times = (_epoch_s(event.at_ns), (next_day - _EPOCH) // timedelta(seconds=1))
    rows = [_row(Check.EXERCISE_COUNT, tag, "", len(detail.contract_ids), len(exercises))]
    for contract in detail.contract_ids:
        rows += _exercise_rows(
            tag, (contract, held[contract]), times, detail, by_contract.get(contract)
        )
    with localcontext(EXACT):
        cash = detail.net_cash.amount - detail.fees.amount
    rows.append(
        _row(Check.SETTLEMENT_FEES, tag, "", detail.fees.amount, _sum(f.fee for f in exercises))
    )
    rows.append(_row(Check.SETTLEMENT_CASH, tag, "", cash, _sum(f.cash_change for f in exercises)))
    return rows


def _exercise_rows(
    tag: str,
    held: tuple[str, int],
    times: tuple[int, int],
    detail: SettlementDetail,
    fill: _LeanFill | None,
) -> list[Comparison]:
    """Return exercise time (M14), quantity and the index value LEAN exercised at (M3)."""
    if fill is None:
        return []  # EXERCISE_COUNT reports the contract LEAN did not exercise
    contract, quantity = held
    our_s, lean_s = times
    lag = (Decimal(lean_s - our_s), (KnownMismatch.M14,))
    return [
        _row(Check.EXERCISE_TIME, tag, contract, our_s, fill.utc_s, predicted=lag),
        _row(Check.EXERCISE_QUANTITY, tag, contract, -quantity, fill.quantity),
        _row(Check.SETTLEMENT_VALUE, tag, contract, detail.value.value, fill.index_price),
    ]


def _missed_limit_rows(order: _MissedLimit, replay: _Replay) -> list[Comparison]:
    """Return LEAN's fills of a limit we missed (none) and its cancelled legs (all)."""
    fills = sum(1 for fill in replay.fills if fill.tag == order.order_id)
    cancels = sum(1 for tag in replay.cancel_tags if tag == order.order_id)
    return [
        _row(Check.DIAGNOSTIC_FILLS, order.order_id, "", 0, fills),
        _row(Check.DIAGNOSTIC_CANCELS, order.order_id, "", len(order.legs), cancels),
    ]


def _run_rows(
    bundle: ArtifactBundle, missed: tuple[_MissedLimit, ...], replay: _Replay
) -> list[Comparison]:
    """Return LEAN fills matched to nothing of ours, unexecuted actions and final total cash."""
    tags = {event.event_id for event in _events_of(bundle, SimEventKind.FILLED)}
    tags |= {order.order_id for order in missed}
    settled = {
        contract
        for event in _events_of(bundle, SimEventKind.SETTLED)
        for contract in _detail(event, SettlementDetail).contract_ids
    }
    unmatched = [
        fill
        for fill in replay.fills
        if fill.tag not in tags and not (fill.order_type == _EXERCISE and fill.contract in settled)
    ]
    with localcontext(EXACT):
        lean_total = replay.end.cash + replay.end.unsettled
    return [
        _row(Check.UNMATCHED_FILLS, RUN, "", 0, len(unmatched)),
        _row(Check.UNEXECUTED_ACTIONS, RUN, "", 0, replay.unexecuted),
        _row(Check.FINAL_TOTAL_CASH, RUN, "", _total_cash(bundle.events[-1].summary), lean_total),
    ]
