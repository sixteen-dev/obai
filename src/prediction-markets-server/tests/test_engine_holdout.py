"""Tests for engine.holdout — chronological split + out_of_sample assembly (§11.5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from src.engine.holdout import LOW_N_FLOOR, HoldoutSpec, build_out_of_sample
from src.engine.observations import split_by_entry


@dataclass(frozen=True)
class _Item:
    cid: str
    ts: datetime


def _at(minute: int) -> datetime:
    return datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def _items(n: int, *, distinct: bool = True) -> list[_Item]:
    # distinct=True → one market per item; distinct=False → all share one cid.
    return [_Item(cid=f"m{i}" if distinct else "m", ts=_at(i)) for i in range(n)]


def _straddling_items(n: int) -> list[_Item]:
    # Each market contributes one early and one late item, straddling _at(50).
    early = [_Item(cid=f"m{i}", ts=_at(i)) for i in range(n)]
    late = [_Item(cid=f"m{i}", ts=_at(100 + i)) for i in range(n)]
    return early + late


def _key(i: _Item) -> datetime:
    return i.ts


def _market(i: _Item) -> str:
    return i.cid


# -- split_by_entry ----------------------------------------------------------


def test_split_fraction_takes_latest_as_holdout() -> None:
    train, holdout = split_by_entry(_items(10), key=_key, fraction=0.3)
    assert [i.cid for i in train] == [f"m{i}" for i in range(7)]
    assert [i.cid for i in holdout] == ["m7", "m8", "m9"]


def test_split_sorts_unordered_input() -> None:
    shuffled = [_items(5)[i] for i in (3, 0, 4, 1, 2)]
    train, holdout = split_by_entry(shuffled, key=_key, fraction=0.4)
    assert [i.cid for i in train] == ["m0", "m1", "m2"]
    assert [i.cid for i in holdout] == ["m3", "m4"]


def test_split_cutoff_is_strictly_before_vs_on_or_after() -> None:
    train, holdout = split_by_entry(_items(6), key=_key, cutoff=_at(3))
    assert [i.cid for i in train] == ["m0", "m1", "m2"]
    assert [i.cid for i in holdout] == ["m3", "m4", "m5"]  # cutoff timestamp lands in holdout


@pytest.mark.parametrize(
    ("fraction", "cutoff"),
    [(0.3, _at(3)), (None, None)],
)
def test_split_requires_exactly_one_of_fraction_cutoff(
    fraction: float | None, cutoff: datetime | None
) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        split_by_entry(_items(4), key=_key, fraction=fraction, cutoff=cutoff)


@pytest.mark.parametrize("fraction", [0.0, 1.0, 1.5, -0.1])
def test_split_rejects_out_of_range_fraction(fraction: float) -> None:
    with pytest.raises(ValueError, match="fraction"):
        split_by_entry(_items(4), key=_key, fraction=fraction)


def test_split_empty_data_returns_two_empties_without_raising() -> None:
    train, holdout = split_by_entry([], key=_key, fraction=0.3)
    assert train == []
    assert holdout == []


def test_split_one_sided_when_fraction_rounds_to_zero_holdout() -> None:
    # 2 items * 0.3 → int(0.6) == 0 holdout; a degenerate split, not an error.
    train, holdout = split_by_entry(_items(2), key=_key, fraction=0.3)
    assert len(train) == 2
    assert holdout == []


# -- build_out_of_sample ------------------------------------------------------


def _count(items: list[_Item]) -> dict[str, Any]:
    return {"n": float(len(items))}


def _delta(holdout: dict[str, Any], train: dict[str, Any]) -> dict[str, Any]:
    return {"n": holdout["n"] - train["n"]}


def test_build_out_of_sample_fraction_block_shape() -> None:
    block = build_out_of_sample(
        _items(10),
        key=_key,
        market_key=_market,
        spec=HoldoutSpec(fraction=0.3),
        aggregate=_count,
        delta=_delta,
    )
    assert block["split_key"] == "entry_timestamp"
    assert block["method"] == "fraction"
    assert block["cutoff_ts"] == _at(7).isoformat().replace("+00:00", "Z")  # first holdout ts
    assert block["train"]["n"] == 7.0
    assert block["holdout"]["n"] == 3.0
    assert block["delta"]["n"] == -4.0


def test_build_out_of_sample_explicit_cutoff_method() -> None:
    block = build_out_of_sample(
        _items(6),
        key=_key,
        market_key=_market,
        spec=HoldoutSpec(cutoff=_at(3)),
        aggregate=_count,
        delta=_delta,
    )
    assert block["method"] == "explicit_cutoff"
    assert block["cutoff_ts"] == _at(3).isoformat().replace("+00:00", "Z")


def test_build_out_of_sample_explicit_cutoff_echoes_requested_not_first_holdout() -> None:
    # Cutoff sits BETWEEN m2 and m3 (no observation exactly on it). cutoff_ts
    # must echo the REQUESTED boundary, not the first holdout row's later ts.
    cutoff = _at(3) - timedelta(seconds=30)
    block = build_out_of_sample(
        _items(6),
        key=_key,
        market_key=_market,
        spec=HoldoutSpec(cutoff=cutoff),
        aggregate=_count,
        delta=_delta,
    )
    assert block["method"] == "explicit_cutoff"
    assert block["cutoff_ts"] == cutoff.isoformat().replace("+00:00", "Z")
    assert block["cutoff_ts"] != _at(3).isoformat().replace("+00:00", "Z")  # not first holdout ts
    assert block["train"]["n"] == 3.0  # m0, m1, m2 (all strictly before the cutoff)
    assert block["holdout"]["n"] == 3.0  # m3, m4, m5 (on/after the cutoff)


def test_build_out_of_sample_low_n_true_below_floor() -> None:
    block = build_out_of_sample(
        _items(LOW_N_FLOOR),  # 10 distinct markets total → split halves are both < floor
        key=_key,
        market_key=_market,
        spec=HoldoutSpec(fraction=0.3),
        aggregate=_count,
        delta=_delta,
    )
    assert block["low_n"] is True


def test_build_out_of_sample_low_n_false_when_both_halves_have_enough_markets() -> None:
    block = build_out_of_sample(
        _items(LOW_N_FLOOR * 4),  # 40 distinct → 28 train / 12 holdout, both >= floor
        key=_key,
        market_key=_market,
        spec=HoldoutSpec(fraction=0.3),
        aggregate=_count,
        delta=_delta,
    )
    assert block["low_n"] is False


def test_build_out_of_sample_counts_markets_present_in_both_halves() -> None:
    # The audit fixture: 12 markets, each contributing one observation before
    # the cutoff and one on/after it, so every market lands in both halves.
    block = build_out_of_sample(
        _straddling_items(12),
        key=_key,
        market_key=_market,
        spec=HoldoutSpec(cutoff=_at(50)),
        aggregate=_count,
        delta=_delta,
    )
    assert block["overlap_market_count"] == 12
    assert block["train"]["n"] == 12.0
    assert block["holdout"]["n"] == 12.0
    assert block["low_n"] is False  # 12 distinct markets per half clears the floor


def test_build_out_of_sample_overlap_is_zero_for_disjoint_markets() -> None:
    # One market per item → a chronological split cannot put a market in both halves.
    block = build_out_of_sample(
        _items(LOW_N_FLOOR * 4),
        key=_key,
        market_key=_market,
        spec=HoldoutSpec(fraction=0.3),
        aggregate=_count,
        delta=_delta,
    )
    assert block["overlap_market_count"] == 0


def test_holdout_spec_engaged() -> None:
    assert HoldoutSpec().engaged is False
    assert HoldoutSpec(fraction=0.3).engaged is True
    assert HoldoutSpec(cutoff=_at(1)).engaged is True
