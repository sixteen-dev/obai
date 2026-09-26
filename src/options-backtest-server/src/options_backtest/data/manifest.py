"""Frozen, digest-identified datasets (ADR 0002 §2, design §8.1-§8.2).

A dataset is nine immutable tables plus a manifest. ``FrozenDataset.freeze`` sorts every table,
serializes each row as canonical JSON and hashes; ``manifest_id`` hashes the table digests and
versions, so the same rows in any input order give the same ``manifest_id`` (C34).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
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
)

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

    """
    raise NotImplementedError


def table_digest(rows: Sequence[object]) -> str:
    """Return the sha256 hex of a table: each row's ``canonical_json`` then one newline byte.

    These bytes are exactly the table's JSONL file (``data.store``); artifact tables use the
    same digest (ADR 0002 §6).

    Args:
        rows: The table's rows, in table order.

    Returns:
        The digest; that of zero bytes for an empty table.

    """
    raise NotImplementedError


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


@dataclass(frozen=True, slots=True)
class FrozenDataset:
    """An immutable dataset whose tables match its manifest; build it only with ``freeze``.

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
                ``license_policy_id`` is not ``synthetic_public`` (refused, never coerced).

        """
        raise NotImplementedError
