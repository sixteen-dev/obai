"""Deterministic synthetic markets: ``MarketSpec`` to ``FrozenDataset`` (ADR 0002 §5, design §7.4).

``generate`` is pure: ``random.Random(seed)`` drives the index path and nothing else, and every
time-zone conversion happens here, once, with ``zoneinfo`` (America/New_York). The rules below
are binding for golden scenarios (ADR 0002 §17); an e2e TOML override's ``kind`` is the
snake_case class name (``quote_pin`` is ``QuotePin``).

Sessions: every weekday in ``[first_session, last_session]`` not in ``holidays``; open 09:30,
close 16:00 (13:00 on ``early_closes``), cutoff 23:59:59 local. Slots per
``reference.calendars.slot_times``.

Index: per session index ``i`` (date order) ``S_i = Decimal(float(index_start) · exp(L_i))``
quantized to 0.01 ``ROUND_HALF_EVEN``, ``L_0 = 0``, ``L_i = L_{i-1} + float(daily_drift) +
float(daily_vol) · z_i``, ``z_i = rng.gauss(0.0, 1.0)`` drawn once per session ``i >= 1``. With
zero drift and zero vol, ``S_i = index_start`` exactly. Within a session the index is constant:
INDEX_VALUE observations at DEC, F1, F2, F3 and CLOSE all equal ``S_i``; OFFICIAL_CLOSE equals
``S_i``, observed at CLOSE and available at 17:00 local. A root's underlying value is
``S_i / settlement_divisor`` exactly (SPX ``S_i``; XSP ``S_i/10``); one series per underlying
of ``roots``.

Expiries (every root lists the same dates): for each ``k`` in ``weekly_dtes``,
``first_session + k`` days moved back to the nearest weekday not in ``holidays`` (a Good Friday
expiry becomes Thursday). ``expires_at_ns = last_tradable_at_ns`` = that date's close (16:00;
13:00 if it is an ``early_closes`` session). Every contract is listed, known and effective from
the first session's ``open_ns`` (version ``@v1``, ``effective_to_ns`` None).

Strikes per root: centre ``c = strike_step · round_half_up(index_start / divisor / strike_step)``
in the root's points, strikes ``c + j·strike_step`` for ``j = -strikes_each_side ..
+strikes_each_side``, calls and puts. The grid never moves.

Quotes: every session ``d``, root, expiry ``e >= d``, strike, right and slot ``τ`` in DEC, F1,
F2, F3, CLOSE with ``τ < expires_at_ns`` (none at the expiry session's CLOSE):
``t = (expires_at_ns - τ)/(365·86_400e9)`` years, ``DF = DiscountCurve(rates).df(t·365)``,
``F = S/DF`` (S the root's index value), ``mid = black76(F, K, t, DF, sigma, right)``,
``h = max(half_spread_abs, half_spread_rel · mid)``, ``bid = tick · floor(Decimal(mid - h) /
tick)`` and ``ask = tick · ceil(Decimal(mid + h) / tick)`` (exact on the float's value, so the
market is never locked or crossed); a bid ``<= 0`` becomes 0 with size 0 (NO_BID), else
``bid_size``; ask size ``ask_size``; ``observed_at_ns = available_at_ns = τ``.

Settlements: per root and expiry that is a table session, series ``rules.settlement_series``,
value ``settlement_price(rules, S_e)`` (SPXW: ``S_e``; XSP: ``S_e/10`` rounded half up to cents),
available 17:00 local on the expiry date, ``payable_date`` the next weekday not in
``holidays``, final, correction 0.

Rates: per table session ``S`` and ``(tenor_days, bey)`` in ``rates``, curve ``UST_CMT``, dated
the previous table session (``S - 1 day`` for the first) and available at ``S``'s DEC, so every
session's DEC sees its own curve. Coverage: ``("quotes", d, COMPLETE, "")`` per session.
Activity: none unless pinned.

Then ``overrides`` apply in order; then features are computed from the overridden tables with
``pricing.features.feature_series`` per root and ``FEATURE_VERSIONS``. Manifest: fidelity
SYNTHETIC_FIXTURE, limitations (), ``calendar_version="synthetic_weekdays_v1"``,
``PRODUCT_RULES_VERSION``, license ``synthetic_public``; provenance as ``data.records.Provenance``
describes, with ``normalizer_version=GENERATOR_VERSION``.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, Inexact, localcontext
from itertools import pairwise, product
from typing import Final
from zoneinfo import ZoneInfo

from options_backtest.data.manifest import (
    SYNTHETIC_LICENSE_POLICY_ID,
    SYNTHETIC_SOURCE_ID,
    FrozenDataset,
    canonical_json,
)
from options_backtest.data.records import (
    ActivityObservation,
    ContractVersion,
    CoveragePartition,
    CoverageState,
    FidelityClass,
    Provenance,
    QuoteObservation,
    RateObservation,
    SettlementObservation,
    TradingSession,
    UnderlyingField,
    UnderlyingObservation,
)
from options_backtest.models.market import (
    ContractTerms,
    Deliverable,
    DeliverableComponent,
    OptionType,
    require_type,
)
from options_backtest.money import EXACT, ZERO_USD, Price
from options_backtest.pricing.european import black76
from options_backtest.pricing.features import FEATURE_VERSIONS, feature_series
from options_backtest.reference.calendars import QUOTE_SLOTS, Slot, dte, slot_instant, slot_times
from options_backtest.reference.products import (
    PRODUCT_RULES_VERSION,
    ProductRules,
    product_rules,
    settlement_price,
)
from options_backtest.reference.rates import CMT_CURVE_ID, DiscountCurve, bill_df

GENERATOR_VERSION: Final = "synthetic_market_v1"
CALENDAR_VERSION: Final = "synthetic_weekdays_v1"
_SOURCE_SCHEMA_VERSION: Final = "market_spec_v1"
_TIME_ZONE: Final = "America/New_York"
_OPEN: Final = time(9, 30)
_CLOSE: Final = time(16, 0)
_EARLY_CLOSE: Final = time(13, 0)
_CUTOFF: Final = time(23, 59, 59)
_PUBLICATION: Final = time(17, 0)
"""Local time the official close and the settlement value become available."""
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_NS_PER_SECOND: Final = 10**9
_NS_PER_YEAR: Final = 365 * 86_400 * _NS_PER_SECOND
_CENT: Final = Decimal("0.01")
_SATURDAY: Final = 5
"""``date.weekday()`` of the first weekend day."""


@dataclass(frozen=True, slots=True)
class QuotePin:
    """Replace the prices and sizes of generated quotes; their instants are kept.

    Attributes:
        contract: Contract id.
        session: Session of the quotes.
        slots: Quote slots pinned (DEC, F1, F2, F3, CLOSE).
        bid: Raw bid; may be zero or negative, to build any status.
        ask: Raw ask; may be zero or negative.
        bid_size: Raw bid size.
        ask_size: Raw ask size.

    """

    contract: str
    session: date
    slots: tuple[Slot, ...]
    bid: Decimal
    ask: Decimal
    bid_size: int
    ask_size: int


@dataclass(frozen=True, slots=True)
class QuoteDrop:
    """Remove generated quotes.

    Attributes:
        contract: Contract id.
        session: Session of the quotes.
        slots: Quote slots removed.

    """

    contract: str
    session: date
    slots: tuple[Slot, ...]


@dataclass(frozen=True, slots=True)
class QuoteStale:
    """Age generated quotes: ``observed_at_ns -= seconds``; ``available_at_ns`` is kept.

    Attributes:
        contract: Contract id.
        session: Session of the quotes.
        slots: Quote slots aged.
        seconds: Age added, >= 1.

    """

    contract: str
    session: date
    slots: tuple[Slot, ...]
    seconds: int


@dataclass(frozen=True, slots=True)
class LateAvailability:
    """Delay one record's publication.

    Attributes:
        selector: The ``observation_id`` of one quote, underlying, activity, settlement or rate
            record, in the formats of ``data.records``.
        available_at: New ``available_at``: a timezone-aware instant later than the current one.

    """

    selector: str
    available_at: datetime


@dataclass(frozen=True, slots=True)
class UnderlyingDrop:
    """Remove index observations.

    Attributes:
        underlying_id: Underlying index.
        field: INDEX_VALUE, or OFFICIAL_CLOSE (slot CLOSE).
        session: Session of the observations.
        slots: Slots removed.

    """

    underlying_id: str
    field: UnderlyingField
    session: date
    slots: tuple[Slot, ...]


@dataclass(frozen=True, slots=True)
class SettlementPin:
    """Replace the value of a generated settlement observation.

    Attributes:
        series: Settlement series (``SPX_PM`` or ``XSP_PM``).
        session: Expiry session.
        value: Value used as is (no rounding).

    """

    series: str
    session: date
    value: Price


@dataclass(frozen=True, slots=True)
class SettlementDrop:
    """Remove a generated settlement observation.

    Attributes:
        series: Settlement series.
        session: Expiry session.

    """

    series: str
    session: date


@dataclass(frozen=True, slots=True)
class RateDrop:
    """Remove the rate observations that become available at a session's DEC.

    Attributes:
        session: Session whose curve is removed.
        tenor_days: One tenor; None removes every tenor.

    """

    session: date
    tenor_days: int | None


@dataclass(frozen=True, slots=True)
class ActivityPin:
    """Add (or replace) a cumulative-volume observation.

    It is ``a:{contract}:{session}:{slot}``, measured through and available at the slot's
    instant (any slot, OPEN to CUT).

    Attributes:
        contract: Contract id.
        session: Session.
        slot: Slot whose instant ends the measured interval.
        cumulative_volume: Contracts traded in the session through then, >= 0.

    """

    contract: str
    session: date
    slot: Slot
    cumulative_volume: int


@dataclass(frozen=True, slots=True)
class TermsRevision:
    """Re-version a contract at a session's open, as an F05-style deliverable change.

    The current version ends (``effective_to_ns``) at the session's ``open_ns``; version
    ``@v{n+1}``, effective and known from that instant, keeps the contract id, listing, expiry,
    AEA and multiplier and delivers ``deliverable_units`` units
    (deliverable id ``{underlying_id}:{units}``).

    Attributes:
        contract: Contract id.
        session: Session whose open the revision takes effect at; on or before expiry.
        deliverable_units: New units per contract, > 0.

    """

    contract: str
    session: date
    deliverable_units: Decimal


@dataclass(frozen=True, slots=True)
class CoverageStatus:
    """Replace the status and note of an existing coverage partition.

    Attributes:
        table: Table name (the generator emits ``"quotes"`` partitions).
        session: Session.
        status: New status.
        note: New note.

    """

    table: str
    session: date
    status: CoverageState
    note: str


@dataclass(frozen=True, slots=True)
class EarlyClose:
    """Mark a session as a 13:00 early close in the session table only.

    Nothing generated for the session moves: its regular-hours observations then lie after the
    new close (phantom post-close data, design §7.3 item 4). ``MarketSpec.early_closes`` is what
    generates a genuine early session.

    Attributes:
        session: A table session that is not already an early close.

    """

    session: date


type Override = (
    QuotePin
    | QuoteDrop
    | QuoteStale
    | LateAvailability
    | UnderlyingDrop
    | SettlementPin
    | SettlementDrop
    | RateDrop
    | ActivityPin
    | TermsRevision
    | CoverageStatus
    | EarlyClose
)


@dataclass(frozen=True, slots=True)
class MarketSpec:
    """A synthetic market; every field is required (e2e defaults live in ``defaults.toml``).

    Attributes:
        seed: Seed of the index path's ``random.Random``.
        first_session: First session; a weekday not in ``holidays``.
        last_session: Last session, >= ``first_session``; a weekday not in ``holidays``.
        holidays: Weekdays without a session (they also move expiries and payable dates).
        early_closes: Table sessions closing at 13:00.
        index_start: SPX value of the first session, > 0, at most 2 decimal places.
        daily_drift: Log drift per session; 0 allowed.
        daily_vol: Log volatility per session, >= 0; 0 with zero drift fixes the index.
        sigma: Black-76 volatility of every generated quote, > 0.
        rates: (tenor_days, bey) pairs of the ``UST_CMT`` curve: non-empty, tenors strictly
            ascending and > 0; bey 0 gives DF 1. Every expiry needs
            ``dte(first_session, expiry) <`` the last tenor.
        roots: Option roots, a non-empty subset of {"SPXW", "XSP"} without repeats.
        weekly_dtes: Calendar-day offsets from ``first_session`` of the listed expiries, each
            >= 1; the resulting dates must be distinct.
        strike_step: Strike spacing in each root's points, > 0.
        strikes_each_side: Strikes on each side of the grid centre, >= 0; every strike > 0.
        tick: Quote price increment, > 0.
        half_spread_abs: Minimum half spread in price units, > 0.
        half_spread_rel: Half spread as a fraction of the model mid, >= 0.
        bid_size: Displayed bid size of every generated quote with a bid, > 0.
        ask_size: Displayed ask size of every generated quote, > 0.
        premium_multiplier: Contract multiplier of every root, > 0 (100 for the templates).
        deliverable_units: Index units per contract of every root, > 0; AEA = units × strike.
        overrides: Applied in order after generation.

    """

    seed: int
    first_session: date
    last_session: date
    holidays: tuple[date, ...]
    early_closes: tuple[date, ...]
    index_start: Decimal
    daily_drift: Decimal
    daily_vol: Decimal
    sigma: Decimal
    rates: tuple[tuple[int, Decimal], ...]
    roots: tuple[str, ...]
    weekly_dtes: tuple[int, ...]
    strike_step: Decimal
    strikes_each_side: int
    tick: Decimal
    half_spread_abs: Decimal
    half_spread_rel: Decimal
    bid_size: int
    ask_size: int
    premium_multiplier: Decimal
    deliverable_units: Decimal
    overrides: tuple[Override, ...]


def generate(spec: MarketSpec) -> FrozenDataset:
    """Generate the dataset the module docstring defines.

    Args:
        spec: The market.

    Returns:
        The frozen dataset; equal specs give byte-identical datasets.

    Raises:
        ValueError: If ``spec`` breaks a field constraint of ``MarketSpec``, or an override
            targets a record, partition or session that does not exist (after the overrides
            before it) or sets an ``available_at`` that is not later.

    """
    require_type(spec, MarketSpec, "generate spec")
    _check_spec(spec)
    digest = hashlib.sha256(canonical_json(spec)).hexdigest()
    provenance = Provenance(
        SYNTHETIC_SOURCE_ID, _SOURCE_SCHEMA_VERSION, digest, GENERATOR_VERSION, "0"
    )
    tables = _generated_tables(spec, provenance)
    for override in spec.overrides:
        _apply(tables, override, provenance)
    unfeatured = _freeze(tables)
    features = tuple(
        row
        for root in spec.roots
        for row in feature_series(unfeatured, _rules(spec, root), FEATURE_VERSIONS)
    )
    return unfeatured.with_features(features)


@dataclass(slots=True)
class _Tables:
    """The dataset's tables by key while overrides edit them; local to one ``generate`` call.

    Attributes:
        sessions: Sessions by date.
        contracts: Contract versions by version id.
        quotes: Quotes by observation id.
        underlying: Index observations by observation id.
        activity: Activity observations by observation id.
        settlements: Settlement observations by observation id.
        rates: Rate observations by observation id.
        coverage: Coverage partitions by (table, session date).

    """

    sessions: dict[date, TradingSession]
    contracts: dict[str, ContractVersion]
    quotes: dict[str, QuoteObservation]
    underlying: dict[str, UnderlyingObservation]
    activity: dict[str, ActivityObservation]
    settlements: dict[str, SettlementObservation]
    rates: dict[str, RateObservation]
    coverage: dict[tuple[str, date], CoveragePartition]


@dataclass(frozen=True, slots=True)
class _Listing:
    """One root's contracts of one expiry, with the rules that price and settle them.

    Attributes:
        rules: The root's rules, with the spec's multiplier and deliverable units.
        expiry: Expiry date.
        expires_at_ns: The expiry date's close.
        terms: Terms of every strike and right.

    """

    rules: ProductRules
    expiry: date
    expires_at_ns: int
    terms: tuple[ContractTerms, ...]


@dataclass(frozen=True, slots=True)
class _QuoteModel:
    """The spec's quote parameters, converted once for the quote loop.

    Attributes:
        sigma: Black-76 volatility.
        tick: Quote price increment.
        half_spread_abs: Minimum half spread, float64.
        half_spread_rel: Half spread per unit of model mid, float64.
        bid_size: Displayed size of a positive bid.
        ask_size: Displayed ask size.
        curve: The spec's ``UST_CMT`` curve.
        provenance: Provenance of every generated record.

    """

    sigma: float
    tick: Decimal
    half_spread_abs: float
    half_spread_rel: float
    bid_size: int
    ask_size: int
    curve: DiscountCurve
    provenance: Provenance


# --- spec validation ------------------------------------------------------------------------------


def _check_spec(spec: MarketSpec) -> None:
    """Refuse a spec that breaks a ``MarketSpec`` field constraint (targets come later)."""
    _check_calendar(spec)
    _check_index(spec)
    _check_chain(spec)
    for override in _require_tuple(spec.overrides, "overrides"):
        _check_override(override)


def _check_calendar(spec: MarketSpec) -> None:
    _require_exact(spec.seed, int, "seed")
    _require_exact(spec.first_session, date, "first_session")
    _require_exact(spec.last_session, date, "last_session")
    holidays = _require_dates(spec.holidays, "holidays")
    early_closes = _require_dates(spec.early_closes, "early_closes")
    first, last = spec.first_session, spec.last_session
    if not _is_business_day(first, holidays):
        raise ValueError(f"MarketSpec.first_session {first} must be a weekday not in holidays")
    if last < first or not _is_business_day(last, holidays):
        raise ValueError(
            f"MarketSpec.last_session {last} must be a weekday not in holidays, "
            f"on or after first_session {first}"
        )
    weekend = [day for day in holidays if day.weekday() >= _SATURDAY]
    if weekend:
        raise ValueError(f"MarketSpec.holidays must be weekdays, got {weekend}")
    outside = [
        day
        for day in early_closes
        if not (first <= day <= last and _is_business_day(day, holidays))
    ]
    if outside:
        raise ValueError(f"MarketSpec.early_closes must be table sessions, got {outside}")


def _check_index(spec: MarketSpec) -> None:
    _require_positive(spec.index_start, "index_start")
    with localcontext(EXACT):
        fraction_of_cent = spec.index_start % _CENT
    if fraction_of_cent != 0:
        raise ValueError(
            f"MarketSpec.index_start must have at most 2 decimal places, got {spec.index_start}"
        )
    _require_decimal(spec.daily_drift, "daily_drift")
    _require_non_negative(spec.daily_vol, "daily_vol")
    _require_positive(spec.sigma, "sigma")
    if not _require_tuple(spec.rates, "rates"):
        raise ValueError("MarketSpec.rates must not be empty")
    for tenor, bey in spec.rates:
        _require_count(tenor, "rates tenor_days", 1)
        _require_decimal(bey, "rates bey")
    tenors = [tenor for tenor, _ in spec.rates]
    if any(upper <= lower for lower, upper in pairwise(tenors)):
        raise ValueError(f"MarketSpec.rates tenors must ascend strictly, got {tenors}")


def _check_chain(spec: MarketSpec) -> None:
    roots = _require_tuple(spec.roots, "roots")
    if not roots or len(set(roots)) != len(roots):
        raise ValueError(f"MarketSpec.roots must be non-empty without repeats, got {roots}")
    for offset in _require_tuple(spec.weekly_dtes, "weekly_dtes"):
        _require_count(offset, "weekly_dtes item", 1)
    _require_positive(spec.strike_step, "strike_step")
    _require_count(spec.strikes_each_side, "strikes_each_side", 0)
    _require_positive(spec.tick, "tick")
    _require_positive(spec.half_spread_abs, "half_spread_abs")
    _require_non_negative(spec.half_spread_rel, "half_spread_rel")
    _require_count(spec.bid_size, "bid_size", 1)
    _require_count(spec.ask_size, "ask_size", 1)
    _require_positive(spec.premium_multiplier, "premium_multiplier")
    _require_positive(spec.deliverable_units, "deliverable_units")


def _check_override(override: object) -> None:
    """Refuse an override whose own fields are malformed; its target is checked when applied."""
    name = type(override).__name__
    if isinstance(override, QuotePin | QuoteDrop | QuoteStale | UnderlyingDrop):
        _check_slots(override.slots, name)
    if isinstance(override, UnderlyingDrop):
        require_type(override.field, UnderlyingField, f"{name}.field")
    if isinstance(override, QuoteStale) and not (
        type(override.seconds) is int and override.seconds >= 1
    ):
        raise ValueError(f"{name}.seconds must be an int >= 1, got {override.seconds!r}")
    if isinstance(override, LateAvailability) and not _is_aware(override.available_at):
        raise ValueError(
            f"{name}.available_at must be a timezone-aware datetime, got {override.available_at!r}"
        )


def _check_slots(slots: object, name: str) -> None:
    if not isinstance(slots, tuple) or not all(isinstance(slot, Slot) for slot in slots):
        raise TypeError(f"{name}.slots must be a tuple of Slot, got {slots!r}")
    if not slots or len(set(slots)) != len(slots):
        raise ValueError(f"{name}.slots must be non-empty without repeats, got {slots}")


def _is_aware(value: object) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


def _require_exact(value: object, kind: type[object], field: str) -> None:
    if type(value) is not kind:
        raise TypeError(f"MarketSpec.{field} must be {kind.__name__}, got {type(value).__name__}")


def _require_tuple(value: object, field: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"MarketSpec.{field} must be a tuple, got {type(value).__name__}")
    return value


def _require_dates(values: tuple[date, ...], field: str) -> tuple[date, ...]:
    for value in _require_tuple(values, field):
        _require_exact(value, date, f"{field} item")
    return values


def _require_decimal(value: object, field: str) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(f"MarketSpec.{field} must be Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"MarketSpec.{field} must be finite, got {value}")
    return value


def _require_positive(value: object, field: str) -> None:
    if _require_decimal(value, field) <= 0:
        raise ValueError(f"MarketSpec.{field} must be > 0, got {value}")


def _require_non_negative(value: object, field: str) -> None:
    if _require_decimal(value, field) < 0:
        raise ValueError(f"MarketSpec.{field} must be >= 0, got {value}")


def _require_count(value: object, field: str, minimum: int) -> None:
    if type(value) is not int:
        raise TypeError(f"MarketSpec.{field} must be int, got {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"MarketSpec.{field} must be >= {minimum}, got {value}")


# --- calendar and index ---------------------------------------------------------------------------


def _is_business_day(day: date, holidays: tuple[date, ...]) -> bool:
    return day.weekday() < _SATURDAY and day not in holidays


def _next_business_day(day: date, holidays: tuple[date, ...]) -> date:
    """Return the first weekday after ``day`` not in ``holidays``."""
    horizon = 7 * (len(holidays) + 1)  # holds more weekdays than there are holidays
    return next(
        candidate
        for candidate in (day + timedelta(days=offset) for offset in range(1, horizon + 1))
        if _is_business_day(candidate, holidays)
    )


def _on_or_before(spec: MarketSpec, day: date) -> date:
    """Return the latest business day at or before ``day``; ``first_session`` is one."""
    back = (day - spec.first_session).days
    return next(
        candidate
        for candidate in (day - timedelta(days=offset) for offset in range(back + 1))
        if _is_business_day(candidate, spec.holidays)
    )


def _instant_ns(moment: datetime) -> int:
    """Return an aware datetime as UTC integer nanoseconds."""
    return (moment - _EPOCH) // timedelta(microseconds=1) * 1000


def _local_ns(day: date, clock: time) -> int:
    """Return the UTC nanoseconds of a New York wall-clock time on ``day``."""
    return _instant_ns(datetime.combine(day, clock, tzinfo=ZoneInfo(_TIME_ZONE)))


def _sessions(spec: MarketSpec) -> tuple[TradingSession, ...]:
    """Return the table sessions: weekdays of ``[first_session, last_session]`` not holidays."""
    span = (spec.last_session - spec.first_session).days
    days = (spec.first_session + timedelta(days=offset) for offset in range(span + 1))
    return tuple(
        _session(day, early=day in spec.early_closes)
        for day in days
        if _is_business_day(day, spec.holidays)
    )


def _session(day: date, *, early: bool) -> TradingSession:
    close = _EARLY_CLOSE if early else _CLOSE
    return TradingSession(
        session_date=day,
        open_ns=_local_ns(day, _OPEN),
        close_ns=_local_ns(day, close),
        cutoff_ns=_local_ns(day, _CUTOFF),
        early_close=early,
    )


def _index_path(spec: MarketSpec, count: int) -> tuple[Decimal, ...]:
    """Return ``S_i`` per session: cents of ``index_start·exp(L_i)`` (§17 item 18)."""
    rng = random.Random(spec.seed)  # noqa: S311 — a reproducible fixture path, not cryptography
    start, drift, vol = float(spec.index_start), float(spec.daily_drift), float(spec.daily_vol)
    level = 0.0
    values: list[Decimal] = []
    for position in range(count):
        if position:
            level = level + drift + vol * rng.gauss(0.0, 1.0)
        values.append(_cents(start * math.exp(level)))
    return tuple(values)


def _cents(value: float) -> Decimal:
    """Return the float's exact value quantized to cents, half even; it must stay positive."""
    with localcontext(EXACT) as context:
        context.traps[Inexact] = False  # the one declared rounding of the index path
        cents = Decimal(value).quantize(_CENT, rounding=ROUND_HALF_EVEN)
    if cents <= 0:
        raise ValueError(f"MarketSpec index path reaches {cents}; the index must stay above 0")
    return cents


