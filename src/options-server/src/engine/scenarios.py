"""Scenario analysis and position risk profiling for options portfolios.

All functions are pure (no I/O, no async). Builds on top of the pricing engine.
"""

from typing import Any

import numpy as np

from .pricing import bs_greeks, bs_price

_SPOT_PCTS = [-10.0, -5.0, -2.0, 0.0, 2.0, 5.0, 10.0]
_VOL_PCTS = [-20.0, -10.0, 0.0, 10.0, 20.0]


_VALID_DIRECTIONS = frozenset({"long", "short"})


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
    spot_range_pct: float = 10.0,
    vol_shift_range: float = 20.0,
    contract_multiplier: int = 100,
) -> dict[str, Any]:
    """Compute P&L grid across spot-price and volatility scenarios.

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
        spot_range_pct: Max spot change percentage for the grid.
        vol_shift_range: Max vol shift percentage for the grid.
        contract_multiplier: Shares per contract (default 100 for equity options).

    Returns:
        Dict with spot_changes, vol_changes, pnl_grid, max_profit, max_loss.
    """
    sign = _direction_sign(direction)
    spot_changes = _SPOT_PCTS
    vol_changes = _VOL_PCTS

    pnl_grid: list[list[float]] = []
    all_pnls: list[float] = []

    for spot_pct in spot_changes:
        row: list[float] = []
        new_spot = current_price * (1.0 + spot_pct / 100.0)

        for vol_pct in vol_changes:
            new_vol = iv * (1.0 + vol_pct / 100.0)
            new_vol = max(new_vol, 1e-6)  # floor at near-zero

            new_price = bs_price(
                new_spot, strike, expiry_years, risk_free_rate, new_vol, option_type
            )
            pnl = (new_price - entry_premium) * quantity * contract_multiplier * sign
            pnl = round(pnl, 2)
            row.append(pnl)
            all_pnls.append(pnl)

        pnl_grid.append(row)

    return {
        "spot_changes": spot_changes,
        "vol_changes": vol_changes,
        "pnl_grid": pnl_grid,
        "max_profit": max(all_pnls),
        "max_loss": min(all_pnls),
    }


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
    intrinsic = max(spot - strike, 0.0) if option_type == "call" else max(strike - spot, 0.0)
    return (intrinsic - entry_premium) * quantity * contract_multiplier * sign


def position_risk_profile(contracts: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate risk profile across a multi-leg options position.

    Args:
        contracts: List of contract dicts. Each must contain:
            underlying_price, strike, expiry_years, option_type,
            direction, quantity, entry_premium, iv, risk_free_rate.
            Optional: contract_multiplier (default 100).

    Returns:
        Dict with net_greeks, max_profit, max_loss, breakevens.
    """
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
        greeks = bs_greeks(spot, strike, expiry, rate, vol, opt_type)

        net_delta += greeks["delta"] * qty * multiplier * sign
        net_gamma += greeks["gamma"] * qty * multiplier * sign
        net_theta += greeks["theta"] * qty * multiplier * sign
        net_vega += greeks["vega"] * qty * multiplier * sign

    # Compute payoff curve at expiry across a price range
    low = underlying * 0.5
    high = underlying * 1.5
    price_range = np.linspace(low, high, 500)

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

    # Detect unlimited profit/loss at boundaries (check BOTH sides)
    if payoffs[-1] == max(payoffs) and payoffs[-1] > payoffs[-2]:
        max_profit = "unlimited"
    if payoffs[0] == max(payoffs) and payoffs[0] > payoffs[1]:
        max_profit = "unlimited"

    if payoffs[0] == min(payoffs) and payoffs[0] < payoffs[1]:
        max_loss = "unlimited"
    if payoffs[-1] == min(payoffs) and payoffs[-1] < payoffs[-2]:
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
