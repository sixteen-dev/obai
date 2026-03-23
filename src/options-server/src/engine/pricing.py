"""Black-Scholes pricing, Greeks computation, and implied volatility solver.

All functions are pure (no I/O, no async). Uses math.erf for the normal CDF
to avoid a scipy dependency.
"""

import math


def _norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function.

    Args:
        x: Input value.

    Returns:
        Probability that a standard normal RV is <= x.
    """
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard normal probability density function.

    Args:
        x: Input value.

    Returns:
        Density at x.
    """
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1(spot: float, strike: float, time: float, rate: float, sigma: float) -> float:
    """Compute Black-Scholes d1.

    Args:
        spot: Current underlying price.
        strike: Option strike price.
        time: Time to expiry in years.
        rate: Risk-free interest rate (annualized).
        sigma: Volatility (annualized).

    Returns:
        d1 value.
    """
    return (math.log(spot / strike) + (rate + 0.5 * sigma**2) * time) / (sigma * math.sqrt(time))


def _d2(d1_val: float, sigma: float, time: float) -> float:
    """Compute Black-Scholes d2.

    Args:
        d1_val: Pre-computed d1.
        sigma: Volatility (annualized).
        time: Time to expiry in years.

    Returns:
        d2 value.
    """
    return d1_val - sigma * math.sqrt(time)


def _intrinsic(spot: float, strike: float, option_type: str) -> float:
    """Compute intrinsic value of an option.

    Args:
        spot: Current underlying price.
        strike: Option strike price.
        option_type: 'call' or 'put'.

    Returns:
        Intrinsic value (floored at 0).
    """
    if option_type == "call":
        return max(spot - strike, 0.0)
    return max(strike - spot, 0.0)


def bs_price(
    spot: float,
    strike: float,
    time: float,
    rate: float,
    sigma: float,
    option_type: str,
) -> float:
    """Compute Black-Scholes option price.

    Args:
        spot: Current underlying price.
        strike: Option strike price.
        time: Time to expiry in years.
        rate: Risk-free interest rate (annualized).
        sigma: Volatility (annualized).
        option_type: 'call' or 'put'.

    Returns:
        Theoretical option price.
    """
    if time <= 0.0 or sigma <= 0.0:
        return _intrinsic(spot, strike, option_type)

    d1_val = _d1(spot, strike, time, rate, sigma)
    d2_val = _d2(d1_val, sigma, time)

    if option_type == "call":
        return spot * _norm_cdf(d1_val) - strike * math.exp(-rate * time) * _norm_cdf(d2_val)
    return strike * math.exp(-rate * time) * _norm_cdf(-d2_val) - spot * _norm_cdf(-d1_val)


def bs_greeks(
    spot: float,
    strike: float,
    time: float,
    rate: float,
    sigma: float,
    option_type: str,
) -> dict[str, float]:
    """Compute Black-Scholes Greeks for an option.

    Args:
        spot: Current underlying price.
        strike: Option strike price.
        time: Time to expiry in years.
        rate: Risk-free interest rate (annualized).
        sigma: Volatility (annualized).
        option_type: 'call' or 'put'.

    Returns:
        Dict with keys: delta, gamma, theta, vega, rho.
        Vega and rho are per 1% move (divided by 100).
    """
    if time <= 0.0 or sigma <= 0.0:
        delta = 1.0 if option_type == "call" and spot > strike else 0.0
        if option_type == "put":
            delta = -1.0 if spot < strike else 0.0
        return {"delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}

    d1_val = _d1(spot, strike, time, rate, sigma)
    d2_val = _d2(d1_val, sigma, time)
    sqrt_t = math.sqrt(time)
    exp_rt = math.exp(-rate * time)
    pdf_d1 = _norm_pdf(d1_val)

    # Gamma is the same for calls and puts
    gamma = pdf_d1 / (spot * sigma * sqrt_t)

    # Vega is the same for calls and puts (per 1% vol move)
    vega = spot * pdf_d1 * sqrt_t / 100.0

    if option_type == "call":
        delta = _norm_cdf(d1_val)
        theta = -(spot * pdf_d1 * sigma) / (2.0 * sqrt_t) - rate * strike * exp_rt * _norm_cdf(
            d2_val
        )
        rho = strike * time * exp_rt * _norm_cdf(d2_val) / 100.0
    else:
        delta = _norm_cdf(d1_val) - 1.0
        theta = -(spot * pdf_d1 * sigma) / (2.0 * sqrt_t) + rate * strike * exp_rt * _norm_cdf(
            -d2_val
        )
        rho = -strike * time * exp_rt * _norm_cdf(-d2_val) / 100.0

    return {
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
        "rho": rho,
    }


def implied_vol(
    market_price: float,
    spot: float,
    strike: float,
    time: float,
    rate: float,
    option_type: str,
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float:
    """Solve for implied volatility using Newton-Raphson.

    Args:
        market_price: Observed market price of the option.
        spot: Current underlying price.
        strike: Option strike price.
        time: Time to expiry in years.
        rate: Risk-free interest rate (annualized).
        option_type: 'call' or 'put'.
        tol: Convergence tolerance.
        max_iter: Maximum Newton-Raphson iterations.

    Returns:
        Implied volatility, or float('nan') if solver fails to converge.
    """
    if time <= 0.0:
        return float("nan")

    vol = 0.3  # Starting guess: 30% annualized vol

    for _ in range(max_iter):
        price = bs_price(spot, strike, time, rate, vol, option_type)
        diff = price - market_price

        if abs(diff) < tol:
            return vol

        # Vega in raw units (not per-1%)
        d1_val = _d1(spot, strike, time, rate, vol)
        vega_raw = spot * _norm_pdf(d1_val) * math.sqrt(time)

        if vega_raw < 1e-12:
            break

        vol = vol - diff / vega_raw

        # Guard against negative vol
        if vol <= 0.0:
            vol = 1e-6

    return float("nan")


def breakeven_at_expiry(
    strike: float,
    premium: float,
    option_type: str,
    direction: str,
) -> float:
    """Compute the breakeven price at expiry.

    Args:
        strike: Option strike price.
        premium: Entry premium paid (or received).
        option_type: 'call' or 'put'.
        direction: 'long' or 'short'.

    Returns:
        Breakeven underlying price at expiry.
    """
    # Long and short have same breakeven — the difference is
    # which side profits above/below it.
    if option_type == "call":
        return strike + premium
    return strike - premium
