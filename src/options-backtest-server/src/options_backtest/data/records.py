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

Every record validates its fields on construction and raises ``TypeError`` or ``ValueError``
naming the field. An id must agree with the fields it spells (the slot suffix excepted), so a
table sorted by id keeps each contract's, session's or series' rows contiguous; the as-of view
relies on that to find them by prefix.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Final

from options_backtest.models.market import (
    ContractTerms,
    OptionType,
    Quote,
    require_id,
    require_int,
    require_type,
)
from options_backtest.money import EXACT, Price

_NS_LIMIT: Final = 2**63
"""Instants lie in ``[0, 2**63)``: the int64 nanosecond range of columnar storage."""
_SHA256_HEX: Final = frozenset("0123456789abcdef")
_SHA256_LENGTH: Final = 64
_CONTRACT_ID_PARTS: Final = 4


class FidelityClass(StrEnum):
    """Data fidelity of a dataset segment (design §7.4); a run's fidelity is the minimum."""

    SYNTHETIC_FIXTURE = "synthetic_fixture"
    HISTORICAL_SNAPSHOT = "historical_snapshot"
    HISTORICAL_QUOTE_EVENTS = "historical_quote_events"

    @property
    def rank(self) -> int:
        """Return the class's order: SYNTHETIC_FIXTURE 0 < HISTORICAL_SNAPSHOT 1 < EVENTS 2."""
        return tuple(type(self)).index(self)


class QuoteStatus(StrEnum):
    """Validity of one raw quote observation (design §8.4)."""

    VALID = "valid"
    LOCKED = "locked"
    NO_BID = "no_bid"
    CROSSED = "crossed"
    ZERO_ASK = "zero_ask"
    NEGATIVE = "negative"


_QUOTABLE: Final = frozenset({QuoteStatus.VALID, QuoteStatus.LOCKED, QuoteStatus.NO_BID})


class UnderlyingField(StrEnum):
    """Meaning of an underlying observation's value."""

    INDEX_VALUE = "index_value"
    OFFICIAL_CLOSE = "official_close"


class CoverageState(StrEnum):
    """Completeness of one table partition for one session (design §8.2 ``CoveragePartition``)."""

    COMPLETE = "complete"
    GAP = "gap"
    UNKNOWN = "unknown"


def require_sha256(value: object, field: str) -> None:
    """Reject a value that is not a lower-case sha256 hex digest; a record field guard.

    Args:
        value: Field value.
        field: Field name used in the message.

    Raises:
        TypeError: If ``value`` is not a ``str``.
        ValueError: If it is not 64 lower-case hex digits.

    """
    if not isinstance(value, str):
        raise TypeError(f"{field} must be str, got {type(value).__name__}")
    if len(value) != _SHA256_LENGTH or not set(value) <= _SHA256_HEX:
        raise ValueError(f"{field} must be a lower-case sha256 hex digest, got {value!r}")


