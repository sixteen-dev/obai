"""Canonical market and reference records: the ADR 0002 §2 subset of design §8.2.

Every record is a frozen, slotted dataclass. Instants are UTC integer nanoseconds (``*_ns``);
session, observation and payable dates are exchange-local ``datetime.date`` values; prices are
exact ``Decimal`` or ``Price``. No record holds a float. Records taken from a source carry a
``Provenance``; the derived tables (sessions, features, coverage) do not.

Identifier formats (ADR 0002 §17): contract ``{root}:{YYYY-MM-DD}:{C|P}:{strike}``; version
``{contract_id}@v{n}``; quote ``q:{contract_id}:{session}:{slot}``; underlying
``u:{underlying_id}:{field}:{session}:{slot}``; settlement
``s:{series}:{session}:c{correction}``; rate ``r:{curve_id}:{tenor_days}:{observation_date}``;
activity ``a:{contract_id}:{session}:{slot}``; feature ``{underlying_id}:{name}``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from options_backtest.models.market import ContractTerms, Quote
from options_backtest.money import Price


class FidelityClass(StrEnum):
    """Data fidelity of a dataset segment (design §7.4); a run's fidelity is the minimum."""

    SYNTHETIC_FIXTURE = "synthetic_fixture"
    HISTORICAL_SNAPSHOT = "historical_snapshot"
    HISTORICAL_QUOTE_EVENTS = "historical_quote_events"

    @property
    def rank(self) -> int:
        """Return the class's order: SYNTHETIC_FIXTURE 0 < HISTORICAL_SNAPSHOT 1 < EVENTS 2."""
        raise NotImplementedError


class QuoteStatus(StrEnum):
    """Validity of one raw quote observation (design §8.4)."""

    VALID = "valid"
    LOCKED = "locked"
    NO_BID = "no_bid"
    CROSSED = "crossed"
    ZERO_ASK = "zero_ask"
    NEGATIVE = "negative"


class UnderlyingField(StrEnum):
    """Meaning of an underlying observation's value."""

    INDEX_VALUE = "index_value"
    OFFICIAL_CLOSE = "official_close"


