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


def _d1(
    spot: float,
    strike: float,
    time: float,
    rate: float,
    sigma: float,
    dividend_yield: float = 0.0,
) -> float:
    """Compute Black-Scholes-Merton d1 with continuous dividend yield.

    Args:
        spot: Current underlying price.
        strike: Option strike price.
        time: Time to expiry in years.
        rate: Risk-free interest rate (annualized).
        sigma: Volatility (annualized).
        dividend_yield: Continuous dividend yield (annualized). 0.0 for
            non-dividend-paying underlyings and indices priced ex-dividend.

    Returns:
        d1 value.
    """
    drift = rate - dividend_yield + 0.5 * sigma**2
    return (math.log(spot / strike) + drift * time) / (sigma * math.sqrt(time))


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


_VALID_OPTION_TYPES = frozenset({"call", "put"})


def _normalize_option_type(option_type: str) -> str:
    """Return ``option_type`` lower-cased, raising on anything else.

    Without this guard the pricing functions silently treat any
    non-``"call"`` string as a put — a bad upstream argument flips the
    payoff interpretation instead of failing loudly.
    """
    normalized = option_type.lower() if isinstance(option_type, str) else ""
    if normalized not in _VALID_OPTION_TYPES:
        msg = f"option_type must be 'call' or 'put'; got {option_type!r}"
        raise ValueError(msg)
    return normalized


def _intrinsic(spot: float, strike: float, option_type: str) -> float:
    """Compute intrinsic value of an option.

    Args:
        spot: Current underlying price.
        strike: Option strike price.
        option_type: 'call' or 'put'.

    Returns:
        Intrinsic value (floored at 0).
    """
    if _normalize_option_type(option_type) == "call":
        return max(spot - strike, 0.0)
    return max(strike - spot, 0.0)


def bs_price(
    spot: float,
    strike: float,
    time: float,
    rate: float,
    sigma: float,
    option_type: str,
    dividend_yield: float = 0.0,
) -> float:
    """Compute Black-Scholes-Merton option price.

    Args:
        spot: Current underlying price.
        strike: Option strike price.
        time: Time to expiry in years.
        rate: Risk-free interest rate (annualized).
        sigma: Volatility (annualized).
        option_type: 'call' or 'put'.
        dividend_yield: Continuous dividend yield (annualized). 0.0
            preserves the original Black-Scholes (no-dividend) behavior.

    Returns:
        Theoretical option price.
    """
    option_type = _normalize_option_type(option_type)
    if time <= 0.0 or sigma <= 0.0:
        return _intrinsic(spot, strike, option_type)

    d1_val = _d1(spot, strike, time, rate, sigma, dividend_yield)
    d2_val = _d2(d1_val, sigma, time)
    disc_div = math.exp(-dividend_yield * time)
    disc_rate = math.exp(-rate * time)

    if option_type == "call":
        return spot * disc_div * _norm_cdf(d1_val) - strike * disc_rate * _norm_cdf(d2_val)
    return strike * disc_rate * _norm_cdf(-d2_val) - spot * disc_div * _norm_cdf(-d1_val)


