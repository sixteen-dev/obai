"""Canonical on-disk form of a frozen dataset: the package's only file I/O (ADR 0002 §2).

A dataset directory holds ``{table}.jsonl`` per ``TABLE_NAMES`` entry (the table's canonical
bytes, so its sha256 is the manifest's digest) and ``manifest.json`` (canonical JSON of the
manifest).
"""

from __future__ import annotations

from pathlib import Path

from options_backtest.data.manifest import FrozenDataset


def write_dataset(dataset: FrozenDataset, directory: Path) -> None:
    """Write the dataset's tables and manifest into ``directory``.

    Args:
        dataset: Dataset to write.
        directory: Existing, empty directory.

    Raises:
        FileExistsError: If a file it would write already exists.
        FileNotFoundError: If ``directory`` does not exist.

    """
    raise NotImplementedError


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
    raise NotImplementedError
