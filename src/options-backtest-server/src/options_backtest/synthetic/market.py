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

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Final

from options_backtest.data.manifest import FrozenDataset
from options_backtest.data.records import CoverageState, UnderlyingField
from options_backtest.money import Price
from options_backtest.reference.calendars import Slot

GENERATOR_VERSION: Final = "synthetic_market_v1"
CALENDAR_VERSION: Final = "synthetic_weekdays_v1"


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
    raise NotImplementedError
