"""Bootstrap Monte Carlo risk over a return series (§10.5 + §13.2).

Pure functions over a list of per-trade returns. Seeded for reproducibility;
the seed is required so the caller cannot silently drift between calls.

V1 is IID bootstrap — sample returns with replacement and apply a fixed
position fraction of bankroll on each step. Block bootstrap and
event-cluster bootstrap are out of scope until Phase 6 work introduces
per-event correlation tagging.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Literal

SamplingMethod = Literal["iid_bootstrap"]

# Hard cap on path count — keeps tool responses bounded without forcing
# every caller to pass a number. Above the cap we fail loud so users
# notice they\'re asking for something the tool will not deliver.
MAX_NUM_PATHS = 10_000

# Per-path step count cap (length of the resampled return sequence). Equal
# to the size of the observed return distribution by default — bootstrapping
# more steps than observations is allowed but the caller must opt in.
DEFAULT_STEPS_PER_PATH = 0  # 0 = use len(returns)


@dataclass(frozen=True)
class MonteCarloResult:
    """Aggregated risk stats from a Monte Carlo bootstrap run."""

    sampling_method: SamplingMethod
    num_paths: int
    steps_per_path: int
    seed: int
    starting_bankroll: float
    position_fraction: float
    max_drawdown_limit: float
    median_terminal_wealth: float
    p5_terminal_wealth: float
    p95_terminal_wealth: float
    median_max_drawdown: float
    p95_max_drawdown: float
    p99_max_drawdown: float
    prob_exceeds_drawdown_limit: float
    ruin_probability: float


def run_monte_carlo(
    *,
    returns: list[float],
    num_paths: int,
    starting_bankroll: float,
    position_fraction: float,
    max_drawdown_limit: float,
    seed: int,
    steps_per_path: int = DEFAULT_STEPS_PER_PATH,
) -> MonteCarloResult:
    """Run an IID bootstrap Monte Carlo over a return series.

    Args:
        returns: Per-trade return-on-cost values (e.g. monte_carlo_input.returns).
        num_paths: How many synthetic equity paths to simulate. Bounded by
            ``MAX_NUM_PATHS``.
        starting_bankroll: Initial bankroll for each path.
        position_fraction: Fraction of bankroll risked per step in (0, 1].
        max_drawdown_limit: Drawdown threshold used to compute
            ``prob_exceeds_drawdown_limit`` (e.g. 0.30 for a 30% cap).
        seed: PRNG seed; the same seed must produce identical output.
        steps_per_path: Number of resampled returns per path; 0 (default)
            uses ``len(returns)``.

    Returns:
        MonteCarloResult.

    Raises:
        ValueError: For empty returns, out-of-range fractions, or
            ``num_paths`` over the cap.

    """
    _validate_inputs(
        returns=returns,
        num_paths=num_paths,
        position_fraction=position_fraction,
        starting_bankroll=starting_bankroll,
        max_drawdown_limit=max_drawdown_limit,
    )
    effective_steps = steps_per_path if steps_per_path > 0 else len(returns)
    rng = random.Random(seed)
    terminal_wealths: list[float] = []
    max_drawdowns: list[float] = []
    exceeded = 0
    ruined = 0

    for _ in range(num_paths):
        sampled = [rng.choice(returns) for _ in range(effective_steps)]
        terminal, max_dd, hit_ruin = _simulate_path(
            sampled,
            starting_bankroll=starting_bankroll,
            position_fraction=position_fraction,
        )
        terminal_wealths.append(terminal)
        max_drawdowns.append(max_dd)
        if max_dd > max_drawdown_limit:
            exceeded += 1
        if hit_ruin:
            ruined += 1

    return MonteCarloResult(
        sampling_method="iid_bootstrap",
        num_paths=num_paths,
        steps_per_path=effective_steps,
        seed=seed,
        starting_bankroll=starting_bankroll,
        position_fraction=position_fraction,
        max_drawdown_limit=max_drawdown_limit,
        median_terminal_wealth=_percentile(terminal_wealths, 0.5),
        p5_terminal_wealth=_percentile(terminal_wealths, 0.05),
        p95_terminal_wealth=_percentile(terminal_wealths, 0.95),
        median_max_drawdown=_percentile(max_drawdowns, 0.5),
        p95_max_drawdown=_percentile(max_drawdowns, 0.95),
        p99_max_drawdown=_percentile(max_drawdowns, 0.99),
        prob_exceeds_drawdown_limit=exceeded / num_paths,
        ruin_probability=ruined / num_paths,
    )


def result_to_dict(result: MonteCarloResult) -> dict[str, Any]:
    """Render a MonteCarloResult as a JSON-friendly dict (rounded to 6 dp)."""
    return {
        "sampling_method": result.sampling_method,
        "num_paths": result.num_paths,
        "steps_per_path": result.steps_per_path,
        "seed": result.seed,
        "starting_bankroll": result.starting_bankroll,
        "position_fraction": result.position_fraction,
        "max_drawdown_limit": result.max_drawdown_limit,
        "median_terminal_wealth": round(result.median_terminal_wealth, 6),
        "p5_terminal_wealth": round(result.p5_terminal_wealth, 6),
        "p95_terminal_wealth": round(result.p95_terminal_wealth, 6),
        "median_max_drawdown": round(result.median_max_drawdown, 6),
        "p95_max_drawdown": round(result.p95_max_drawdown, 6),
        "p99_max_drawdown": round(result.p99_max_drawdown, 6),
        "prob_exceeds_drawdown_limit": round(result.prob_exceeds_drawdown_limit, 6),
        "ruin_probability": round(result.ruin_probability, 6),
    }


# -- helpers ------------------------------------------------------------------


def _simulate_path(
    returns: list[float],
    *,
    starting_bankroll: float,
    position_fraction: float,
) -> tuple[float, float, bool]:
    """Walk one resampled return path; return (terminal, max_dd, ruin_hit).

    Drawdown is peak-to-trough on the equity curve. Ruin is a bankroll
    at or below zero at any point — once ruined we stop compounding so
    the path does not magically recover from negative wealth.
    """
    wealth = starting_bankroll
    peak = wealth
    max_drawdown = 0.0
    ruined = False
    for r in returns:
        if ruined:
            continue
        position_size = wealth * position_fraction
        wealth = wealth + position_size * r
        if wealth <= 0.0:
            ruined = True
            wealth = 0.0
        peak = max(peak, wealth)
        drawdown = (peak - wealth) / peak if peak > 0 else 0.0
        max_drawdown = max(max_drawdown, drawdown)
    return wealth, max_drawdown, ruined


def _percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile (numpy parity, no numpy dependency here)."""
    if not values:
        return 0.0
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]
    idx = q * (len(sorted_values) - 1)
    lower = math.floor(idx)
    upper = math.ceil(idx)
    if lower == upper:
        return sorted_values[lower]
    fraction = idx - lower
    return sorted_values[lower] + fraction * (sorted_values[upper] - sorted_values[lower])


def _validate_inputs(
    *,
    returns: list[float],
    num_paths: int,
    position_fraction: float,
    starting_bankroll: float,
    max_drawdown_limit: float,
) -> None:
    """Fail loud on bad inputs."""
    if not returns:
        msg = "returns is empty; Monte Carlo needs at least one observation"
        raise ValueError(msg)
    if num_paths <= 0:
        msg = f"num_paths must be positive; got {num_paths}"
        raise ValueError(msg)
    if num_paths > MAX_NUM_PATHS:
        msg = (
            f"num_paths {num_paths} exceeds MAX_NUM_PATHS={MAX_NUM_PATHS}; "
            "narrow the request or raise the cap deliberately."
        )
        raise ValueError(msg)
    if not 0.0 < position_fraction <= 1.0:
        msg = f"position_fraction must be in (0, 1]; got {position_fraction}"
        raise ValueError(msg)
    if starting_bankroll <= 0.0:
        msg = f"starting_bankroll must be positive; got {starting_bankroll}"
        raise ValueError(msg)
    if not 0.0 < max_drawdown_limit <= 1.0:
        msg = f"max_drawdown_limit must be in (0, 1]; got {max_drawdown_limit}"
        raise ValueError(msg)