def _require_ns(value: object, field: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{field} must be int nanoseconds, got {type(value).__name__}")
    if not 0 <= value < _NS_LIMIT:
        raise ValueError(f"{field} must be UTC nanoseconds in [0, 2**63), got {value}")


def _require_date(value: object, field: str) -> None:
    if type(value) is not date:
        raise TypeError(f"{field} must be exactly date, got {type(value).__name__}")


def _require_bool(value: object, field: str) -> None:
    if type(value) is not bool:
        raise TypeError(f"{field} must be bool, got {type(value).__name__}")


def _require_count(value: object, field: str, minimum: int) -> None:
    if type(value) is not int:
        raise TypeError(f"{field} must be int, got {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{field} must be >= {minimum}, got {value}")


def _require_finite_decimal(value: object, field: str) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(f"{field} must be exactly Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{field} must be finite, got {value}")
    return value


def _require_raw_price(value: object, field: str) -> None:
    """Accept a raw quote side: a finite decimal of either sign within DECIMAL(24,9)."""
    magnitude = _require_finite_decimal(value, field).copy_abs()
    try:
        Price(magnitude)
    except ValueError as e:
        raise ValueError(f"{field} must fit DECIMAL(24,9): {e}") from e


def _require_prefix(value: str, prefix: str, field: str) -> None:
    if not value.startswith(prefix) or len(value) == len(prefix):
        raise ValueError(f"{field} {value!r} must be {prefix!r} followed by its slot")


def _require_exact_id(value: str, expected: str, field: str) -> None:
    if value != expected:
        raise ValueError(f"{field} {value!r} must be {expected!r}")


def _require_not_before(available_at_ns: int, observed_at_ns: int, field: str) -> None:
    if available_at_ns < observed_at_ns:
        raise ValueError(
            f"{field} {available_at_ns} precedes the observed instant {observed_at_ns}"
        )


def _require_contract_id(terms: ContractTerms, root: str) -> None:
    """Check ``{root}:{YYYY-MM-DD}:{C|P}:{strike}`` against the version's root and terms."""
    field = "ContractVersion.terms.contract_id"
    right = "C" if terms.option_type is OptionType.CALL else "P"
    tail = [right, format(terms.strike.value.normalize(EXACT), "f")]
    parts = terms.contract_id.split(":")
    if len(parts) != _CONTRACT_ID_PARTS or parts[0] != root or parts[2:] != tail:
        raise ValueError(
            f"{field} {terms.contract_id!r} must be '{root}:YYYY-MM-DD:{tail[0]}:{tail[1]}' "
            f"(ContractVersion.root {root!r})"
        )
    try:
        canonical = date.fromisoformat(parts[1]).isoformat() == parts[1]
    except ValueError as e:
        raise ValueError(f"{field} {terms.contract_id!r} has no valid expiry date") from e
    if not canonical:
        raise ValueError(f"{field} {terms.contract_id!r} must spell its expiry YYYY-MM-DD")


def _require_version_id(version_id: object, contract_id: str) -> None:
    field = "ContractVersion.version_id"
    require_id(version_id, field)
    head, marker, number = str(version_id).rpartition("@v")
    numbered = number.isascii() and number.isdigit() and not number.startswith("0")
    if head != contract_id or not marker or not numbered:
        raise ValueError(f"{field} {version_id!r} must be '{contract_id}@v{{n}}' with n >= 1")


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

    def __post_init__(self) -> None:
        """Require non-empty ids and a sha256 raw-object digest."""
        require_id(self.source_id, "Provenance.source_id")
        require_id(self.source_schema_version, "Provenance.source_schema_version")
        require_sha256(self.raw_object_digest, "Provenance.raw_object_digest")
        require_id(self.normalizer_version, "Provenance.normalizer_version")
        require_id(self.revision_id, "Provenance.revision_id")


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

    def __post_init__(self) -> None:
        """Validate types, ids and ``listed <= last tradable <= expiry``, ``from < to``."""
        require_type(self.terms, ContractTerms, "ContractVersion.terms")
        require_id(self.root, "ContractVersion.root")
        require_id(self.underlying_id, "ContractVersion.underlying_id")
        require_id(self.settlement_series, "ContractVersion.settlement_series")
        _require_ns(self.listed_at_ns, "ContractVersion.listed_at_ns")
        _require_ns(self.last_tradable_at_ns, "ContractVersion.last_tradable_at_ns")
        _require_ns(self.effective_from_ns, "ContractVersion.effective_from_ns")
        if self.effective_to_ns is not None:
            _require_ns(self.effective_to_ns, "ContractVersion.effective_to_ns")
        _require_ns(self.known_from_ns, "ContractVersion.known_from_ns")
        require_type(self.provenance, Provenance, "ContractVersion.provenance")
        _require_contract_id(self.terms, self.root)
        _require_version_id(self.version_id, self.terms.contract_id)
        if self.listed_at_ns > self.last_tradable_at_ns:
            raise ValueError("ContractVersion.listed_at_ns is after last_tradable_at_ns")
        if self.last_tradable_at_ns > self.terms.expires_at_ns:
            raise ValueError("ContractVersion.last_tradable_at_ns is after terms.expires_at_ns")
        if self.effective_to_ns is not None and self.effective_to_ns <= self.effective_from_ns:
            raise ValueError("ContractVersion.effective_to_ns must be after effective_from_ns")


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

    def __post_init__(self) -> None:
        """Validate types, DECIMAL(24,9) sides of any sign, the id and availability."""
        require_id(self.observation_id, "QuoteObservation.observation_id")
        require_id(self.contract_id, "QuoteObservation.contract_id")
        _require_raw_price(self.bid, "QuoteObservation.bid")
        _require_raw_price(self.ask, "QuoteObservation.ask")
        require_int(self.bid_size, "QuoteObservation.bid_size")
        require_int(self.ask_size, "QuoteObservation.ask_size")
        _require_ns(self.observed_at_ns, "QuoteObservation.observed_at_ns")
        _require_ns(self.available_at_ns, "QuoteObservation.available_at_ns")
        _require_date(self.session_date, "QuoteObservation.session_date")
        require_type(self.provenance, Provenance, "QuoteObservation.provenance")
        prefix = f"q:{self.contract_id}:{self.session_date.isoformat()}:"
        _require_prefix(self.observation_id, prefix, "QuoteObservation.observation_id")
        _require_not_before(
            self.available_at_ns, self.observed_at_ns, "QuoteObservation.available_at_ns"
        )

    def status(self) -> QuoteStatus:
        """Return the status, checked in order and first match wins.

        NEGATIVE if any price or size is negative; ZERO_ASK if ask == 0; CROSSED if bid > ask;
        LOCKED if bid == ask; NO_BID if bid == 0; otherwise VALID.

        Returns:
            The quote's status.

        """
        if min(self.bid, self.ask) < 0 or min(self.bid_size, self.ask_size) < 0:
            return QuoteStatus.NEGATIVE
        if self.ask == 0:
            return QuoteStatus.ZERO_ASK
        if self.bid > self.ask:
            return QuoteStatus.CROSSED
        if self.bid == self.ask:
            return QuoteStatus.LOCKED
        if self.bid == 0:
            return QuoteStatus.NO_BID
        return QuoteStatus.VALID

    def quote(self) -> Quote:
        """Return the prices as a ``Quote``; only for VALID, LOCKED and NO_BID.

        Returns:
            ``Quote(Price(bid), Price(ask))``.

        Raises:
            ValueError: If the status is CROSSED, ZERO_ASK or NEGATIVE.

        """
        status = self.status()
        if status not in _QUOTABLE:
            raise ValueError(
                f"{self.observation_id} is {status.value}: only valid, locked and no_bid "
                "observations are quotes"
            )
        return Quote(Price(self.bid), Price(self.ask))


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

    def __post_init__(self) -> None:
        """Validate types, the id and availability."""
        require_id(self.observation_id, "UnderlyingObservation.observation_id")
        require_id(self.underlying_id, "UnderlyingObservation.underlying_id")
        require_type(self.field, UnderlyingField, "UnderlyingObservation.field")
        require_type(self.value, Price, "UnderlyingObservation.value")
        _require_ns(self.observed_at_ns, "UnderlyingObservation.observed_at_ns")
        _require_ns(self.available_at_ns, "UnderlyingObservation.available_at_ns")
        _require_date(self.session_date, "UnderlyingObservation.session_date")
        require_type(self.provenance, Provenance, "UnderlyingObservation.provenance")
        session = self.session_date.isoformat()
        prefix = f"u:{self.underlying_id}:{self.field.value}:{session}:"
        _require_prefix(self.observation_id, prefix, "UnderlyingObservation.observation_id")
        _require_not_before(
            self.available_at_ns, self.observed_at_ns, "UnderlyingObservation.available_at_ns"
        )


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

    def __post_init__(self) -> None:
        """Validate types, a non-negative volume, the id and availability."""
        require_id(self.observation_id, "ActivityObservation.observation_id")
        require_id(self.contract_id, "ActivityObservation.contract_id")
        _require_count(self.cumulative_volume, "ActivityObservation.cumulative_volume", 0)
        _require_ns(self.measured_through_ns, "ActivityObservation.measured_through_ns")
        _require_ns(self.available_at_ns, "ActivityObservation.available_at_ns")
        require_type(self.provenance, Provenance, "ActivityObservation.provenance")
        prefix = f"a:{self.contract_id}:"
        _require_prefix(self.observation_id, prefix, "ActivityObservation.observation_id")
        _require_not_before(
            self.available_at_ns, self.measured_through_ns, "ActivityObservation.available_at_ns"
        )


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

    def __post_init__(self) -> None:
        """Validate types, the id and a payable date after the session."""
        require_id(self.observation_id, "SettlementObservation.observation_id")
        require_id(self.settlement_series, "SettlementObservation.settlement_series")
        _require_date(self.session_date, "SettlementObservation.session_date")
        require_type(self.value, Price, "SettlementObservation.value")
        _require_ns(self.available_at_ns, "SettlementObservation.available_at_ns")
        _require_date(self.payable_date, "SettlementObservation.payable_date")
        _require_bool(self.final, "SettlementObservation.final")
        _require_count(self.correction_version, "SettlementObservation.correction_version", 0)
        require_type(self.provenance, Provenance, "SettlementObservation.provenance")
        session = self.session_date.isoformat()
        expected = f"s:{self.settlement_series}:{session}:c{self.correction_version}"
        _require_exact_id(self.observation_id, expected, "SettlementObservation.observation_id")
        if self.payable_date <= self.session_date:
            raise ValueError("SettlementObservation.payable_date must be after session_date")


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

    def __post_init__(self) -> None:
        """Validate types, a positive tenor, a finite yield and the id."""
        require_id(self.observation_id, "RateObservation.observation_id")
        require_id(self.curve_id, "RateObservation.curve_id")
        _require_count(self.tenor_days, "RateObservation.tenor_days", 1)
        _require_finite_decimal(self.bey, "RateObservation.bey")
        _require_date(self.observation_date, "RateObservation.observation_date")
        _require_ns(self.available_at_ns, "RateObservation.available_at_ns")
        require_type(self.provenance, Provenance, "RateObservation.provenance")
        dated = self.observation_date.isoformat()
        expected = f"r:{self.curve_id}:{self.tenor_days}:{dated}"
        _require_exact_id(self.observation_id, expected, "RateObservation.observation_id")


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

    def __post_init__(self) -> None:
        """Validate types and that exactly one of value and missing_reason is present."""
        require_id(self.feature_id, "FeatureObservation.feature_id")
        require_id(self.feature_version, "FeatureObservation.feature_version")
        _require_date(self.session_date, "FeatureObservation.session_date")
        if self.value is not None:
            _require_finite_decimal(self.value, "FeatureObservation.value")
        field = "FeatureObservation.max_input_available_at_ns"
        _require_ns(self.max_input_available_at_ns, field)
        _require_count(self.warmup_count, "FeatureObservation.warmup_count", 0)
        if self.missing_reason is not None:
            require_id(self.missing_reason, "FeatureObservation.missing_reason")
        require_sha256(self.input_digest, "FeatureObservation.input_digest")
        if (self.value is None) == (self.missing_reason is None):
            raise ValueError(
                "FeatureObservation.missing_reason must be given exactly when value is None"
            )


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

    def __post_init__(self) -> None:
        """Validate types and ``open_ns < close_ns < cutoff_ns``."""
        _require_date(self.session_date, "TradingSession.session_date")
        _require_ns(self.open_ns, "TradingSession.open_ns")
        _require_ns(self.close_ns, "TradingSession.close_ns")
        _require_ns(self.cutoff_ns, "TradingSession.cutoff_ns")
        _require_bool(self.early_close, "TradingSession.early_close")
        if self.close_ns <= self.open_ns:
            raise ValueError("TradingSession.close_ns must be after open_ns")
        if self.cutoff_ns <= self.close_ns:
            raise ValueError("TradingSession.cutoff_ns must be after close_ns")


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

    def __post_init__(self) -> None:
        """Validate types and a non-empty table name."""
        require_id(self.table, "CoveragePartition.table")
        _require_date(self.session_date, "CoveragePartition.session_date")
        require_type(self.status, CoverageState, "CoveragePartition.status")
        require_type(self.note, str, "CoveragePartition.note")
