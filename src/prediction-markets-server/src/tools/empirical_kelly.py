"""estimate_empirical_kelly — empirical Kelly + drawdown-constrained sizing (§10.6).

Per §13.3 ("Do not output precise sizing unless bankroll/risk constraints
are supplied"), the response is qualitative when ``max_drawdown_limit`` or
``starting_bankroll`` is missing; numerical fractions only appear when the
caller commits to a risk constraint.

Returns may come from the compact ``monte_carlo_input`` payload (preferred)
or inline.
"""

from __future__ import annotations

from typing import Any

from ..engine import estimate_sizing
from ..logging_config import get_logger

logger = get_logger(__name__)


def estimate_empirical_kelly(
    *,
    monte_carlo_input: dict[str, Any] | None = None,
    returns: list[float] | None = None,
    starting_bankroll: float | None = None,
    max_drawdown_limit: float | None = None,
    confidence_haircut: float = 0.5,
    win_prob: float | None = None,
    payoff_odds: float | None = None,
    seed: int = 4_242,
) -> dict[str, Any]:
    """Estimate empirical Kelly + drawdown-constrained sizing.

    Args:
        monte_carlo_input: Optional dict from backtest_prediction_rule.
        returns: Optional inline returns list.
        starting_bankroll: Required for numerical sizing; absent → qualitative.
        max_drawdown_limit: Required for numerical sizing; absent → qualitative.
        confidence_haircut: Multiplier in [0, 1] applied to half-Kelly when
            forming the conservative fraction.
        win_prob: Optional closed-form input.
        payoff_odds: Optional closed-form input.
        seed: PRNG seed for the drawdown Monte Carlo inner loop.

    Returns:
        Dict matching the §15 response contract. Numerical fields are
        replaced with ``None`` when the user has not supplied risk
        constraints — the prompt rule then turns this into qualitative
        guidance instead of fabricated precision.

    Raises:
        ValueError: For both/neither input sources, empty returns, or
            constraint values outside their valid ranges.

    """
    chosen_returns, source_fingerprint = _resolve_returns(
        monte_carlo_input=monte_carlo_input,
        returns=returns,
    )

    if starting_bankroll is None or max_drawdown_limit is None:
        return _qualitative_response(
            chosen_returns,
            source_fingerprint=source_fingerprint,
            missing_constraints=_missing_names(starting_bankroll, max_drawdown_limit),
        )

    sizing = estimate_sizing(
        returns=chosen_returns,
        max_drawdown_limit=max_drawdown_limit,
        confidence_haircut=confidence_haircut,
        win_prob=win_prob,
        payoff_odds=payoff_odds,
        seed=seed,
    )
    return {
        "tool": "estimate_empirical_kelly",
        "filters": {
            "starting_bankroll": starting_bankroll,
            "max_drawdown_limit": max_drawdown_limit,
            "confidence_haircut": confidence_haircut,
            "win_prob": win_prob,
            "payoff_odds": payoff_odds,
            "seed": seed,
        },
        "sample_size": len(chosen_returns),
        "source_backtest_fingerprint": source_fingerprint,
        "metrics": {
            "kelly_method": sizing.estimates.method,
            "naive_kelly": sizing.estimates.naive_kelly,
            "half_kelly": sizing.estimates.half_kelly,
            "capped_kelly": sizing.capped_kelly,
            "drawdown_constrained_fraction": sizing.drawdown_constrained_fraction,
            "conservative_fraction": sizing.conservative_fraction,
        },
        "limitations": [
            "Kelly assumes returns are independent draws from the observed "
            "distribution — same IID caveat as the Monte Carlo risk tool.",
            "Drawdown-constrained fraction is the largest grid point whose "
            "p95 drawdown stays under the limit; tighter limits cut sizing.",
            "Conservative fraction is the smaller of haircut-applied half-Kelly "
            "and the drawdown-constrained fraction.",
        ],
        "quality_flags": ["iid_monte_carlo_assumption"],
    }


# -- helpers ------------------------------------------------------------------


def _qualitative_response(
    returns_list: list[float],
    *,
    source_fingerprint: str | None,
    missing_constraints: list[str],
) -> dict[str, Any]:
    """No bankroll/drawdown limit → qualitative output per §13.3."""
    return {
        "tool": "estimate_empirical_kelly",
        "filters": {
            "missing_constraints": missing_constraints,
        },
        "sample_size": len(returns_list),
        "source_backtest_fingerprint": source_fingerprint,
        "metrics": None,
        "guidance": (
            "Precise sizing requires bankroll and a drawdown limit. Without "
            "both, qualitative guidance only: prefer capped or half-Kelly "
            "sizing over naive Kelly, and treat any historical-edge estimate "
            "as base-rate evidence, not a guarantee."
        ),
        "limitations": [
            "Numerical sizing is intentionally withheld until risk constraints "
            "are supplied (§13.3).",
        ],
        "quality_flags": ["qualitative_sizing_only"],
    }


def _missing_names(starting_bankroll: float | None, max_drawdown_limit: float | None) -> list[str]:
    """Name the missing constraints so the agent can ask for them by name."""
    missing: list[str] = []
    if starting_bankroll is None:
        missing.append("starting_bankroll")
    if max_drawdown_limit is None:
        missing.append("max_drawdown_limit")
    return missing


def _resolve_returns(
    *,
    monte_carlo_input: dict[str, Any] | None,
    returns: list[float] | None,
) -> tuple[list[float], str | None]:
    """Pick exactly one source of returns."""
    if monte_carlo_input is None and returns is None:
        msg = "Provide either monte_carlo_input or returns."
        raise ValueError(msg)
    if monte_carlo_input is not None and returns is not None:
        msg = "Provide monte_carlo_input OR returns, not both."
        raise ValueError(msg)
    if monte_carlo_input is not None:
        raw = monte_carlo_input.get("returns")
        if not isinstance(raw, list) or not raw:
            msg = "monte_carlo_input.returns is missing or empty."
            raise ValueError(msg)
        return [float(r) for r in raw], monte_carlo_input.get("source_backtest_fingerprint")
    if not returns:
        msg = "returns list is empty."
        raise ValueError(msg)
    return [float(r) for r in returns], None
