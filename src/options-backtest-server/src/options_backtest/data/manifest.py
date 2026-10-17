"""Frozen, digest-identified datasets (ADR 0002 §2, design §8.1-§8.2).

A dataset is nine immutable tables plus a manifest. ``FrozenDataset.freeze`` sorts every table,
serializes each row as canonical JSON and hashes; ``manifest_id`` hashes the table digests and
versions, so the same rows in any input order give the same ``manifest_id`` (C34).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from itertools import pairwise
from typing import Final

from options_backtest.data.records import (
    ActivityObservation,
    ContractVersion,
    CoveragePartition,
    FeatureObservation,
    FidelityClass,
    QuoteObservation,
    RateObservation,
    SettlementObservation,
    TradingSession,
    UnderlyingObservation,
    require_sha256,
)
from options_backtest.models.market import require_id, require_type

TABLE_NAMES: Final = (
    "sessions",
    "contracts",
    "quotes",
    "underlying",
    "activity",
    "settlements",
    "rates",
    "features",
    "coverage",
)
"""Dataset tables, in manifest order; each is also a ``FrozenDataset`` attribute."""
SYNTHETIC_SOURCE_ID: Final = "synthetic"
SYNTHETIC_LICENSE_POLICY_ID: Final = "synthetic_public"
_MAX_DEPTH: Final = 32
"""Deepest container nesting ``canonical_json`` encodes; records nest well below it."""
_JSON_SCALARS: Final = (bool, int, str)

type _SourcedRecord = (
    ContractVersion
    | QuoteObservation
    | UnderlyingObservation
    | ActivityObservation
    | SettlementObservation
    | RateObservation
)


def canonical_json(value: object) -> bytes:
    """Return the canonical UTF-8 JSON of a record tree.

    ``json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)``
    over: a dataclass as an object of its fields; a mapping as an object (keys must be str); a
    tuple or list as an array; ``Decimal`` as ``str(value)`` (representation kept, so a
    round trip through the store is byte-identical); ``date`` and aware ``datetime`` as
    ``isoformat()``; an enum as its value; ``bool``, ``int``, ``str`` and None as themselves.

    Args:
        value: The tree to encode.

    Returns:
        The canonical bytes.

    Raises:
        TypeError: For a float, a naive datetime or any other unsupported type.
        ValueError: For a non-finite ``Decimal`` or nesting deeper than 32 containers.

    """
    text = json.dumps(
        _plain(value, 0),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return text.encode("utf-8")


def _plain(value: object, depth: int) -> object:
    """Return the JSON-native tree of ``value``: containers here, scalars in ``_scalar``."""
    if depth > _MAX_DEPTH:
        raise ValueError(f"canonical_json: tree nested deeper than {_MAX_DEPTH} containers")
    if value is None or type(value) in _JSON_SCALARS:
        return value  # most leaves: exactly _scalar's first case, without the container checks
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name), depth + 1) for f in fields(value)}
    if isinstance(value, Mapping):
        return {_key(key): _plain(item, depth + 1) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item, depth + 1) for item in value]
    return _scalar(value)


def _key(key: object) -> str:
    if type(key) is not str:
        raise TypeError(f"canonical_json: mapping keys must be str, got {type(key).__name__}")
    return key


def _scalar(value: object) -> object:
    if value is None or type(value) in _JSON_SCALARS:
        return value
    if isinstance(value, Enum):
        return _scalar(value.value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"canonical_json: Decimal must be finite, got {value}")
        return str(value)
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise TypeError(f"canonical_json: datetime must be timezone-aware, got {value}")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"canonical_json cannot encode {type(value).__name__}")


def table_digest(rows: Sequence[object]) -> str:
    """Return the sha256 hex of a table: each row's ``canonical_json`` then one newline byte.

    These bytes are exactly the table's JSONL file (``data.store``); artifact tables use the
    same digest (ADR 0002 §6).

    Args:
        rows: The table's rows, in table order.

    Returns:
        The digest; that of zero bytes for an empty table.

    Raises:
        TypeError: If ``rows`` is not a sequence or a row cannot be encoded.

    """
    _require_sequence(rows, "table_digest rows")
    digest = hashlib.sha256()
    for row in rows:
        digest.update(canonical_json(row))
        digest.update(b"\n")
    return digest.hexdigest()


def _require_sequence(value: object, field: str) -> None:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise TypeError(f"{field} must be a sequence of rows, got {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class TableDigest:
    """Identity of one frozen table.

    Attributes:
        table: Table name, one of ``TABLE_NAMES``.
        rows: Row count.
        sha256: ``table_digest`` of its rows.

    """

    table: str
    rows: int
    sha256: str

    def __post_init__(self) -> None:
        """Require a known table, a row count >= 0 and a sha256 digest."""
        if self.table not in TABLE_NAMES:
            raise ValueError(f"TableDigest.table must be one of TABLE_NAMES, got {self.table!r}")
        if type(self.rows) is not int:
            raise TypeError(f"TableDigest.rows must be int, got {type(self.rows).__name__}")
        if self.rows < 0:
            raise ValueError(f"TableDigest.rows must be >= 0, got {self.rows}")
        require_sha256(self.sha256, "TableDigest.sha256")


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    """What a frozen dataset contains and under which versions (design §8.2 ``DatasetManifest``).

    Attributes:
        manifest_id: sha256 hex of the canonical JSON of every other field.
        fidelity: Fidelity class of the whole dataset.
        limitations: Disclosed limitation ids, sorted; () for synthetic data.
        tables: One digest per table, in ``TABLE_NAMES`` order.
        calendar_version: Version of the session table's rules (``synthetic_weekdays_v1``).
        product_rules_version: ``reference.products.PRODUCT_RULES_VERSION``.
        feature_versions: (feature name, version) pairs, sorted by name.
        license_policy_id: License and retention policy; ``synthetic_public`` for synthetic.

    """

    manifest_id: str
    fidelity: FidelityClass
    limitations: tuple[str, ...]
    tables: tuple[TableDigest, ...]
    calendar_version: str
    product_rules_version: str
    feature_versions: tuple[tuple[str, str], ...]
    license_policy_id: str

    def __post_init__(self) -> None:
        """Validate every field and that ``manifest_id`` is the digest of the others."""
        require_sha256(self.manifest_id, "DatasetManifest.manifest_id")
        require_type(self.fidelity, FidelityClass, "DatasetManifest.fidelity")
        _require_sorted_ids(self.limitations, "DatasetManifest.limitations")
        _require_table_order(self.tables)
        require_id(self.calendar_version, "DatasetManifest.calendar_version")
        require_id(self.product_rules_version, "DatasetManifest.product_rules_version")
        _require_feature_versions(self.feature_versions)
        require_id(self.license_policy_id, "DatasetManifest.license_policy_id")
        body = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "manifest_id"}
        if self.manifest_id != _manifest_id(body):
            raise ValueError(
                f"DatasetManifest.manifest_id {self.manifest_id} is not the sha256 of the "
                f"canonical manifest without it ({_manifest_id(body)})"
            )


def _manifest_id(body: Mapping[str, object]) -> str:
    """Return the sha256 of the canonical JSON of the manifest fields other than its id."""
    return hashlib.sha256(canonical_json(body)).hexdigest()


def _require_sorted_ids(values: object, field: str) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple, got {type(values).__name__}")
    for value in values:
        require_id(value, f"{field} item")
    if list(values) != sorted(set(values)):
        raise ValueError(f"{field} must be sorted and unique, got {values}")


def _require_table_order(tables: object) -> None:
    if not isinstance(tables, tuple):
        raise TypeError(f"DatasetManifest.tables must be a tuple, got {type(tables).__name__}")
    for digest in tables:
        require_type(digest, TableDigest, "DatasetManifest.tables item")
    if tuple(digest.table for digest in tables) != TABLE_NAMES:
        raise ValueError(
            "DatasetManifest.tables must list one digest per table in TABLE_NAMES order"
        )


def _require_feature_versions(pairs: object) -> None:
    field = "DatasetManifest.feature_versions"
    if not isinstance(pairs, tuple):
        raise TypeError(f"{field} must be a tuple, got {type(pairs).__name__}")
    for pair in pairs:
        require_type(pair, tuple, f"{field} item")
        if len(pair) != 2:  # noqa: PLR2004 — a (name, version) pair
            raise ValueError(f"{field} items must be (name, version) pairs, got {pair!r}")
        require_id(pair[0], f"{field} name")
        require_id(pair[1], f"{field} version")
    _require_sorted_ids(tuple(name for name, _ in pairs), field)


@dataclass(frozen=True, slots=True)
class FrozenDataset:
    """An immutable dataset whose tables match its manifest; build it only with ``freeze``.

    ``with_features`` reseals a frozen dataset with other features (the generator computes them
    from the frozen tables, ADR 0002 §5), exactly as ``freeze`` would.

    Every table is sorted: ``sessions`` by date; ``contracts`` by ``version_id``; observation
    tables by ``observation_id``; ``features`` by (feature_id, feature_version, session_date);
    ``coverage`` by (table, session_date).

    Attributes:
        manifest: Identity and versions.
        sessions: Trading session table.
        contracts: Contract versions.
        quotes: Option quote observations.
        underlying: Index observations.
        activity: Cumulative volume observations.
        settlements: Settlement observations.
        rates: Discount-curve observations.
        features: Feature observations.
        coverage: Coverage partitions.

    """

    manifest: DatasetManifest
    sessions: tuple[TradingSession, ...]
    contracts: tuple[ContractVersion, ...]
    quotes: tuple[QuoteObservation, ...]
    underlying: tuple[UnderlyingObservation, ...]
    activity: tuple[ActivityObservation, ...]
    settlements: tuple[SettlementObservation, ...]
    rates: tuple[RateObservation, ...]
    features: tuple[FeatureObservation, ...]
    coverage: tuple[CoveragePartition, ...]

    @classmethod
    def freeze(  # noqa: PLR0913 — one keyword per table and manifest field (ADR 0002 §2)
        cls,
        *,
        sessions: Sequence[TradingSession],
        contracts: Sequence[ContractVersion],
        quotes: Sequence[QuoteObservation],
        underlying: Sequence[UnderlyingObservation],
        activity: Sequence[ActivityObservation],
        settlements: Sequence[SettlementObservation],
        rates: Sequence[RateObservation],
        features: Sequence[FeatureObservation],
        coverage: Sequence[CoveragePartition],
        fidelity: FidelityClass,
        limitations: Sequence[str],
        calendar_version: str,
        product_rules_version: str,
        feature_versions: Sequence[tuple[str, str]],
        license_policy_id: str,
    ) -> FrozenDataset:
        """Sort, digest and seal the tables into a dataset.

        Args:
            sessions: Session rows, any order.
            contracts: Contract versions, any order.
            quotes: Quote observations, any order.
            underlying: Index observations, any order.
            activity: Activity observations, any order.
            settlements: Settlement observations, any order.
            rates: Rate observations, any order.
            features: Feature observations, any order.
            coverage: Coverage partitions, any order.
            fidelity: Fidelity class.
            limitations: Limitation ids; stored sorted.
            calendar_version: Session-table rule version.
            product_rules_version: Product-rules version.
            feature_versions: (feature name, version) pairs; stored sorted.
            license_policy_id: License policy.

        Returns:
            The frozen dataset; equal inputs in any order give an equal dataset.

        Raises:
            ValueError: On a duplicate id or key in any table; or when a record's provenance
                is ``synthetic`` but ``fidelity`` is not SYNTHETIC_FIXTURE or
                ``license_policy_id`` is not ``synthetic_public`` (refused, never coerced). A
                table's key also covers time: two sessions whose ``[open_ns, cutoff_ns]``
                intervals meet, or two versions of one contract whose effective intervals
                overlap, are refused as duplicates.
            TypeError: If a table holds a row of another type or an argument has the wrong
                type.

        """
        require_type(fidelity, FidelityClass, "freeze fidelity")
        _require_sequence(limitations, "freeze limitations")
        _require_sequence(feature_versions, "freeze feature_versions")
        tables = (
            _sorted_rows("sessions", sessions, TradingSession, lambda row: (row.session_date,)),
            _sorted_rows("contracts", contracts, ContractVersion, lambda row: (row.version_id,)),
            _sorted_rows("quotes", quotes, QuoteObservation, _observation_key),
            _sorted_rows("underlying", underlying, UnderlyingObservation, _observation_key),
            _sorted_rows("activity", activity, ActivityObservation, _observation_key),
            _sorted_rows("settlements", settlements, SettlementObservation, _observation_key),
            _sorted_rows("rates", rates, RateObservation, _observation_key),
            _sorted_rows("features", features, FeatureObservation, _feature_key),
            _sorted_rows("coverage", coverage, CoveragePartition, _coverage_key),
        )
        _require_disjoint_sessions(tables[0])
        _require_disjoint_versions(tables[1])
        _require_synthetic_policy(
            (contracts, quotes, underlying, activity, settlements, rates),
            fidelity,
            license_policy_id,
        )
        digests = tuple(
            TableDigest(name, len(rows), table_digest(rows))
            for name, rows in zip(TABLE_NAMES, tables, strict=True)
        )
        manifest = _seal(
            fidelity=fidelity,
            limitations=tuple(sorted(limitations)),
            tables=digests,
            calendar_version=calendar_version,
            product_rules_version=product_rules_version,
            feature_versions=tuple(sorted(feature_versions)),
            license_policy_id=license_policy_id,
        )
        return cls(manifest, *tables)

    def with_features(self, features: Sequence[FeatureObservation]) -> FrozenDataset:
        """Return this dataset with its features table replaced, sealed as ``freeze`` seals it.

        Equal to ``freeze`` of this dataset's other tables and manifest fields with
        ``features``: only the features table is sorted, checked and digested; the other
        tables, already sealed, keep their rows and digests (the features table takes part in
        no other check of ``freeze``).

        Args:
            features: Feature observations, any order.

        Returns:
            The resealed dataset.

        Raises:
            ValueError: On a repeated (feature_id, feature_version, session_date).
            TypeError: If a row is not a ``FeatureObservation``.

        """
        rows = _sorted_rows("features", features, FeatureObservation, _feature_key)
        digest = TableDigest("features", len(rows), table_digest(rows))
        old = self.manifest
        manifest = _seal(
            fidelity=old.fidelity,
            limitations=old.limitations,
            tables=tuple(digest if t.table == "features" else t for t in old.tables),
            calendar_version=old.calendar_version,
            product_rules_version=old.product_rules_version,
            feature_versions=old.feature_versions,
            license_policy_id=old.license_policy_id,
        )
        return replace(self, manifest=manifest, features=rows)


type _ObservationRow = (
    QuoteObservation
    | UnderlyingObservation
    | ActivityObservation
    | SettlementObservation
    | RateObservation
)


def _observation_key(row: _ObservationRow) -> tuple[str]:
    return (row.observation_id,)


def _feature_key(row: FeatureObservation) -> tuple[str, str, date]:
    return (row.feature_id, row.feature_version, row.session_date)


def _coverage_key(row: CoveragePartition) -> tuple[str, date]:
    return (row.table, row.session_date)


def _sorted_rows[R](
    table: str,
    rows: Sequence[R],
    record_type: type[R],
    key: Callable[[R], tuple[str | date, ...]],
) -> tuple[R, ...]:
    """Return the rows sorted by ``key``, refusing a foreign row or a repeated key."""
    _require_sequence(rows, table)
    for row in rows:
        if not isinstance(row, record_type):
            raise TypeError(
                f"{table}: rows must be {record_type.__name__}, got {type(row).__name__}"
            )
    ordered = tuple(sorted(rows, key=key))
    for previous, current in pairwise(ordered):
        if key(previous) == key(current):
            raise ValueError(f"{table}: duplicate key {key(current)!r}")
    return ordered


def _require_disjoint_sessions(sessions: Sequence[TradingSession]) -> None:
    """Refuse a session opening at or before the previous one's cutoff (date-sorted input)."""
    for previous, current in pairwise(sessions):
        if current.open_ns <= previous.cutoff_ns:
            raise ValueError(
                f"sessions: overlapping intervals of {previous.session_date} and "
                f"{current.session_date}"
            )


