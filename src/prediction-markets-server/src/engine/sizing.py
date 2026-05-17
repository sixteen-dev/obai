"""Empirical Kelly + drawdown-constrained sizing (§10.6, §13.3).

Pure functions over a list of per-trade returns. Closed-form Kelly is used
when ``win_prob`` and ``payoff_odds`` are supplied; otherwise we fall back
to a grid search over fractions in ``KELLY_GRID`` that maximises mean
log-growth on the observed return distribution.

Drawdown-constrained sizing runs an inner Monte Carlo per candidate
fraction and picks the largest fraction whose p95 drawdown stays below
the caller's limit. The Monte Carlo path is the same engine.risk module so
the math cannot drift.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .risk import run_monte_carlo

# Coarse-then-fine grid keeps the runtime bounded without making the
# top end of "what fraction is still safe?" depend on a magic step size.
KELLY_GRID: tuple[float, ...] = tuple(round(i * 0.01, 4) for i in range(1, 101))

# Default Monte Carlo path count for drawdown-constrained sizing — modest
# because we run it K times (once per fraction).
_DRAWDOWN_MC_PATHS = 500
_DRAWDOWN_MC_SEED = 4242


@dataclass(frozen=True)
class KellyEstimates:
    """Output of estimate_kelly."""

    naive_kelly: float
    half_kelly: float
    capped_kelly: float  # min(naive, 0.5) — common production cap
    method: str  # "closed_form" | "grid_search"


@dataclass(frozen=True)
class SizingResult:
    """Output of estimate_sizing — bundles Kelly + drawdown-constrained."""

    estimates: KellyEstimates
    drawdown_constrained_fraction: float
    conservative_fraction: float
    confidence_haircut: float
    max_drawdown_limit: float


def estimate_kelly(
    *,
    returns: list[float],
    win_prob: float | None = None,
    payoff_odds: float | None = None,
) -> KellyEstimates:
    """Estimate naive Kelly + half/capped Kelly.

    Args:
        returns: Per-trade return-on-cost values.
        win_prob: Optional. If both ``win_prob`` and ``payoff_odds`` are
            provided, closed-form Kelly is used (``p - q/b``); else
            grid search.
        payoff_odds: Optional. Net odds received on a winning bet (e.g.
            (1 - p) / p for a YES contract bought at p). Pairs with
            ``win_prob``.

    Returns:
        KellyEstimates with ``method`` describing the path taken.

    Raises:
        ValueError: For empty ``returns`` or inconsistent closed-form
            inputs.

    """
    if not returns:
        msg = "returns is empty; Kelly needs at least one observation"
        raise ValueError(msg)
    if win_prob is not None and payoff_odds is not None:
        return _closed_form_kelly(win_prob=win_prob, payoff_odds=payoff_odds)
    if (win_prob is None) != (payoff_odds is None):
        msg = (
            "Provide both win_prob and payoff_odds for closed-form Kelly, "
            "or neither for grid search."
        )
        raise ValueError(msg)
    return _grid_search_kelly(returns)


def drawdown_constrained_fraction(
    *,
    returns: list[float],
    max_drawdown_limit: float,
    num_paths: int = _DRAWDOWN_MC_PATHS,
    seed: int = _DRAWDOWN_MC_SEED,
) -> float:
    """Pick the largest grid fraction whose p95 drawdown stays under the limit.

    Runs an inner Monte Carlo per candidate fraction (engine.risk.run_monte_carlo).
    Returns 0.0 when the smallest tested fraction already breaches the limit.

    Args:
        returns: Per-trade return-on-cost values.
        max_drawdown_limit: Max allowable p95 drawdown in (0, 1].
        num_paths: Inner Monte Carlo path count per fraction.
        seed: PRNG seed.

    Returns:
        Largest fraction in KELLY_GRID whose Monte Carlo p95 drawdown
        is <= ``max_drawdown_limit``.

    """
    if not returns:
        msg = "returns is empty; drawdown_constrained_fraction needs observations"
        raise ValueError(msg)
    best = 0.0
    for fraction in KELLY_GRID:
        mc = run_monte_carlo(
            returns=returns,
            num_paths=num_paths,
            starting_bankroll=1.0,
            position_fraction=fraction,
            max_drawdown_limit=max_drawdown_limit,
            seed=seed,
        )
        if mc.p95_max_drawdown <= max_drawdown_limit:
            best = fraction
        else:
            # Drawdown curve is approximately monotone in fraction; once we
            # cross the limit, larger fractions will only be worse. Break
            # early to keep this affordable.
            break
    return best


def estimate_sizing(
    *,
    returns: list[float],
    max_drawdown_limit: float,
    confidence_haircut: float,
    win_prob: float | None = None,
    payoff_odds: float | None = None,
    num_paths: int = _DRAWDOWN_MC_PATHS,
    seed: int = _DRAWDOWN_MC_SEED,
) -> SizingResult:
    """Top-level sizing: combine Kelly + drawdown-constrained + haircut.

    The ``conservative_fraction`` is the minimum of the haircut-applied
    half-Kelly and the drawdown-constrained fraction — the design picks
    the more cautious of the two so the response cannot under-state risk.

    Args:
        returns: Per-trade return-on-cost values.
        max_drawdown_limit: Max allowable p95 drawdown.
        confidence_haircut: Multiplier in [0, 1] to scale Kelly estimates.
        win_prob: Optional closed-form input.
        payoff_odds: Optional closed-form input.
        num_paths: Inner Monte Carlo path count.
        seed: PRNG seed.

    Returns:
        SizingResult.

    """
    if not 0.0 <= confidence_haircut <= 1.0:
        msg = f"confidence_haircut must be in [0, 1]; got {confidence_haircut}"
        raise ValueError(msg)
    estimates = estimate_kelly(returns=returns, win_prob=win_prob, payoff_odds=payoff_odds)
    drawdown_fraction = drawdown_constrained_fraction(
        returns=returns,
        max_drawdown_limit=max_drawdown_limit,
        num_paths=num_paths,
        seed=seed,
    )
    half_haircut = estimates.half_kelly * confidence_haircut
    conservative = min(half_haircut, drawdown_fraction)
    return SizingResult(
        estimates=estimates,
        drawdown_constrained_fraction=drawdown_fraction,
        conservative_fraction=conservative,
        confidence_haircut=confidence_haircut,
        max_drawdown_limit=max_drawdown_limit,
    )


# -- helpers ------------------------------------------------------------------


def _closed_form_kelly(*, win_prob: float, payoff_odds: float) -> KellyEstimates:
    """Closed-form binary Kelly: f* = p - (1-p)/b, clamped to [0, 1]."""
    if not 0.0 <= win_prob <= 1.0:
        msg = f"win_prob must be in [0, 1]; got {win_prob}"
        raise ValueError(msg)
    if payoff_odds <= 0.0:
        msg = f"payoff_odds must be positive; got {payoff_odds}"
        raise ValueError(msg)
    naive = win_prob - (1.0 - win_prob) / payoff_odds
    naive = max(min(naive, 1.0), 0.0)
    return KellyEstimates(
        naive_kelly=round(naive, 6),
        half_kelly=round(naive * 0.5, 6),
        capped_kelly=round(min(naive, 0.5), 6),
        method="closed_form",
    )


def _grid_search_kelly(returns: list[float]) -> KellyEstimates:
    """Find the fraction in KELLY_GRID maximising mean log-growth."""
    best_fraction = 0.0
    best_growth = -math.inf
    for fraction in KELLY_GRID:
        growth = _expected_log_growth(returns, fraction)
        if growth > best_growth:
            best_growth = growth
            best_fraction = fraction
    return KellyEstimates(
        naive_kelly=round(best_fraction, 6),
        half_kelly=round(best_fraction * 0.5, 6),
        capped_kelly=round(min(best_fraction, 0.5), 6),
        method="grid_search",
    )


def _expected_log_growth(returns: list[float], fraction: float) -> float:
    """E[log(1 + f * r)] over the observed return distribution.

    Returns -inf when any sample would push 1 + f*r <= 0 (bankrupt
    candidate) so the maximiser never lands on a fraction that admits
    a single-step wipe-out.
    """
    log_terms: list[float] = []
    for r in returns:
        wealth_after = 1.0 + fraction * r
        if wealth_after <= 0.0:
            return -math.inf
        log_terms.append(math.log(wealth_after))
    return sum(log_terms) / len(log_terms)
