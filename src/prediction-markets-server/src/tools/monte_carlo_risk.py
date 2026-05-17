"""monte_carlo_prediction_risk — IID bootstrap risk over a return series.

Accepts either a ``monte_carlo_input`` dict (the compact §10.5 payload
emitted by backtest_prediction_rule) or an inline ``returns`` list. Exactly
one of the two must be supplied — the response is shaped identically in
both cases so the agent does not need to branch on input shape.

Per §13.2, the response must always include the IID limitation language
verbatim; the constant is defined here so any change is a one-line edit
that the contract scorer will catch.
"""

from __future__ import annotations

from typing import Any

from ..engine import monte_carlo_to_dict, run_monte_carlo
from ..logging_config import get_logger

logger = get_logger(__name__)

# §13.2 required language — kept as a module constant so a future
# clustered-bootstrap mode can swap text without touching call sites.
IID_LIMITATION = (
    "Monte Carlo paths resample observed returns as if they were independent. "
    "Correlated event exposure and concurrent positions are not modeled, so "
    "drawdown tails may be optimistic."
)


def monte_carlo_prediction_risk(
    *,
    monte_carlo_input: dict[str, Any] | None = None,
    returns: list[float] | None = None,
    num_paths: int = 1_000,
    starting_bankroll: float = 1.0,
    position_fraction: float = 1.0,
    max_drawdown_limit: float = 0.30,
    seed: int = 12_345,
) -> dict[str, Any]:
    """Run IID bootstrap Monte Carlo over a return distribution.

    Args:
        monte_carlo_input: Optional dict from backtest_prediction_rule with
            ``returns`` (and metadata for fingerprint echoing).
        returns: Optional inline returns list. Use when the agent wants to
            run an ad-hoc distribution that did not come from a backtest.
        num_paths: Number of synthetic paths (bounded by engine.risk
            MAX_NUM_PATHS).
        starting_bankroll: Initial bankroll per path.
        position_fraction: Fraction of bankroll risked per step in (0, 1].
        max_drawdown_limit: Threshold for ``prob_exceeds_drawdown_limit``.
        seed: PRNG seed; identical inputs must produce identical output.

    Returns:
        Dict matching the §15 response contract with metrics, limitations,
        and quality_flags. Always contains the IID limitation text and an
        ``iid_monte_carlo_assumption`` quality flag.

    Raises:
        ValueError: If neither (or both) of monte_carlo_input / returns
            are supplied, or if the chosen returns list is empty.

    """
    chosen_returns, source_fingerprint, condition_count = _resolve_returns(
        monte_carlo_input=monte_carlo_input,
        returns=returns,
    )
    result = run_monte_carlo(
        returns=chosen_returns,
        num_paths=num_paths,
        starting_bankroll=starting_bankroll,
        position_fraction=position_fraction,
        max_drawdown_limit=max_drawdown_limit,
        seed=seed,
    )
    return {
        "tool": "monte_carlo_prediction_risk",
        "filters": {
            "num_paths": num_paths,
            "starting_bankroll": starting_bankroll,
            "position_fraction": position_fraction,
            "max_drawdown_limit": max_drawdown_limit,
            "seed": seed,
        },
        "sample_size": len(chosen_returns),
        "source_backtest_fingerprint": source_fingerprint,
        "source_market_count": condition_count,
        "metrics": monte_carlo_to_dict(result),
        "limitations": [
            IID_LIMITATION,
            "Resampling does not generate new causal evidence — paths reflect "
            "the observed historical distribution only.",
        ],
        "quality_flags": ["iid_monte_carlo_assumption"],
    }


# -- helpers ------------------------------------------------------------------


def _resolve_returns(
    *,
    monte_carlo_input: dict[str, Any] | None,
    returns: list[float] | None,
) -> tuple[list[float], str | None, int]:
    """Pick exactly one source of returns; fail loud on both/neither/empty."""
    if monte_carlo_input is None and returns is None:
        msg = "Provide either monte_carlo_input (from backtest_prediction_rule) or returns."
        raise ValueError(msg)
    if monte_carlo_input is not None and returns is not None:
        msg = "Provide monte_carlo_input OR returns, not both."
        raise ValueError(msg)
    if monte_carlo_input is not None:
        raw = monte_carlo_input.get("returns")
        if not isinstance(raw, list) or not raw:
            msg = "monte_carlo_input.returns is missing or empty."
            raise ValueError(msg)
        condition_ids = monte_carlo_input.get("condition_ids", [])
        count = len(condition_ids) if isinstance(condition_ids, list) else 0
        return (
            [float(r) for r in raw],
            monte_carlo_input.get("source_backtest_fingerprint"),
            count,
        )
    if not returns:
        msg = "returns list is empty."
        raise ValueError(msg)
    return [float(r) for r in returns], None, 0