class CoverageState(StrEnum):
    """Completeness of one table partition for one session (design §8.2 ``CoveragePartition``)."""

    COMPLETE = "complete"
    GAP = "gap"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a record came from (design §8.2).

    Synthetic records use ``source_id="synthetic"``, ``source_schema_version="market_spec_v1"``,
    the sha256 of the canonical JSON of their ``MarketSpec`` as ``raw_object_digest``,
    ``normalizer_version`` = the generator version and ``revision_id="0"``.

    Attributes:
        source_id: Source identifier.
        source_schema_version: Version of the source's schema.
        raw_object_digest: sha256 hex of the immutable raw object the record came from.
        normalizer_version: Version of the code that normalized it.
        revision_id: Source revision of the record.

    """

    source_id: str
    source_schema_version: str
    raw_object_digest: str
    normalizer_version: str
    revision_id: str


@dataclass(frozen=True, slots=True)
class ContractVersion:
    """One effective- and knowledge-dated version of a listed option contract.

    A revision keeps ``terms.contract_id`` and adds a version whose ``effective_from_ns`` ends
    the previous version's interval (``effective_to_ns``).

    Attributes:
        version_id: ``{contract_id}@v{n}``; n = 1 for the listing, +1 per revision.
        terms: Economic terms; ``terms.contract_id`` is the contract id.
        root: Option root, such as ``SPXW``.
        underlying_id: Underlying index, such as ``SPX``.
        listed_at_ns: First instant the contract is a candidate.
        last_tradable_at_ns: Last instant an order may trade it (PM: the expiry date's close).
        settlement_series: Series whose ``SettlementObservation`` settles it.
        effective_from_ns: Start of the version's economic effect.
        effective_to_ns: End (exclusive) of its effect; None while it is current.
        known_from_ns: First instant the version could be known.
        provenance: Source of the record.

    """

    version_id: str
    terms: ContractTerms
    root: str
    underlying_id: str
    listed_at_ns: int
    last_tradable_at_ns: int
    settlement_series: str
    effective_from_ns: int
    effective_to_ns: int | None
    known_from_ns: int
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class QuoteObservation:
    """One raw two-sided quote of one contract; invalid states are kept, not repaired.

    Attributes:
        observation_id: ``q:{contract_id}:{session}:{slot}``.
        contract_id: Quoted contract.
        bid: Raw bid; may be zero or negative.
        ask: Raw ask; may be zero or negative.
        bid_size: Raw displayed bid size in contracts.
        ask_size: Raw displayed ask size in contracts.
        observed_at_ns: Instant the quote describes.
        available_at_ns: First instant it could inform a decision.
        session_date: Session the observation belongs to.
        provenance: Source of the record.

    """

    observation_id: str
    contract_id: str
    bid: Decimal
    ask: Decimal
    bid_size: int
    ask_size: int
    observed_at_ns: int
    available_at_ns: int
    session_date: date
    provenance: Provenance

    def status(self) -> QuoteStatus:
        """Return the status, checked in order and first match wins.

        NEGATIVE if any price or size is negative; ZERO_ASK if ask == 0; CROSSED if bid > ask;
        LOCKED if bid == ask; NO_BID if bid == 0; otherwise VALID.

        Returns:
            The quote's status.

        """
        raise NotImplementedError

    def quote(self) -> Quote:
        """Return the prices as a ``Quote``; only for VALID, LOCKED and NO_BID.

        Returns:
            ``Quote(Price(bid), Price(ask))``.

        Raises:
            ValueError: If the status is CROSSED, ZERO_ASK or NEGATIVE.

        """
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class UnderlyingObservation:
    """One raw underlying index value.

    Attributes:
        observation_id: ``u:{underlying_id}:{field}:{session}:{slot}``; the official close uses
            slot ``CLOSE``.
        underlying_id: Underlying index, such as ``SPX`` or ``XSP``.
        field: Meaning of ``value``.
        value: Index value in index points.
        observed_at_ns: Instant the value describes.
        available_at_ns: First instant it could inform a decision.
        session_date: Session the observation belongs to.
        provenance: Source of the record.

    """

    observation_id: str
    underlying_id: str
    field: UnderlyingField
    value: Price
    observed_at_ns: int
    available_at_ns: int
    session_date: date
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class ActivityObservation:
    """Cumulative traded volume of one contract through an instant of its session.

    ``measured_through_ns`` plays the part of ``observed_at_ns`` in the as-of view and fixes the
    observation's session.

    Attributes:
        observation_id: ``a:{contract_id}:{session}:{slot}``.
        contract_id: Contract measured.
        cumulative_volume: Contracts traded in the session through ``measured_through_ns``.
        measured_through_ns: End of the measured interval.
        available_at_ns: First instant it could inform a decision.
        provenance: Source of the record.

    """

    observation_id: str
    contract_id: str
    cumulative_volume: int
    measured_through_ns: int
    available_at_ns: int
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class SettlementObservation:
    """One official settlement value of a settlement series for one expiry session.

    Attributes:
        observation_id: ``s:{series}:{session}:c{correction_version}``.
        settlement_series: Series, such as ``SPX_PM``.
        session_date: Expiry session the value settles.
        value: Settlement value in the deliverable asset's price units.
        available_at_ns: Publication instant.
        payable_date: Informational cash date: the next weekday that is not a holiday. The
            engine settles on the dataset's next session instead.
        final: Whether the value is final (the engine uses only final values).
        correction_version: 0 for the first publication, +1 per correction.
        provenance: Source of the record.

    """

    observation_id: str
    settlement_series: str
    session_date: date
    value: Price
    available_at_ns: int
    payable_date: date
    final: bool
    correction_version: int
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class RateObservation:
    """One Treasury bill-based CMT point, quoted as a bond-equivalent yield (design §13.1).

    Attributes:
        observation_id: ``r:{curve_id}:{tenor_days}:{observation_date}``.
        curve_id: Curve, ``UST_CMT``.
        tenor_days: Tenor in calendar days, > 0.
        bey: Bond-equivalent yield as a decimal ratio (0.05 is 5%).
        observation_date: Date the rate is dated.
        available_at_ns: First instant it could inform a decision (the next session's DEC).
        provenance: Source of the record.

    """

    observation_id: str
    curve_id: str
    tenor_days: int
    bey: Decimal
    observation_date: date
    available_at_ns: int
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class FeatureObservation:
    """One session's value of one historical feature, computed at dataset creation.

    Attributes:
        feature_id: ``{underlying_id}:{name}``, such as ``SPX:underlying.return_20s``.
        feature_version: Version of the feature's definition.
        session_date: Session whose CLOSE snapshot produced the value.
        value: The value (``Decimal(float)``); None when unavailable, never zero.
        max_input_available_at_ns: Latest ``available_at_ns`` of every input read (the
            session's ``close_ns`` when none was); the value is visible only from then.
        warmup_count: Consecutive valid prior inputs the window saw, capped at its length.
        missing_reason: Why ``value`` is None; None when it is present.
        input_digest: sha256 hex of the canonical JSON of the input observation ids, sorted.

    """

    feature_id: str
    feature_version: str
    session_date: date
    value: Decimal | None
    max_input_available_at_ns: int
    warmup_count: int
    missing_reason: str | None
    input_digest: str


@dataclass(frozen=True, slots=True)
class TradingSession:
    """One common trading session (design §10.1); the engine never converts time zones.

    Attributes:
        session_date: Exchange-local date.
        open_ns: 09:30 America/New_York.
        close_ns: Common close: 16:00 America/New_York, 13:00 on an early close.
        cutoff_ns: Ledger cutoff: 23:59:59 America/New_York.
        early_close: Whether the session closes early.

    """

    session_date: date
    open_ns: int
    close_ns: int
    cutoff_ns: int
    early_close: bool


@dataclass(frozen=True, slots=True)
class CoveragePartition:
    """Completeness of one table for one session.

    The engine reads only table ``"quotes"`` (the option chain): COMPLETE proceeds, GAP is a
    disclosed missed opportunity, UNKNOWN or an absent partition invalidates the run.

    Attributes:
        table: Dataset table name.
        session_date: Session covered.
        status: Completeness.
        note: Free-text explanation; "" when none.

    """

    table: str
    session_date: date
    status: CoverageState
    note: str
