"""Resolution inference for Polymarket markets.

Implements the 6-rule waterfall from
docs/prediction-markets-historical-analytics-upgrade.md §8.1. Pure function
over a normalized market payload — no DB writes, no HTTP calls — so callers
can audit and test it in isolation.

The output drives every downstream calibration/backtest metric, so the
boundary between "resolved", "ambiguous", and "unresolved" must be
deterministic and explainable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

# Resolution status vocabulary. "resolved" rows enter calibration; everything
# else is reported in skipped counts.
ResolutionStatus = Literal["resolved", "ambiguous", "unresolved"]

# Resolution method vocabulary. Mirrors the §8.1 wording so downstream
# response builders can group counts by method without further translation.
ResolutionMethod = Literal[
    "explicit_api",
    "terminal_price_exact",
    "terminal_price_threshold",
    "ambiguous",
    "unresolved",
]

# Tolerance for "exactly 1.0 / 0.0" terminal prices. Polymarket sometimes
# returns "1.0" and "0.0" as strings that round-trip cleanly through float,
# but we still want a hair of slack for legitimate 1.0 inferences.
_EXACT_TOL = 1e-9

# Near-terminal thresholds — rule 3 of §8.1.
_NEAR_TERMINAL_WIN = 0.99
_NEAR_TERMINAL_LOSS = 0.01


@dataclass(frozen=True)
class ResolutionResult:
    """Result of running the 6-rule resolution waterfall on one market."""

    winning_outcome: str | None
    resolution_method: ResolutionMethod
    resolution_confidence: float
    resolution_status: ResolutionStatus
    reason: str


def infer_resolution(payload: dict[str, Any]) -> ResolutionResult:
    """Run the 6-rule resolution waterfall on a normalized Gamma payload.

    Expected keys on ``payload``:
        - closed (bool)
        - uma_resolution_status (str | None)
        - winning_outcome (str | None) — explicit field if present in API
        - outcomes (list[str])
        - outcome_prices (list[float | None]) — terminal prices
          when ``closed`` is True

    Args:
        payload: Normalized market dict (see GammaClient._normalize_market).

    Returns:
        ResolutionResult describing winner, method, confidence, status.

    """
    explicit = _maybe_explicit_winner(payload)
    if explicit is not None:
        return ResolutionResult(
            winning_outcome=explicit,
            resolution_method="explicit_api",
            resolution_confidence=1.0,
            resolution_status="resolved",
            reason="explicit winning_outcome field present on payload",
        )

    if not _is_closed_and_uma_resolved(payload):
        return ResolutionResult(
            winning_outcome=None,
            resolution_method="unresolved",
            resolution_confidence=0.0,
            resolution_status="unresolved",
            reason="market not closed or UMA resolution not finalized",
        )

    outcomes = _string_list(payload.get("outcomes"))
    prices = _terminal_price_list(payload.get("outcome_prices"))
    if outcomes is None or prices is None or len(outcomes) != len(prices) or not outcomes:
        return ResolutionResult(
            winning_outcome=None,
            resolution_method="ambiguous",
            resolution_confidence=0.0,
            resolution_status="ambiguous",
            reason="outcomes and terminal prices not aligned or unparseable",
        )

    return _classify_by_terminal_prices(outcomes, prices)


def _maybe_explicit_winner(payload: dict[str, Any]) -> str | None:
    """Return an explicit winning-outcome string if one is on the payload."""
    explicit = payload.get("winning_outcome")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    return None


def _is_closed_and_uma_resolved(payload: dict[str, Any]) -> bool:
    """Both flags must be set before terminal-price inference is trustworthy."""
    closed = bool(payload.get("closed"))
    uma_status = payload.get("uma_resolution_status")
    if not isinstance(uma_status, str):
        return False
    return closed and uma_status.strip().lower() == "resolved"


def _string_list(value: Any) -> list[str] | None:
    """Coerce a raw outcomes field into a list of trimmed strings, or None."""
    if not isinstance(value, list):
        return None
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return None
        out.append(item.strip())
    return out


def _terminal_price_list(value: Any) -> list[float] | None:
    """Coerce terminal outcome_prices into floats; any None or NaN fails the check."""
    if not isinstance(value, list):
        return None
    out: list[float] = []
    for item in value:
        if item is None:
            return None
        try:
            price = float(item)
        except (TypeError, ValueError):
            return None
        if math.isnan(price):
            return None
        out.append(price)
    return out


def _classify_by_terminal_prices(
    outcomes: list[str],
    prices: list[float],
) -> ResolutionResult:
    """Apply rules 3-6 of §8.1 once we have aligned outcomes + terminal prices."""
    winners = [i for i, p in enumerate(prices) if p >= _NEAR_TERMINAL_WIN]
    losers = [i for i, p in enumerate(prices) if p <= _NEAR_TERMINAL_LOSS]
    if len(winners) != 1 or len(winners) + len(losers) != len(prices):
        return ResolutionResult(
            winning_outcome=None,
            resolution_method="ambiguous",
            resolution_confidence=0.0,
            resolution_status="ambiguous",
            reason="terminal prices do not identify exactly one winner under the rule",
        )

    winner_idx = winners[0]
    winner_price = prices[winner_idx]
    losers_max = max((prices[i] for i in losers), default=0.0)

    is_exact = abs(winner_price - 1.0) <= _EXACT_TOL and losers_max <= _EXACT_TOL
    if is_exact:
        return ResolutionResult(
            winning_outcome=outcomes[winner_idx],
            resolution_method="terminal_price_exact",
            resolution_confidence=0.99,
            resolution_status="resolved",
            reason="terminal prices exactly 1/0",
        )

    return ResolutionResult(
        winning_outcome=outcomes[winner_idx],
        resolution_method="terminal_price_threshold",
        resolution_confidence=0.90,
        resolution_status="resolved",
        reason=f"terminal prices within threshold (winner={winner_price})",
    )