# --- listings -------------------------------------------------------------------------------------


def _rules(spec: MarketSpec, root: str) -> ProductRules:
    """Return the root's template rules with the spec's multiplier and deliverable units."""
    return replace(
        product_rules(root),
        premium_multiplier=spec.premium_multiplier,
        deliverable_units=spec.deliverable_units,
    )


def _expiries(spec: MarketSpec) -> tuple[date, ...]:
    """Return the expiry dates: ``first_session + k`` days moved back to a business day."""
    dates = tuple(
        _on_or_before(spec, spec.first_session + timedelta(days=offset))
        for offset in spec.weekly_dtes
    )
    if len(set(dates)) != len(dates):
        raise ValueError(f"MarketSpec.weekly_dtes {spec.weekly_dtes} repeat expiry dates {dates}")
    last_tenor = spec.rates[-1][0]
    late = [expiry for expiry in dates if dte(spec.first_session, expiry) >= last_tenor]
    if late:
        raise ValueError(
            f"MarketSpec.weekly_dtes: expiries {late} are not before the last rate tenor "
            f"({last_tenor} days from first_session)"
        )
    return dates


def _strikes(spec: MarketSpec, rules: ProductRules) -> tuple[Price, ...]:
    """Return ``c + j·step``, ``c = step·round_half_up(index_start/divisor/step)`` (§17 item 20)."""
    width = spec.strikes_each_side
    with localcontext(EXACT):
        spacing = rules.settlement_divisor * spec.strike_step
        steps, remainder = divmod(spec.index_start, spacing)
        centre = steps + 1 if 2 * remainder >= spacing else steps
        values = tuple((centre + j) * spec.strike_step for j in range(-width, width + 1))
    if values[0] <= 0:
        raise ValueError(f"MarketSpec strikes of {rules.root} must be > 0, lowest {values[0]}")
    return tuple(Price(value) for value in values)


