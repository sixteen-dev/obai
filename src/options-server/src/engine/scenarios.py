"""Scenario analysis and position risk profiling for options portfolios.

All functions are pure (no I/O, no async). Builds on top of the pricing engine.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np

from .pricing import _normalize_option_type, bs_greeks, bs_price

# Grid axes are built symmetrically around 0.0 from the caller's requested
# ranges. Fixed odd point counts guarantee a 0% (unchanged) center cell.
_SPOT_GRID_POINTS = 7
_VOL_GRID_POINTS = 5

# Sane upper bounds on the requested ranges. Spot moves stay below 100% so a
# down-move never drives the underlying to zero/negative (bs_price would take
# the log of a non-positive spot); vol shifts cap at a large but finite width.
_MAX_SPOT_RANGE_PCT = 90.0
_MAX_VOL_SHIFT_RANGE = 200.0

# Bound on the optional forward-horizon (time-decay) axis so a caller cannot
# request an unbounded grid.
_MAX_DAYS_FORWARD = 10
_DAYS_PER_YEAR = 365.0

# Legs whose expiry_years differ by less than this (in years) are treated as
# the same expiry. Distinct listed expiries differ by at least one day
# (~0.0027 years), so this cleanly absorbs float jitter without merging real
# calendar legs.
_EXPIRY_EPSILON = 1e-6


_VALID_DIRECTIONS = frozenset({"long", "short"})


# Payoff scans run from a spot of zero to this multiple of the underlying. The
# point count keeps the spacing the earlier half-to-one-and-a-half-spot window
# used, so breakeven interpolation is no coarser for covering more ground. Every
# strike is added to the grid on top of this.
_PAYOFF_SCAN_HIGH_MULTIPLE = 1.5
_PAYOFF_SCAN_POINTS = 750


def _direction_sign(direction: str) -> int:
    """Return +1 for 'long', -1 for 'short', raising on any other value.

    The previous behavior treated *anything* other than ``"long"`` as
    ``-1``, so a bad upstream arg silently flipped a long position into a
    short. Fail loud instead.
    """
    normalized = direction.lower() if isinstance(direction, str) else ""
    if normalized not in _VALID_DIRECTIONS:
        msg = f"direction must be 'long' or 'short'; got {direction!r}"
        raise ValueError(msg)
    return 1 if normalized == "long" else -1


@dataclass(frozen=True)
class _Leg:
    """Fixed single-leg parameters shared across every grid cell.

    Bundled so the grid builders take one leg argument instead of a long
    positional parameter list.
    """

    current_price: float
    strike: float
    option_type: str
    entry_premium: float
    iv: float
    risk_free_rate: float
    dividend_yield: float
    quantity: int
    contract_multiplier: int
    sign: int


def _symmetric_grid(range_pct: float, n_points: int) -> list[float]:
    """Build ``n_points`` symmetric percentage steps spanning ±range_pct.

    Args:
        range_pct: Positive grid half-width in percent.
        n_points: Positive odd point count so 0.0 lands at the center.

    Returns:
        Ascending list from -range_pct to +range_pct that always contains 0.0.

    Raises:
        ValueError: If range_pct is non-positive or n_points is not a positive
            odd integer.
    """
    if range_pct <= 0.0:
        msg = f"range_pct must be positive; got {range_pct}"
        raise ValueError(msg)
    if n_points < 1 or n_points % 2 == 0:
        msg = f"n_points must be a positive odd integer; got {n_points}"
        raise ValueError(msg)
    return [round(float(pct), 6) for pct in np.linspace(-range_pct, range_pct, n_points)]


def _validate_ranges(spot_range_pct: float, vol_shift_range: float) -> None:
    """Guard the requested grid half-widths (percent) against absurd inputs.

    Args:
        spot_range_pct: Requested spot-move half-width in percent.
        vol_shift_range: Requested vol-shift half-width in percent.

    Raises:
        ValueError: If either range is non-positive or exceeds its cap.
    """
    if not 0.0 < spot_range_pct <= _MAX_SPOT_RANGE_PCT:
        msg = f"spot_range_pct must be in (0, {_MAX_SPOT_RANGE_PCT}]; got {spot_range_pct}"
        raise ValueError(msg)
    if not 0.0 < vol_shift_range <= _MAX_VOL_SHIFT_RANGE:
        msg = f"vol_shift_range must be in (0, {_MAX_VOL_SHIFT_RANGE}]; got {vol_shift_range}"
        raise ValueError(msg)


def _validate_days_forward(days_forward: list[int] | None) -> list[int]:
    """Validate the optional forward-horizon list; empty when None.

    Args:
        days_forward: Calendar-day horizons at which to reprice the grid, or
            None for a single-horizon (t0) grid.

    Returns:
        A copy of the horizons, or an empty list when None was given.

    Raises:
        ValueError: If more than ``_MAX_DAYS_FORWARD`` horizons are requested or
            any horizon is negative.
    """
    if days_forward is None:
        return []
    if len(days_forward) > _MAX_DAYS_FORWARD:
        msg = f"days_forward accepts at most {_MAX_DAYS_FORWARD} horizons; got {len(days_forward)}"
        raise ValueError(msg)
    if any(day < 0 for day in days_forward):
        msg = f"days_forward horizons must be non-negative; got {days_forward}"
        raise ValueError(msg)
    return list(days_forward)


def _build_pnl_grid(
    leg: _Leg,
    expiry_years: float,
    spot_changes: list[float],
    vol_changes: list[float],
) -> tuple[list[list[float]], list[float]]:
    """Price the P&L grid for one leg at a single time-to-expiry.

    Args:
        leg: Fixed leg parameters.
        expiry_years: Time to expiry (years) at which to price every cell.
        spot_changes: Spot-move percentages (grid rows).
        vol_changes: Vol-shift percentages (grid columns).

    Returns:
        Tuple of (2D P&L grid indexed [spot][vol], flat list of every P&L).
    """
    grid: list[list[float]] = []
    all_pnls: list[float] = []
    for spot_pct in spot_changes:
        new_spot = leg.current_price * (1.0 + spot_pct / 100.0)
        row: list[float] = []
        for vol_pct in vol_changes:
            new_vol = max(leg.iv * (1.0 + vol_pct / 100.0), 1e-6)  # floor at near-zero
            price = bs_price(
                new_spot,
                leg.strike,
                expiry_years,
                leg.risk_free_rate,
                new_vol,
                leg.option_type,
                leg.dividend_yield,
            )
            pnl = round(
                (price - leg.entry_premium) * leg.quantity * leg.contract_multiplier * leg.sign, 2
            )
            row.append(pnl)
            all_pnls.append(pnl)
        grid.append(row)
    return grid, all_pnls


def _build_time_decay(
    leg: _Leg,
    expiry_years: float,
    spot_changes: list[float],
    vol_changes: list[float],
    days: list[int],
) -> tuple[list[dict[str, Any]], list[float]]:
    """Reprice the grid at each forward horizon to expose time decay (theta).

    Each horizon shortens time-to-expiry by ``days / 365`` (floored at 0, so a
    horizon at/after expiry collapses to intrinsic value via ``bs_price``).

    Args:
        leg: Fixed leg parameters.
        expiry_years: Current time to expiry in years.
        spot_changes: Spot-move percentages (grid rows).
        vol_changes: Vol-shift percentages (grid columns).
        days: Validated, bounded forward horizons in calendar days.

    Returns:
        Tuple of (per-horizon dicts with days/expiry_years/pnl_grid, flat list
        of every P&L across all horizons).
    """
    horizons: list[dict[str, Any]] = []
    decay_pnls: list[float] = []
    for day in days:
        remaining = max(expiry_years - day / _DAYS_PER_YEAR, 0.0)
        grid, pnls = _build_pnl_grid(leg, remaining, spot_changes, vol_changes)
        horizons.append({"days": day, "expiry_years": round(remaining, 6), "pnl_grid": grid})
        decay_pnls.extend(pnls)
    return horizons, decay_pnls


def position_pnl_scenarios(
    current_price: float,
    strike: float,
    expiry_years: float,
    option_type: str,
    direction: str,
    quantity: int,
    entry_premium: float,
    iv: float,
    risk_free_rate: float = 0.045,
    dividend_yield: float = 0.0,
    spot_range_pct: float = 10.0,
    vol_shift_range: float = 20.0,
    contract_multiplier: int = 100,
    days_forward: list[int] | None = None,
) -> dict[str, Any]:
    """Compute P&L grid across spot-price, volatility, and time scenarios.

    The spot and vol axes are built from ``spot_range_pct``/``vol_shift_range``
    so the grid spans the move size the caller asked about (not a fixed width).
    Passing ``days_forward`` adds a time-decay dimension: the grid is repriced
    at each forward horizon so theta is visible.

    Args:
        current_price: Current underlying price.
        strike: Option strike price.
        expiry_years: Time to expiry in years.
        option_type: 'call' or 'put'.
        direction: 'long' or 'short'.
        quantity: Number of contracts (each = contract_multiplier shares).
        entry_premium: Premium paid (long) or received (short) per share.
        iv: Current implied volatility (annualized, e.g. 0.30 for 30%).
        risk_free_rate: Risk-free rate (annualized).
        dividend_yield: Continuous dividend yield (annualized). 0.0 for
            non-dividend-paying underlyings.
        spot_range_pct: Symmetric spot-move half-width in percent for the grid.
        vol_shift_range: Symmetric vol-shift half-width in percent for the grid.
        contract_multiplier: Shares per contract (default 100 for equity options).
        days_forward: Optional calendar-day horizons at which to reprice the
            grid for time decay. None yields a single t0 grid.

    Returns:
        Dict with spot_changes, vol_changes, pnl_grid, max_profit, max_loss.
        When days_forward is given, also includes days_forward and
        pnl_grid_by_day (one repriced grid per horizon), and max_profit/max_loss
        span every horizon.
    """
    sign = _direction_sign(direction)
    _validate_ranges(spot_range_pct, vol_shift_range)
    days = _validate_days_forward(days_forward)

    spot_changes = _symmetric_grid(spot_range_pct, _SPOT_GRID_POINTS)
    vol_changes = _symmetric_grid(vol_shift_range, _VOL_GRID_POINTS)
    leg = _Leg(
        current_price=current_price,
        strike=strike,
        option_type=option_type,
        entry_premium=entry_premium,
        iv=iv,
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield,
        quantity=quantity,
        contract_multiplier=contract_multiplier,
        sign=sign,
    )

    pnl_grid, all_pnls = _build_pnl_grid(leg, expiry_years, spot_changes, vol_changes)
    result: dict[str, Any] = {
        "spot_changes": spot_changes,
        "vol_changes": vol_changes,
        "pnl_grid": pnl_grid,
        "max_profit": max(all_pnls),
        "max_loss": min(all_pnls),
    }
    if not days:
        return result

    horizons, decay_pnls = _build_time_decay(leg, expiry_years, spot_changes, vol_changes, days)
    combined = all_pnls + decay_pnls
    result["days_forward"] = days
    result["pnl_grid_by_day"] = horizons
    result["max_profit"] = max(combined)
    result["max_loss"] = min(combined)
    return result


def _payoff_at_expiry(
    spot: float,
    strike: float,
    option_type: str,
    direction: str,
    quantity: int,
    entry_premium: float,
    contract_multiplier: int = 100,
) -> float:
    """Compute single-contract payoff at expiry for a given spot price.

    Args:
        spot: Underlying price at expiry.
        strike: Option strike price.
        option_type: 'call' or 'put'.
        direction: 'long' or 'short'.
        quantity: Number of contracts (each = contract_multiplier shares).
        entry_premium: Premium per share.
        contract_multiplier: Shares per contract (default 100 for equity options).

    Returns:
        Net P&L at expiry.
    """
    sign = _direction_sign(direction)
    is_call = _normalize_option_type(option_type) == "call"
    intrinsic = max(spot - strike, 0.0) if is_call else max(strike - spot, 0.0)
    return (intrinsic - entry_premium) * quantity * contract_multiplier * sign


def _has_distinct_expiries(contracts: list[dict[str, Any]]) -> bool:
    """Return True if the legs span more than one expiry (beyond float noise).

    Args:
        contracts: Legs, each carrying an ``expiry_years`` float.

    Returns:
        True when the expiry spread exceeds ``_EXPIRY_EPSILON``.
    """
    expiries = [float(c["expiry_years"]) for c in contracts]
    return (max(expiries) - min(expiries)) > _EXPIRY_EPSILON


def position_risk_profile(
    contracts: list[dict[str, Any]],
    dividend_yield: float = 0.0,
) -> dict[str, Any]:
    """Aggregate risk profile across a multi-leg options position.

    Args:
        contracts: List of contract dicts. Each must contain:
            underlying_price, strike, expiry_years, option_type,
            direction, quantity, entry_premium, iv, risk_free_rate.
            Optional: contract_multiplier (default 100).
        dividend_yield: Continuous dividend yield (annualized) applied to
            every leg's Greeks. All legs share one underlying, so a single
            yield is correct. 0.0 for non-dividend-paying underlyings.

    Returns:
        Dict with net_greeks, max_profit, max_loss, breakevens.
    """
    if not contracts:
        msg = "position_risk_profile requires at least one contract"
        raise ValueError(msg)

    underlyings = {str(c.get("underlying_symbol", "")).upper() for c in contracts}
    underlyings.discard("")
    if len(underlyings) > 1:
        msg = (
            "position_risk_profile expects all legs on the same underlying; "
            f"got {sorted(underlyings)}"
        )
        raise ValueError(msg)

    # The single-expiry intrinsic payoff curve cannot model legs at different
    # expiries (calendars/diagonals); fail loud instead of fabricating numbers.
    if _has_distinct_expiries(contracts):
        msg = "multi-expiry positions require per-leg modeling"
        raise ValueError(msg)

    # Aggregate net Greeks
    net_delta = 0.0
    net_gamma = 0.0
    net_theta = 0.0
    net_vega = 0.0

    # We'll use a representative underlying price (first contract's)
    underlying = float(contracts[0]["underlying_price"])
    for contract in contracts:
        spot = float(contract["underlying_price"])
        strike = float(contract["strike"])
        expiry = float(contract["expiry_years"])
        opt_type = str(contract["option_type"])
        direction = str(contract["direction"])
        qty = int(contract["quantity"])
        vol = float(contract["iv"])
        rate = float(contract["risk_free_rate"])
        multiplier = int(contract.get("contract_multiplier", 100))

        sign = _direction_sign(direction)
        greeks = bs_greeks(spot, strike, expiry, rate, vol, opt_type, dividend_yield)

        net_delta += greeks["delta"] * qty * multiplier * sign
        net_gamma += greeks["gamma"] * qty * multiplier * sign
        net_theta += greeks["theta"] * qty * multiplier * sign
        net_vega += greeks["vega"] * qty * multiplier * sign

    # Compute payoff curve at expiry across the underlying's whole domain up to
    # a high cut-off. The scan starts at zero because that is a real bound the
    # underlying cannot cross, not an arbitrary window edge.
    high = underlying * _PAYOFF_SCAN_HIGH_MULTIPLE
    # The payoff bends only at a strike, so every exact extreme sits on a
    # strike or a scan edge. Sampling alone lands beside them and understates
    # the worst case -- a straddle's trough is exactly at its strike.
    kinks = [float(c["strike"]) for c in contracts if 0.0 < float(c["strike"]) < high]
    price_range = np.unique(
        np.concatenate([np.linspace(0.0, high, _PAYOFF_SCAN_POINTS), np.array(kinks)])
    )

    payoffs: list[float] = []
    for price_point in price_range:
        total_pnl = 0.0
        for contract in contracts:
            strike = float(contract["strike"])
            opt_type = str(contract["option_type"])
            direction = str(contract["direction"])
            qty = int(contract["quantity"])
            premium = float(contract["entry_premium"])
            multiplier = int(contract.get("contract_multiplier", 100))
            total_pnl += _payoff_at_expiry(
                float(price_point), strike, opt_type, direction, qty, premium, multiplier
            )
        payoffs.append(total_pnl)

    max_profit: float | str = round(max(payoffs), 2)
    max_loss: float | str = round(min(payoffs), 2)

    # Only the high side is open. A payoff still sloped at the top of the scan
    # keeps going, because the underlying has no ceiling; one still sloped at
    # the bottom has already reached a spot of zero, so payoffs[0] is the true
    # extreme rather than a value to extrapolate past.
    # The slope at that open edge is what decides it, not whether the edge
    # holds the extreme: a long straddle keeps rising above its call strike
    # while its deepest point sits at a spot of zero on the put leg.
    if payoffs[-1] > payoffs[-2]:
        max_profit = "unlimited"
    if payoffs[-1] < payoffs[-2]:
        max_loss = "unlimited"

    # Find zero-crossings for breakevens
    breakevens: list[float] = []
    for idx in range(len(payoffs) - 1):
        if payoffs[idx] * payoffs[idx + 1] < 0:
            # Linear interpolation between adjacent points
            p1, p2 = payoffs[idx], payoffs[idx + 1]
            s1, s2 = float(price_range[idx]), float(price_range[idx + 1])
            cross = s1 + (s2 - s1) * (-p1) / (p2 - p1)
            breakevens.append(round(cross, 2))

    return {
        "net_greeks": {
            "delta": round(net_delta, 4),
            "gamma": round(net_gamma, 6),
            "theta": round(net_theta, 4),
            "vega": round(net_vega, 4),
        },
        "max_profit": max_profit,
        "max_loss": max_loss,
        "breakevens": breakevens,
    }
