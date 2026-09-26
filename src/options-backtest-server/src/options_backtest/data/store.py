"""Canonical on-disk form of a frozen dataset: the package's only file I/O (ADR 0002 §2).

A dataset directory holds ``{table}.jsonl`` per ``TABLE_NAMES`` entry (the table's canonical
bytes, so its sha256 is the manifest's digest) and ``manifest.json`` (canonical JSON of the
manifest).

``write_dataset`` streams each table and writes the manifest last, so an interrupted write
leaves no manifest and cannot be read. ``read_dataset`` checks every file against the manifest,
decodes each row back into its record type from the dataclass type hints (the inverse of
``canonical_json``: objects are dataclasses, strings are ``Decimal``, ``date`` or enum values
where the hint says so), re-freezes, and refuses any difference.
"""

from __future__ import annotations

import hashlib
import json
import types
import typing
from dataclasses import fields, is_dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Final, get_args, get_origin, get_type_hints

from options_backtest.data.manifest import (
    TABLE_NAMES,
    DatasetManifest,
    FrozenDataset,
    TableDigest,
    canonical_json,
)
from options_backtest.data.records import (
    ActivityObservation,
    ContractVersion,
    CoveragePartition,
    FeatureObservation,
    QuoteObservation,
    RateObservation,
    SettlementObservation,
    TradingSession,
    UnderlyingObservation,
)
from options_backtest.models.market import require_type

if TYPE_CHECKING:
    from collections.abc import Sequence

    from _typeshed import DataclassInstance

MANIFEST_FILE: Final = "manifest.json"
_MAX_DEPTH: Final = 16
"""Deepest nesting a stored row may have; records nest well below it."""
_DECODE_ERRORS: Final = (ValueError, TypeError, ArithmeticError)

type _Hints = dict[type, dict[str, object]]
"""Resolved field type hints per dataclass, filled while one ``read_dataset`` call runs."""


def write_dataset(dataset: FrozenDataset, directory: Path) -> None:
    """Write the dataset's tables and manifest into ``directory``.

    Args:
        dataset: Dataset to write.
        directory: Existing, empty directory.

    Raises:
        FileExistsError: If a file it would write already exists.
        FileNotFoundError: If ``directory`` does not exist.

    """
    require_type(dataset, FrozenDataset, "write_dataset dataset")
    require_type(directory, Path, "write_dataset directory")
    if not directory.is_dir():
        raise FileNotFoundError(f"dataset directory {directory} does not exist")
    names = [f"{table}.jsonl" for table in TABLE_NAMES] + [MANIFEST_FILE]
    existing = [name for name in names if (directory / name).exists()]
    if existing:
        raise FileExistsError(f"{directory} already holds {existing}")
    for table in TABLE_NAMES:
        _write_table(directory / f"{table}.jsonl", getattr(dataset, table))
    with (directory / MANIFEST_FILE).open("xb") as handle:
        handle.write(canonical_json(dataset.manifest))


def _write_table(path: Path, rows: Sequence[object]) -> None:
    """Create ``path`` (never overwrite) and write each row's canonical JSON and a newline."""
    with path.open("xb") as handle:
        for row in rows:
            handle.write(canonical_json(row) + b"\n")


def read_dataset(directory: Path) -> FrozenDataset:
    """Read a dataset written by ``write_dataset`` and re-freeze it.

    Decimals are parsed from their strings exactly, so the re-frozen tables are byte-identical.

    Args:
        directory: Directory holding the tables and ``manifest.json``.

    Returns:
        The dataset.

    Raises:
        FileNotFoundError: If a table or the manifest is missing.
        ValueError: If a row does not decode, or any table digest, row count or the
            ``manifest_id`` of the re-frozen dataset differs from ``manifest.json``.

    """
    require_type(directory, Path, "read_dataset directory")
    hints: _Hints = {}
    stored = _read_manifest(directory / MANIFEST_FILE, hints)
    digests = dict(zip(TABLE_NAMES, stored.tables, strict=True))
    dataset = FrozenDataset.freeze(
        sessions=_read_table(directory, digests["sessions"], TradingSession, hints),
        contracts=_read_table(directory, digests["contracts"], ContractVersion, hints),
        quotes=_read_table(directory, digests["quotes"], QuoteObservation, hints),
        underlying=_read_table(directory, digests["underlying"], UnderlyingObservation, hints),
        activity=_read_table(directory, digests["activity"], ActivityObservation, hints),
        settlements=_read_table(directory, digests["settlements"], SettlementObservation, hints),
        rates=_read_table(directory, digests["rates"], RateObservation, hints),
        features=_read_table(directory, digests["features"], FeatureObservation, hints),
        coverage=_read_table(directory, digests["coverage"], CoveragePartition, hints),
        fidelity=stored.fidelity,
        limitations=stored.limitations,
        calendar_version=stored.calendar_version,
        product_rules_version=stored.product_rules_version,
        feature_versions=stored.feature_versions,
        license_policy_id=stored.license_policy_id,
    )
    if dataset.manifest != stored:
        changed = [
            s.table for s, r in zip(stored.tables, dataset.manifest.tables, strict=True) if s != r
        ]
        raise ValueError(
            f"re-frozen manifest {dataset.manifest.manifest_id} differs from {MANIFEST_FILE} "
            f"{stored.manifest_id} in tables {changed}"
        )
    return dataset