def _listings(spec: MarketSpec) -> tuple[_Listing, ...]:
    """Return one listing per root and expiry."""
    expiries = _expiries(spec)
    listings: list[_Listing] = []
    for root in spec.roots:
        rules = _rules(spec, root)
        strikes = _strikes(spec, rules)
        listings.extend(_listing(spec, rules, strikes, expiry) for expiry in expiries)
    return tuple(listings)


def _listing(
    spec: MarketSpec, rules: ProductRules, strikes: tuple[Price, ...], expiry: date
) -> _Listing:
    close = _EARLY_CLOSE if expiry in spec.early_closes else _CLOSE
    expires_at_ns = _local_ns(expiry, close)
    terms = tuple(
        rules.terms(strike, right, expires_at_ns, expiry=expiry)
        for strike, right in product(strikes, OptionType)
    )
    return _Listing(rules, expiry, expires_at_ns, terms)


# --- generated tables -----------------------------------------------------------------------------


def _generated_tables(spec: MarketSpec, provenance: Provenance) -> _Tables:
    """Return every table the spec generates, before overrides and features."""
    sessions = _sessions(spec)
    levels = _index_path(spec, len(sessions))
    listings = _listings(spec)
    contracts = _contracts(listings, sessions[0], provenance)
    quotes = _quotes(spec, sessions, levels, listings, provenance)
    underlying = _underlying(spec, sessions, levels, provenance)
    settlements = _settlements(spec, sessions, levels, listings, provenance)
    return _Tables(
        sessions={session.session_date: session for session in sessions},
        contracts={row.version_id: row for row in contracts},
        quotes={row.observation_id: row for row in quotes},
        underlying={row.observation_id: row for row in underlying},
        activity={},
        settlements={row.observation_id: row for row in settlements},
        rates={row.observation_id: row for row in _rates(spec, sessions, provenance)},
        coverage={
            ("quotes", session.session_date): CoveragePartition(
                "quotes", session.session_date, CoverageState.COMPLETE, ""
            )
            for session in sessions
        },
    )


