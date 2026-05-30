"""Out-of-sample (train/holdout) assembly for calibration, longshot, backtest (§11.5).

Pure helpers over a chronological split. The split itself lives in
``engine.observations.split_by_entry``; this module turns it into the
response ``out_of_sample`` block by running a caller-supplied aggregation on
each half and reporting both plus a signed delta. The aggregation/delta
callables are the only per-tool divergence, so all three tools share one
assembly path and cannot drift on the block shape.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TypeVar

from .observations import split_by_entry

_T = TypeVar("_T")

# Distinct-market floor below which a split half's realized frequency is
# noise. Mirrors the §12 calibration bucket usability floor; kept here so
# the out_of_sample block flags thin halves without importing a private.
LOW_N_FLOOR = 10


@dataclass(frozen=True)
class HoldoutSpec:
    """A temporal train/holdout split request (§11.5).

    ``engaged`` is False only when the caller passed neither field, in
    which case the tool emits no ``out_of_sample`` block and its response
    is unchanged from the no-split contract.
    """

    fraction: float | None = None
    cutoff: datetime | None = None

    @property
    def engaged(self) -> bool:
        """True when the caller asked for an out-of-sample split."""
        return self.fraction is not None or self.cutoff is not None


def build_out_of_sample(
    items: list[_T],
    *,
    key: Callable[[_T], datetime],
    market_key: Callable[[_T], str],
    spec: HoldoutSpec,
    aggregate: Callable[[list[_T]], dict[str, Any]],
    delta: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the §11.5 ``out_of_sample`` block from a chronological split.

    Splits ``items`` by entry timestamp, aggregates each half with
    ``aggregate``, and reports both halves plus ``delta(holdout, train)``.
    No binary "edge is real" verdict — interpretation is the agent's job.

    Args:
        items: Observations or Trades feeding the split.
        key: Accessor for each item's entry/observation timestamp.
        market_key: Accessor for each item's market id (drives ``low_n``).
        spec: The split request (``fraction`` or ``cutoff``).
        aggregate: Maps an item list to that tool's metrics dict.
        delta: Maps ``(holdout_metrics, train_metrics)`` to a delta dict.

    Returns:
        The ``out_of_sample`` block: split metadata, ``train``/``holdout``
        metrics, ``delta``, and ``low_n`` (True when either half holds
        fewer than ``LOW_N_FLOOR`` distinct markets).

    """
    train, holdout = split_by_entry(items, key=key, fraction=spec.fraction, cutoff=spec.cutoff)
    train_metrics = aggregate(train)
    holdout_metrics = aggregate(holdout)
    boundary = spec.cutoff if spec.cutoff is not None else (key(holdout[0]) if holdout else None)
    return {
        "split_key": "entry_timestamp",
        "method": "explicit_cutoff" if spec.cutoff is not None else "fraction",
        "cutoff_ts": _iso_z(boundary),
        "train": train_metrics,
        "holdout": holdout_metrics,
        "delta": delta(holdout_metrics, train_metrics),
        "low_n": _below_floor(train, market_key) or _below_floor(holdout, market_key),
    }


def _below_floor(items: list[_T], market_key: Callable[[_T], str]) -> bool:
    """Report whether ``items`` span fewer than ``LOW_N_FLOOR`` distinct markets."""
    return len({market_key(item) for item in items}) < LOW_N_FLOOR


def _iso_z(value: datetime | None) -> str | None:
    """Render an aware UTC ISO-8601 string (``Z`` suffix); None passes through."""
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