def _read_manifest(path: Path, hints: _Hints) -> DatasetManifest:
    """Decode ``manifest.json``; the manifest's own validation checks its id."""
    data = path.read_bytes()
    try:
        return _decode_dataclass(DatasetManifest, _parse(data), hints, 0)
    except _DECODE_ERRORS as e:
        raise ValueError(f"{path.name} does not decode: {e}") from e


def _read_table[R: DataclassInstance](
    directory: Path, digest: TableDigest, row_type: type[R], hints: _Hints
) -> tuple[R, ...]:
    """Stream one table file: hash its bytes, decode each line, then match the manifest."""
    path = directory / f"{digest.table}.jsonl"
    sha256 = hashlib.sha256()
    rows: list[R] = []
    with path.open("rb") as handle:
        for number, line in enumerate(handle, start=1):
            sha256.update(line)
            rows.append(_decode_row(line, row_type, hints, f"{path.name} line {number}"))
    if (len(rows), sha256.hexdigest()) != (digest.rows, digest.sha256):
        raise ValueError(
            f"{path.name}: {len(rows)} rows with sha256 {sha256.hexdigest()} do not match the "
            f"manifest's {digest.rows} rows with sha256 {digest.sha256}"
        )
    return tuple(rows)


def _decode_row[R: DataclassInstance](
    line: bytes, row_type: type[R], hints: _Hints, where: str
) -> R:
    """Decode one JSONL line into a ``row_type`` record."""
    if not line.endswith(b"\n"):
        raise ValueError(f"{where} does not end with a newline")
    try:
        return _decode_dataclass(row_type, _parse(line), hints, 0)
    except _DECODE_ERRORS as e:
        raise ValueError(f"{where} does not decode: {e}") from e


def _parse(data: bytes) -> object:
    """Parse canonical JSON: no float or non-finite literal, no repeated key."""
    return json.loads(
        data,
        object_pairs_hook=_unique_keys,
        parse_float=_reject_number,
        parse_constant=_reject_number,
    )


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError(f"repeated key among {[key for key, _ in pairs]}")
    return obj


def _reject_number(text: str) -> object:
    raise ValueError(f"number {text} is not canonical: decimals are stored as strings")


def _decode(hint: object, value: object, hints: _Hints, depth: int) -> object:
    """Return ``value`` decoded as ``hint``: dataclass, ``X | None``, tuple or scalar."""
    if depth > _MAX_DEPTH:
        raise ValueError(f"row nested deeper than {_MAX_DEPTH} levels")
    if hint is str or hint is int or hint is bool or hint is Decimal:
        return _decode_scalar(hint, value)  # most fields: skip the generic-alias checks
    origin = get_origin(hint)
    if origin is typing.Union or origin is types.UnionType:
        return _decode_optional(hint, value, hints, depth)
    if origin is tuple:
        return _decode_tuple(hint, value, hints, depth)
    if isinstance(hint, type) and is_dataclass(hint):
        return _decode_dataclass(hint, value, hints, depth)
    return _decode_scalar(hint, value)


def _decode_optional(hint: object, value: object, hints: _Hints, depth: int) -> object:
    members = [member for member in get_args(hint) if member is not type(None)]
    if len(members) != 1:
        raise TypeError(f"no decoder for union {hint!r}")
    return None if value is None else _decode(members[0], value, hints, depth + 1)


def _decode_tuple(hint: object, value: object, hints: _Hints, depth: int) -> tuple[object, ...]:
    items = _expect(value, list)
    members = get_args(hint)
    if len(members) == 2 and members[1] is Ellipsis:  # noqa: PLR2004 — tuple[X, ...]
        return tuple(_decode(members[0], item, hints, depth + 1) for item in items)
    return tuple(
        _decode(member, item, hints, depth + 1) for member, item in zip(members, items, strict=True)
    )


def _decode_dataclass[R: DataclassInstance](
    cls: type[R], value: object, hints: _Hints, depth: int
) -> R:
    """Build ``cls`` from a JSON object holding exactly its fields, each decoded by its hint."""
    obj = _expect(value, dict)
    names = [field.name for field in fields(cls)]
    if sorted(obj) != sorted(names):
        raise ValueError(f"{cls.__name__} needs fields {sorted(names)}, got {sorted(obj)}")
    if cls not in hints:
        hints[cls] = get_type_hints(cls)
    field_hints = hints[cls]
    return cls(**{name: _decode(field_hints[name], obj[name], hints, depth + 1) for name in names})


def _decode_scalar(hint: object, value: object) -> object:
    if isinstance(hint, type) and issubclass(hint, Enum):
        return hint(_expect(value, str))
    if hint is Decimal:
        return _decimal(_expect(value, str))
    if hint is date:
        return date.fromisoformat(_expect(value, str))
    if hint is str:
        return _expect(value, str)
    if hint is int:
        return _expect(value, int)
    if hint is bool:
        return _expect(value, bool)
    raise TypeError(f"no decoder for field type {hint!r}")


def _expect[T](value: object, kind: type[T]) -> T:
    """Return ``value`` if its type is exactly ``kind`` (so ``True`` is not an ``int``)."""
    if isinstance(value, kind) and type(value) is kind:
        return value
    raise ValueError(f"expected {kind.__name__}, got {type(value).__name__} {value!r}")


def _decimal(text: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation as e:
        raise ValueError(f"{text!r} is not a decimal") from e