def _contracts(
    listings: Sequence[_Listing], first: TradingSession, provenance: Provenance
) -> Iterator[ContractVersion]:
    """Yield version 1 of every contract: listed, known and effective from the first open."""
    for listing in listings:
        yield from (
            ContractVersion(
                version_id=f"{terms.contract_id}@v1",
                terms=terms,
                root=listing.rules.root,
                underlying_id=listing.rules.underlying_id,
                listed_at_ns=first.open_ns,
                last_tradable_at_ns=listing.expires_at_ns,
                settlement_series=listing.rules.settlement_series,
                effective_from_ns=first.open_ns,
                effective_to_ns=None,
                known_from_ns=first.open_ns,
                provenance=provenance,
            )
            for terms in listing.terms
        )


def _quotes(
    spec: MarketSpec,
    sessions: Sequence[TradingSession],
    levels: Sequence[Decimal],
    listings: Sequence[_Listing],
    provenance: Provenance,
) -> Iterator[QuoteObservation]:
    """Yield the quotes of every session, quote slot and listing strictly before its expiry."""
    model = _QuoteModel(
        sigma=float(spec.sigma),
        tick=spec.tick,
        half_spread_abs=float(spec.half_spread_abs),
        half_spread_rel=float(spec.half_spread_rel),
        bid_size=spec.bid_size,
        ask_size=spec.ask_size,
        curve=DiscountCurve(tuple((tenor, bill_df(bey, tenor)) for tenor, bey in spec.rates)),
        provenance=provenance,
    )
    points = product(zip(sessions, levels, strict=True), QUOTE_SLOTS, listings)
    for (session, level), slot, listing in points:
        if slot_instant(session, slot) < listing.expires_at_ns:
            yield from _listing_quotes(model, listing, session, slot, level)