def bs_greeks(
    spot: float,
    strike: float,
    time: float,
    rate: float,
    sigma: float,
    option_type: str,
    dividend_yield: float = 0.0,
) -> dict[str, float]:
    """Compute Black-Scholes-Merton Greeks for an option.

    Args:
        spot: Current underlying price.
        strike: Option strike price.
        time: Time to expiry in years.
        rate: Risk-free interest rate (annualized).
        sigma: Volatility (annualized).
        option_type: 'call' or 'put'.
        dividend_yield: Continuous dividend yield (annualized). 0.0
            preserves the original (no-dividend) behavior.

    Returns:
        Dict with keys: delta, gamma, theta, vega, rho.
        Vega and rho are per 1% move (divided by 100).
    """
    option_type = _normalize_option_type(option_type)
    if time <= 0.0 or sigma <= 0.0:
        delta = 1.0 if option_type == "call" and spot > strike else 0.0
        if option_type == "put":
            delta = -1.0 if spot < strike else 0.0
        return {"delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}

    d1_val = _d1(spot, strike, time, rate, sigma, dividend_yield)
    d2_val = _d2(d1_val, sigma, time)
    sqrt_t = math.sqrt(time)
    exp_rt = math.exp(-rate * time)
    exp_qt = math.exp(-dividend_yield * time)
    pdf_d1 = _norm_pdf(d1_val)

    # Gamma is the same for calls and puts (with dividend discount on spot)
    gamma = exp_qt * pdf_d1 / (spot * sigma * sqrt_t)

    # Vega is the same for calls and puts (per 1% vol move)
    vega = spot * exp_qt * pdf_d1 * sqrt_t / 100.0

    common_theta = -(spot * exp_qt * pdf_d1 * sigma) / (2.0 * sqrt_t)
    if option_type == "call":
        delta = exp_qt * _norm_cdf(d1_val)
        theta = (
            common_theta
            + dividend_yield * spot * exp_qt * _norm_cdf(d1_val)
            - rate * strike * exp_rt * _norm_cdf(d2_val)
        )
        rho = strike * time * exp_rt * _norm_cdf(d2_val) / 100.0
    else:
        delta = -exp_qt * _norm_cdf(-d1_val)
        theta = (
            common_theta
            - dividend_yield * spot * exp_qt * _norm_cdf(-d1_val)
            + rate * strike * exp_rt * _norm_cdf(-d2_val)
        )
        rho = -strike * time * exp_rt * _norm_cdf(-d2_val) / 100.0

    return {
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
        "rho": rho,
    }


_IV_LOWER = 1e-4
_IV_UPPER = 5.0
_BISECT_MAX_ITER = 100


def _bisect_iv(
    market_price: float,
    spot: float,
    strike: float,
    time: float,
    rate: float,
    option_type: str,
    dividend_yield: float,
    tol: float,
) -> float:
    """Bisection IV solver — slower than Newton but always converges
    when the root is bracketed in ``[_IV_LOWER, _IV_UPPER]``.

    Returns ``nan`` if the price lies outside the achievable range.
    """
    lo, hi = _IV_LOWER, _IV_UPPER
    f_lo = bs_price(spot, strike, time, rate, lo, option_type, dividend_yield) - market_price
    f_hi = bs_price(spot, strike, time, rate, hi, option_type, dividend_yield) - market_price
    if f_lo * f_hi > 0.0:
        return float("nan")

    for _ in range(_BISECT_MAX_ITER):
        mid = 0.5 * (lo + hi)
        f_mid = bs_price(spot, strike, time, rate, mid, option_type, dividend_yield) - market_price
        if abs(f_mid) < tol or (hi - lo) < tol:
            return mid
        if f_lo * f_mid < 0.0:
            hi = mid
            f_hi = f_mid
        else:
            lo = mid
            f_lo = f_mid

    return 0.5 * (lo + hi)


def implied_vol(
    market_price: float,
    spot: float,
    strike: float,
    time: float,
    rate: float,
    option_type: str,
    tol: float = 1e-6,
    max_iter: int = 100,
    dividend_yield: float = 0.0,
) -> float:
    """Solve for implied volatility using Newton-Raphson with bisection fallback.

    Newton-Raphson converges fast for well-conditioned problems but can
    diverge for deep ITM/OTM options or low-vega regions. On non-
    convergence (small vega, runaway iterates, or hitting max_iter), this
    falls back to bisection over ``[1e-4, 5.0]`` which always finds the
    root when bracketed.

    Args:
        market_price: Observed market price of the option.
        spot: Current underlying price.
        strike: Option strike price.
        time: Time to expiry in years.
        rate: Risk-free interest rate (annualized).
        option_type: 'call' or 'put'.
        tol: Convergence tolerance.
        max_iter: Maximum Newton-Raphson iterations.
        dividend_yield: Continuous dividend yield (annualized).

    Returns:
        Implied volatility, or float('nan') if both solvers fail.
    """
    if time <= 0.0 or market_price <= 0.0:
        return float("nan")

    option_type = _normalize_option_type(option_type)
    vol = 0.3  # Starting guess: 30% annualized vol

    for _ in range(max_iter):
        price = bs_price(spot, strike, time, rate, vol, option_type, dividend_yield)
        diff = price - market_price

        if abs(diff) < tol:
            return vol

        d1_val = _d1(spot, strike, time, rate, vol, dividend_yield)
        vega_raw = spot * math.exp(-dividend_yield * time) * _norm_pdf(d1_val) * math.sqrt(time)

        if vega_raw < 1e-12:
            break

        vol = vol - diff / vega_raw

        if vol <= 0.0 or vol > _IV_UPPER:
            break  # diverged — let the bisection fallback take over

    return _bisect_iv(market_price, spot, strike, time, rate, option_type, dividend_yield, tol)


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
    # which side profits above/below it. Normalize the input so
    # callers using "CALL" / "Put" / etc. don't silently get the
    # wrong leg's breakeven formula.
    if _normalize_option_type(option_type) == "call":
        return strike + premium
    return strike - premium
