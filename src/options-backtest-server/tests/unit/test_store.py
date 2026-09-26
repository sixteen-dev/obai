"""The canonical on-disk dataset: write, read, and refuse any mismatch (ADR 0002 §2)."""

import hashlib
import json
from pathlib import Path

import pytest
from data_builders import sample_dataset

from options_backtest.data.manifest import TABLE_NAMES, canonical_json
from options_backtest.data.records import FidelityClass
from options_backtest.data.store import read_dataset, write_dataset

MANIFEST = "manifest.json"


def _written(tmp_path: Path) -> Path:
    directory = tmp_path / "dataset"
    directory.mkdir()
    write_dataset(sample_dataset(), directory)
    return directory


def _reseal(directory: Path, table: str) -> None:
    """Rewrite manifest.json so ``table``'s digest matches its file and the id is consistent."""
    manifest = json.loads((directory / MANIFEST).read_bytes())
    data = (directory / f"{table}.jsonl").read_bytes()
    for entry in manifest["tables"]:
        if entry["table"] == table:
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            entry["rows"] = data.count(b"\n")
    del manifest["manifest_id"]
    manifest["manifest_id"] = hashlib.sha256(canonical_json(manifest)).hexdigest()
    (directory / MANIFEST).write_bytes(canonical_json(manifest))


def test_round_trip_gives_an_equal_dataset(tmp_path: Path) -> None:
    dataset = sample_dataset()
    directory = _written(tmp_path)
    assert sorted(path.name for path in directory.iterdir()) == sorted(
        [MANIFEST, *(f"{name}.jsonl" for name in TABLE_NAMES)]
    )
    assert read_dataset(directory) == dataset


def test_files_are_the_canonical_bytes_the_manifest_hashes(tmp_path: Path) -> None:
    dataset = sample_dataset()
    directory = _written(tmp_path)
    assert (directory / MANIFEST).read_bytes() == canonical_json(dataset.manifest)
    for digest in dataset.manifest.tables:
        data = (directory / f"{digest.table}.jsonl").read_bytes()
        rows = getattr(dataset, digest.table)
        assert data == b"".join(canonical_json(row) + b"\n" for row in rows)
        assert hashlib.sha256(data).hexdigest() == digest.sha256


def test_a_rewrite_of_a_read_dataset_is_byte_identical(tmp_path: Path) -> None:
    first = _written(tmp_path)
    second = tmp_path / "again"
    second.mkdir()
    write_dataset(read_dataset(first), second)
    for path in first.iterdir():
        assert (second / path.name).read_bytes() == path.read_bytes()


def test_decimal_text_survives_the_store(tmp_path: Path) -> None:
    quotes = read_dataset(_written(tmp_path)).quotes
    assert {str(q.bid) for q in quotes} == {"2.00", "2.05", "0", "1.90"}


def test_write_refuses_a_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        write_dataset(sample_dataset(), tmp_path / "absent")


def test_write_refuses_to_overwrite_and_writes_nothing(tmp_path: Path) -> None:
    (tmp_path / "rates.jsonl").write_bytes(b"keep")
    with pytest.raises(FileExistsError, match="rates.jsonl"):
        write_dataset(sample_dataset(), tmp_path)
    assert [path.name for path in tmp_path.iterdir()] == ["rates.jsonl"]
    assert (tmp_path / "rates.jsonl").read_bytes() == b"keep"


@pytest.mark.parametrize("name", [MANIFEST, "quotes.jsonl", "coverage.jsonl"])
def test_read_refuses_a_missing_file(tmp_path: Path, name: str) -> None:
    directory = _written(tmp_path)
    (directory / name).unlink()
    with pytest.raises(FileNotFoundError):
        read_dataset(directory)


def test_read_refuses_a_tampered_row(tmp_path: Path) -> None:
    directory = _written(tmp_path)
    path = directory / "quotes.jsonl"
    path.write_bytes(path.read_bytes().replace(b'"bid":"2.00"', b'"bid":"2.05"', 1))
    with pytest.raises(ValueError, match=r"quotes\.jsonl.*sha256"):
        read_dataset(directory)


def test_read_refuses_a_dropped_row(tmp_path: Path) -> None:
    directory = _written(tmp_path)
    path = directory / "quotes.jsonl"
    path.write_bytes(b"".join(path.read_bytes().splitlines(keepends=True)[1:]))
    with pytest.raises(ValueError, match=r"quotes\.jsonl"):
        read_dataset(directory)


def test_read_refuses_a_tampered_manifest(tmp_path: Path) -> None:
    directory = _written(tmp_path)
    manifest = json.loads((directory / MANIFEST).read_bytes())
    manifest["calendar_version"] = "other_calendar_v1"
    (directory / MANIFEST).write_bytes(canonical_json(manifest))
    with pytest.raises(ValueError, match="manifest_id"):
        read_dataset(directory)


def test_read_refuses_a_synthetic_dataset_relabelled_historical(tmp_path: Path) -> None:
    directory = _written(tmp_path)
    manifest = json.loads((directory / MANIFEST).read_bytes())
    manifest["fidelity"] = FidelityClass.HISTORICAL_SNAPSHOT.value
    del manifest["manifest_id"]
    manifest["manifest_id"] = hashlib.sha256(canonical_json(manifest)).hexdigest()
    (directory / MANIFEST).write_bytes(canonical_json(manifest))
    with pytest.raises(ValueError, match="synthetic"):
        read_dataset(directory)


def test_read_refuses_non_canonical_rows_even_when_resealed(tmp_path: Path) -> None:
    directory = _written(tmp_path)
    path = directory / "coverage.jsonl"
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
    _reseal(directory, "coverage")
    with pytest.raises(ValueError, match="re-frozen"):
        read_dataset(directory)


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        (b'"bid":"2.00"', b'"bid":2.00'),
        (b'"bid":"2.00"', b'"bid":NaN'),
        (b'"bid":"2.00"', b'"bid":"2.00","bid":"2.00"'),
        (b'"bid":"2.00"', b'"bid":"two"'),
        (b'"bid":"2.00"', b'"bid":"2.00","venue":"x"'),
        (b'"bid":"2.00",', b""),
        (b'"bid_size":50', b'"bid_size":"50"'),
        (b'"bid_size":50', b'"bid_size":true'),
        (b'"session_date":"2024-03-04"', b'"session_date":"2024-02-30"'),
        (b'"provenance":{', b'"provenance":[{'),
        (b"}\n", b"}"),
        (b"{", b"["),
    ],
)
def test_read_refuses_a_row_that_does_not_decode(
    tmp_path: Path, original: bytes, replacement: bytes
) -> None:
    directory = _written(tmp_path)
    path = directory / "quotes.jsonl"
    data = path.read_bytes()
    assert original in data
    path.write_bytes(data.replace(original, replacement, 1))
    _reseal(directory, "quotes")
    with pytest.raises(ValueError, match=r"quotes\.jsonl"):
        read_dataset(directory)


def test_read_refuses_a_manifest_that_does_not_decode(tmp_path: Path) -> None:
    directory = _written(tmp_path)
    (directory / MANIFEST).write_bytes(b'{"manifest_id":1.5}')
    with pytest.raises(ValueError, match=MANIFEST):
        read_dataset(directory)


def test_read_refuses_a_last_row_without_its_newline(tmp_path: Path) -> None:
    directory = _written(tmp_path)
    path = directory / "sessions.jsonl"
    path.write_bytes(path.read_bytes()[:-1])
    _reseal(directory, "sessions")
    with pytest.raises(ValueError, match=r"sessions\.jsonl line 2 does not end with a newline"):
        read_dataset(directory)