def _listing_quotes(
    model: _QuoteModel, listing: _Listing, session: TradingSession, slot: Slot, level: Decimal
) -> Iterator[QuoteObservation]:
    """Yield one slot's Black-76 quotes of one listing at ``F = S/DF``."""
    at_ns = slot_instant(session, slot)
    t = (listing.expires_at_ns - at_ns) / _NS_PER_YEAR
    df = model.curve.df(t * 365)
    if df is None:
        raise ValueError(f"no discount factor {t * 365} days ahead: past the last rate tenor")
    with localcontext(EXACT):
        spot = float(level / listing.rules.settlement_divisor)
    forward = spot / df
    suffix = f":{session.session_date.isoformat()}:{slot.value}"
    for terms in listing.terms:
        mid = black76(forward, float(terms.strike.value), t, df, model.sigma, terms.option_type)
        bid, bid_size, ask = _sides(model, mid)
        yield QuoteObservation(
            observation_id=f"q:{terms.contract_id}{suffix}",
            contract_id=terms.contract_id,
            bid=bid,
            ask=ask,
            bid_size=bid_size,
            ask_size=model.ask_size,
            observed_at_ns=at_ns,
            available_at_ns=at_ns,
            session_date=session.session_date,
            provenance=model.provenance,
        )


def _sides(model: _QuoteModel, mid: float) -> tuple[Decimal, int, Decimal]:
    """Return (bid, bid size, ask): ``mid ∓ h`` on the tick grid, the bid down, the ask up.

    ``h = max(half_spread_abs, half_spread_rel·mid)`` in float64; the rounding is exact on the
    floats' values, so the sides never lock or cross. A bid ``<= 0`` is 0 with size 0 (NO_BID).
    """
    half = max(model.half_spread_abs, model.half_spread_rel * mid)
    low, high, tick = Decimal(mid - half), Decimal(mid + half), model.tick
    with localcontext(EXACT):
        bid = (low // tick) * tick if low > 0 else tick * 0
        ask = (high // tick) * tick
        if ask < high:
            ask += tick
    if bid >= ask:
        raise ValueError(
            f"MarketSpec.half_spread_abs is too small to separate bid and ask at mid {mid!r}"
        )
    return bid, (model.bid_size if bid > 0 else 0), ask


def _underlying(
    spec: MarketSpec,
    sessions: Sequence[TradingSession],
    levels: Sequence[Decimal],
    provenance: Provenance,
) -> Iterator[UnderlyingObservation]:
    """Yield the prints and official close of every underlying of ``roots``: ``S_i/divisor``."""
    by_underlying = {rules.underlying_id: rules for rules in map(product_rules, spec.roots)}
    points = product(zip(sessions, levels, strict=True), by_underlying.values())
    for (session, level), rules in points:
        with localcontext(EXACT):
            value = Price(level / rules.settlement_divisor)
        yield from _index_rows(session, rules.underlying_id, value, provenance)


def _index_rows(
    session: TradingSession, underlying_id: str, value: Price, provenance: Provenance
) -> tuple[UnderlyingObservation, ...]:
    """Return one session's INDEX_VALUE prints at the quote slots and its OFFICIAL_CLOSE."""
    day = session.session_date.isoformat()
    index_value, official_close = UnderlyingField.INDEX_VALUE, UnderlyingField.OFFICIAL_CLOSE
    prints = tuple(
        UnderlyingObservation(
            observation_id=f"u:{underlying_id}:{index_value.value}:{day}:{slot.value}",
            underlying_id=underlying_id,
            field=index_value,
            value=value,
            observed_at_ns=slot_instant(session, slot),
            available_at_ns=slot_instant(session, slot),
            session_date=session.session_date,
            provenance=provenance,
        )
        for slot in QUOTE_SLOTS
    )
    official = UnderlyingObservation(
        observation_id=f"u:{underlying_id}:{official_close.value}:{day}:{Slot.CLOSE.value}",
        underlying_id=underlying_id,
        field=official_close,
        value=value,
        observed_at_ns=session.close_ns,
        available_at_ns=_local_ns(session.session_date, _PUBLICATION),
        session_date=session.session_date,
        provenance=provenance,
    )
    return (*prints, official)


def _settlements(
    spec: MarketSpec,
    sessions: Sequence[TradingSession],
    levels: Sequence[Decimal],
    listings: Sequence[_Listing],
    provenance: Provenance,
) -> Iterator[SettlementObservation]:
    """Yield the final settlement of every listing that expires on a table session."""
    level_on = {s.session_date: level for s, level in zip(sessions, levels, strict=True)}
    for listing in listings:
        if listing.expiry in level_on:
            yield SettlementObservation(
                observation_id=_settlement_id(listing.rules.settlement_series, listing.expiry),
                settlement_series=listing.rules.settlement_series,
                session_date=listing.expiry,
                value=settlement_price(listing.rules, Price(level_on[listing.expiry])),
                available_at_ns=_local_ns(listing.expiry, _PUBLICATION),
                payable_date=_next_business_day(listing.expiry, spec.holidays),
                final=True,
                correction_version=0,
                provenance=provenance,
            )


def _settlement_id(series: str, day: date) -> str:
    return f"s:{series}:{day.isoformat()}:c0"


def _rates(
    spec: MarketSpec, sessions: Sequence[TradingSession], provenance: Provenance
) -> Iterator[RateObservation]:
    """Yield each session's curve: dated the previous session, published at its DEC."""
    first = sessions[0].session_date - timedelta(days=1)
    dated = (first, *(session.session_date for session in sessions[:-1]))
    points = product(zip(sessions, dated, strict=True), spec.rates)
    for (session, observed), (tenor, bey) in points:
        yield RateObservation(
            observation_id=f"r:{CMT_CURVE_ID}:{tenor}:{observed.isoformat()}",
            curve_id=CMT_CURVE_ID,
            tenor_days=tenor,
            bey=bey,
            observation_date=observed,
            available_at_ns=slot_times(session).dec,
            provenance=provenance,
        )


def _freeze(tables: _Tables) -> FrozenDataset:
    """Seal the tables, without features, as a synthetic fixture under the public license."""
    return FrozenDataset.freeze(
        sessions=tuple(tables.sessions.values()),
        contracts=tuple(tables.contracts.values()),
        quotes=tuple(tables.quotes.values()),
        underlying=tuple(tables.underlying.values()),
        activity=tuple(tables.activity.values()),
        settlements=tuple(tables.settlements.values()),
        rates=tuple(tables.rates.values()),
        features=(),
        coverage=tuple(tables.coverage.values()),
        fidelity=FidelityClass.SYNTHETIC_FIXTURE,
        limitations=(),
        calendar_version=CALENDAR_VERSION,
        product_rules_version=PRODUCT_RULES_VERSION,
        feature_versions=tuple(FEATURE_VERSIONS.items()),
        license_policy_id=SYNTHETIC_LICENSE_POLICY_ID,
    )


# --- overrides ------------------------------------------------------------------------------------


def _apply(  # noqa: PLR0912 — one case per override kind (ADR 0002 §5)
    tables: _Tables, override: Override, provenance: Provenance
) -> None:
    """Apply one override; a target that does not exist is ``ValueError``."""
    match override:
        case QuotePin():
            _pin_quotes(tables, override)
        case QuoteDrop():
            _drop_quotes(tables, override)
        case QuoteStale():
            _age_quotes(tables, override)
        case LateAvailability():
            _delay(tables, override)
        case UnderlyingDrop():
            _drop_index(tables, override)
        case SettlementPin():
            _pin_settlement(tables, override)
        case SettlementDrop():
            _drop_settlement(tables, override)
        case RateDrop():
            _drop_rates(tables, override)
        case ActivityPin():
            _pin_activity(tables, override, provenance)
        case TermsRevision():
            _revise_terms(tables, override)
        case CoverageStatus():
            _set_coverage(tables, override)
        case EarlyClose():
            _close_early(tables, override)
        case _:
            raise TypeError(f"MarketSpec.overrides: unknown override {type(override).__name__}")


def _targets[R](
    table: dict[str, R], record_ids: Sequence[str], override: Override
) -> tuple[str, ...]:
    """Return the ids after checking that every one is in the table."""
    missing = [record_id for record_id in record_ids if record_id not in table]
    if missing:
        raise ValueError(f"{type(override).__name__} targets {missing}, which do not exist")
    return tuple(record_ids)


def _table_session(tables: _Tables, day: date, override: Override) -> TradingSession:
    session = tables.sessions.get(day)
    if session is None:
        raise ValueError(f"{type(override).__name__} targets {day}, which is not a table session")
    return session


def _quote_targets(tables: _Tables, override: QuotePin | QuoteDrop | QuoteStale) -> tuple[str, ...]:
    prefix = f"q:{override.contract}:{override.session.isoformat()}:"
    return _targets(tables.quotes, [prefix + slot.value for slot in override.slots], override)


def _pin_quotes(tables: _Tables, pin: QuotePin) -> None:
    for record_id in _quote_targets(tables, pin):
        tables.quotes[record_id] = replace(
            tables.quotes[record_id],
            bid=pin.bid,
            ask=pin.ask,
            bid_size=pin.bid_size,
            ask_size=pin.ask_size,
        )


def _drop_quotes(tables: _Tables, drop: QuoteDrop) -> None:
    for record_id in _quote_targets(tables, drop):
        del tables.quotes[record_id]


def _age_quotes(tables: _Tables, stale: QuoteStale) -> None:
    for record_id in _quote_targets(tables, stale):
        row = tables.quotes[record_id]
        observed = row.observed_at_ns - stale.seconds * _NS_PER_SECOND
        tables.quotes[record_id] = replace(row, observed_at_ns=observed)


def _delay(tables: _Tables, late: LateAvailability) -> None:
    at_ns = _instant_ns(late.available_at)
    found = (
        _delay_in(tables.quotes, late, at_ns)
        or _delay_in(tables.underlying, late, at_ns)
        or _delay_in(tables.activity, late, at_ns)
        or _delay_in(tables.settlements, late, at_ns)
        or _delay_in(tables.rates, late, at_ns)
    )
    if not found:
        raise ValueError(f"LateAvailability targets {late.selector!r}, which does not exist")


def _delay_in[
    R: (
        QuoteObservation,
        UnderlyingObservation,
        ActivityObservation,
        SettlementObservation,
        RateObservation,
    )
](table: dict[str, R], late: LateAvailability, at_ns: int) -> bool:
    """Delay the table's record ``late.selector``; False when the table has none."""
    row = table.get(late.selector)
    if row is None:
        return False
    if at_ns <= row.available_at_ns:
        raise ValueError(
            f"LateAvailability of {late.selector!r}: {late.available_at.isoformat()} is not "
            "later than its current availability"
        )
    table[late.selector] = replace(row, available_at_ns=at_ns)
    return True


def _drop_index(tables: _Tables, drop: UnderlyingDrop) -> None:
    prefix = f"u:{drop.underlying_id}:{drop.field.value}:{drop.session.isoformat()}:"
    record_ids = [prefix + slot.value for slot in drop.slots]
    for record_id in _targets(tables.underlying, record_ids, drop):
        del tables.underlying[record_id]


def _pin_settlement(tables: _Tables, pin: SettlementPin) -> None:
    (record_id,) = _targets(tables.settlements, [_settlement_id(pin.series, pin.session)], pin)
    tables.settlements[record_id] = replace(tables.settlements[record_id], value=pin.value)


def _drop_settlement(tables: _Tables, drop: SettlementDrop) -> None:
    (record_id,) = _targets(tables.settlements, [_settlement_id(drop.series, drop.session)], drop)
    del tables.settlements[record_id]


def _drop_rates(tables: _Tables, drop: RateDrop) -> None:
    """Remove the rates published at the session's DEC (one tenor, or all when None)."""
    decision = slot_times(_table_session(tables, drop.session, drop)).dec
    record_ids = [
        record_id
        for record_id, row in tables.rates.items()
        if row.available_at_ns == decision and drop.tenor_days in (None, row.tenor_days)
    ]
    if not record_ids:
        raise ValueError(
            f"RateDrop targets no rate published at {drop.session}'s DEC "
            f"(tenor_days {drop.tenor_days})"
        )
    for record_id in record_ids:
        del tables.rates[record_id]


def _pin_activity(tables: _Tables, pin: ActivityPin, provenance: Provenance) -> None:
    session = _table_session(tables, pin.session, pin)
    if f"{pin.contract}@v1" not in tables.contracts:
        raise ValueError(f"ActivityPin targets contract {pin.contract!r}, which does not exist")
    at_ns = slot_instant(session, pin.slot)
    record_id = f"a:{pin.contract}:{pin.session.isoformat()}:{pin.slot.value}"
    tables.activity[record_id] = ActivityObservation(
        record_id, pin.contract, pin.cumulative_volume, at_ns, at_ns, provenance
    )


def _revise_terms(tables: _Tables, revision: TermsRevision) -> None:
    """End the contract's current version at the session open; add the next with new units."""
    session = _table_session(tables, revision.session, revision)
    current = next(
        (
            row
            for row in tables.contracts.values()
            if row.terms.contract_id == revision.contract and row.effective_to_ns is None
        ),
        None,
    )
    if current is None:
        raise ValueError(f"TermsRevision targets {revision.contract!r}, which does not exist")
    if session.open_ns > current.terms.expires_at_ns:
        raise ValueError(
            f"TermsRevision at {revision.session} is after {revision.contract} expires"
        )
    asset, units = current.underlying_id, revision.deliverable_units
    deliverable = Deliverable(f"{asset}:{units}", (DeliverableComponent(asset, units),), ZERO_USD)
    number = int(current.version_id.rpartition("@v")[2]) + 1
    tables.contracts[current.version_id] = replace(current, effective_to_ns=session.open_ns)
    revised = replace(
        current,
        version_id=f"{revision.contract}@v{number}",
        terms=replace(current.terms, deliverable=deliverable),
        effective_from_ns=session.open_ns,
        effective_to_ns=None,
        known_from_ns=session.open_ns,
    )
    tables.contracts[revised.version_id] = revised


def _set_coverage(tables: _Tables, status: CoverageStatus) -> None:
    key = (status.table, status.session)
    row = tables.coverage.get(key)
    if row is None:
        raise ValueError(
            f"CoverageStatus targets no {status.table!r} partition on {status.session}"
        )
    tables.coverage[key] = replace(row, status=status.status, note=status.note)


def _close_early(tables: _Tables, early: EarlyClose) -> None:
    session = _table_session(tables, early.session, early)
    if session.early_close:
        raise ValueError(f"EarlyClose targets {early.session}, which already closes early")
    close = _local_ns(early.session, _EARLY_CLOSE)
    tables.sessions[early.session] = replace(session, close_ns=close, early_close=True)