def _require_disjoint_versions(versions: Sequence[ContractVersion]) -> None:
    """Refuse two versions of one contract whose effective intervals overlap."""
    ordered = sorted(versions, key=lambda v: (v.terms.contract_id, v.effective_from_ns))
    for previous, current in pairwise(ordered):
        same = previous.terms.contract_id == current.terms.contract_id
        ended = previous.effective_to_ns
        if same and (ended is None or ended > current.effective_from_ns):
            raise ValueError(
                f"contracts: overlapping versions {previous.version_id} and {current.version_id}"
            )


def _require_synthetic_policy(
    tables: Iterable[Sequence[_SourcedRecord]], fidelity: FidelityClass, license_policy_id: str
) -> None:
    """Refuse synthetic records unless the dataset is a synthetic fixture under its license."""
    synthetic = any(
        row.provenance.source_id == SYNTHETIC_SOURCE_ID for rows in tables for row in rows
    )
    allowed = (
        fidelity is FidelityClass.SYNTHETIC_FIXTURE
        and license_policy_id == SYNTHETIC_LICENSE_POLICY_ID
    )
    if synthetic and not allowed:
        raise ValueError(
            f"synthetic records need fidelity {FidelityClass.SYNTHETIC_FIXTURE.value} and "
            f"license {SYNTHETIC_LICENSE_POLICY_ID}, got {fidelity.value} and {license_policy_id!r}"
        )


def _seal(  # noqa: PLR0913 — one keyword per manifest field
    *,
    fidelity: FidelityClass,
    limitations: tuple[str, ...],
    tables: tuple[TableDigest, ...],
    calendar_version: str,
    product_rules_version: str,
    feature_versions: tuple[tuple[str, str], ...],
    license_policy_id: str,
) -> DatasetManifest:
    """Return the manifest of these fields, its id the digest of their canonical JSON."""
    body = {
        "fidelity": fidelity,
        "limitations": limitations,
        "tables": tables,
        "calendar_version": calendar_version,
        "product_rules_version": product_rules_version,
        "feature_versions": feature_versions,
        "license_policy_id": license_policy_id,
    }
    return DatasetManifest(
        manifest_id=_manifest_id(body),
        fidelity=fidelity,
        limitations=limitations,
        tables=tables,
        calendar_version=calendar_version,
        product_rules_version=product_rules_version,
        feature_versions=feature_versions,
        license_policy_id=license_policy_id,
    )
