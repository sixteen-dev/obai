"""Bid-ask spread estimation from OHLCV data using Corwin-Schultz (2012)."""

from __future__ import annotations

from typing import Any

import numpy as np

from ..models.strategy import BARS_PER_DAY

# Corwin-Schultz (2012) uses monthly rolling windows (~20 trading sessions).
# This is the standard choice in market microstructure literature.
CS_DEFAULT_WINDOW_SESSIONS: int = 20


def cs_window_for_timeframe(timeframe: str) -> int:
    """Return the Corwin-Schultz window size scaled for a given bar timeframe.

    Maintains ~20 trading sessions of lookback regardless of bar frequency.

    Args:
        timeframe: Bar timeframe (daily, 1hour, 15min, 5min).

    Returns:
        Window size in bars.

    """
    return CS_DEFAULT_WINDOW_SESSIONS * BARS_PER_DAY.get(timeframe, 1)


def estimate_spread_corwin_schultz(
    highs: np.ndarray[Any, np.dtype[np.float64]],
    lows: np.ndarray[Any, np.dtype[np.float64]],
    window: int = 20,
) -> np.ndarray[Any, np.dtype[np.float64]]:
    """Estimate rolling bid-ask spread from high-low prices via Corwin-Schultz.

    Exploits the fact that daily high-low ranges reflect both volatility
    and spread. The spread component is separated because it affects
    adjacent single-period ranges differently than the 2-period range.

    Args:
        highs: Array of high prices.
        lows: Array of low prices.
        window: Rolling window size (bars).

    Returns:
        Array of spread estimates (as fraction of price, not bps).
        First ``window`` values are NaN.

    """
    n = len(highs)
    spreads: np.ndarray[Any, np.dtype[np.float64]] = np.full(n, np.nan)

    if n < window + 1:
        return spreads

    log_hl_sq = np.log(highs / lows) ** 2

    sqrt2 = np.sqrt(2.0)
    denom = 3.0 - 2.0 * sqrt2

    for i in range(window, n):
        h_slice = highs[i - window : i]
        l_slice = lows[i - window : i]
        lhs = log_hl_sq[i - window : i]

        beta = float(np.mean(lhs[:-1] + lhs[1:]))
        h2 = np.maximum(h_slice[:-1], h_slice[1:])
        l2 = np.minimum(l_slice[:-1], l_slice[1:])
        gamma = float(np.mean(np.log(h2 / l2) ** 2))

        alpha = (np.sqrt(2.0 * beta) - np.sqrt(beta)) / denom - np.sqrt(gamma / denom)

        spread = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
        spreads[i] = max(0.0, float(spread))

    return spreads
