"""G21's delta candidates, checked against QuantLib (ADR 0002 §11 G21, §17 items 45 and 46).

The golden ``tests/e2e/scenarios/G21_delta_selection_single_candidate.toml`` claims that at the
2024-03-04 DEC each of its two expiries has exactly one put whose spot delta lies within the
strategy's tolerance of the target, and that the 2024-03-15 candidate's error is the smaller.
This module recomputes every listed put's delta with QuantLib from the golden's own files, never
from the e2e runner or the engine: a pinned quote at its exact mid; a generated quote anywhere
within half a tick of its Black-76 mid at the market's sigma (item 45); the parity forward
anywhere within one tick of the spot (item 45); zero rates, so DF = 1. A strike is in only when
its error stays ``MARGIN`` inside the tolerance at every such corner, and out only when it stays
``MARGIN`` outside (item 46).
"""

import json
import math
import tomllib
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import QuantLib as ql  # type: ignore[import-untyped]

from .conftest import SECONDS_PER_YEAR

E2E_DIR: Final = Path(__file__).resolve().parents[1] / "e2e"
SCENARIO: Final = E2E_DIR / "scenarios" / "G21_delta_selection_single_candidate.toml"
STRATEGY: Final = E2E_DIR / "strategies" / "spxw_put_credit_vertical_delta.json"
DEFAULTS: Final = E2E_DIR / "defaults.toml"
NEW_YORK: Final = ZoneInfo("America/New_York")
SESSION: Final = date(2024, 3, 4)
DECISION: Final = time(15, 45)  # DEC = the 16:00 close - 15 min
EXPIRY_CLOSE: Final = time(16, 0)
MARGIN: Final = 0.001
CANDIDATES: Final = {date(2024, 3, 13): Decimal(4930), date(2024, 3, 15): Decimal(4920)}
"""The one in-tolerance short put per expiry that the golden's derivation names."""


def _toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)


def _market() -> dict[str, Any]:
    """Return the scenario's market: its ``[market]`` keys over ``defaults.toml``'s."""
    return {**_toml(DEFAULTS)["market"], **_toml(SCENARIO)["market"]}


def _delta_rule() -> tuple[float, float, Decimal]:
    """Return the short put's (target delta, tolerance) and the long put's strike offset."""
    short, long = json.loads(STRATEGY.read_text(encoding="utf-8"))["legs"]
    rule = short["strike_selection"]
    assert rule["method"] == "delta", rule
    offset = Decimal(long["strike_selection"]["offset_price_units"])
    return float(rule["target_delta"]), float(rule["tolerance"]), offset


def _pinned_mids(market: dict[str, Any]) -> dict[tuple[date, Decimal], float]:
    """Return the exact mid of every put pinned at the 2024-03-04 DEC."""
    mids: dict[tuple[date, Decimal], float] = {}
    for pin in market["overrides"]:
        if pin["kind"] != "quote_pin" or pin["session"] != SESSION.isoformat():
            continue
        if "DEC" not in pin["slots"]:
            continue
        _, expiry, right, strike = pin["contract"].split(":")
        assert right == "P", pin
        mid = (Decimal(pin["bid"]) + Decimal(pin["ask"])) / 2
        mids[date.fromisoformat(expiry), Decimal(strike)] = float(mid)
    return mids


def _expiries(market: dict[str, Any]) -> tuple[date, ...]:
    first = date.fromisoformat(market["first_session"])
    return tuple(date.fromordinal(first.toordinal() + dte) for dte in market["weekly_dtes"])


def _strikes(market: dict[str, Any]) -> tuple[Decimal, ...]:
    """Return the listed strikes: centre ``step · round_half_up(index_start / step)`` (item 20)."""
    step = Decimal(market["strike_step"])
    centre = step * (Decimal(market["index_start"]) / step).quantize(Decimal(1), ROUND_HALF_UP)
    side = int(market["strikes_each_side"])
    return tuple(centre + k * step for k in range(-side, side + 1))


def _years(expiry: date) -> float:
    """Return ACT/365F years from the DEC instant to the expiry's 16:00 close (DST-exact)."""
    start = datetime.combine(SESSION, DECISION, NEW_YORK).timestamp()
    end = datetime.combine(expiry, EXPIRY_CLOSE, NEW_YORK).timestamp()
    return (end - start) / SECONDS_PER_YEAR


def _put(strike: Decimal) -> Any:
    return ql.PlainVanillaPayoff(ql.Option.Put, float(strike))


def _model_mid(spot: float, strike: Decimal, years: float, sigma: float) -> float:
    return float(ql.BlackCalculator(_put(strike), spot, sigma * math.sqrt(years), 1.0).value())


def _spot_delta(forward: float, spot: float, strike: Decimal, years: float, mid: float) -> float:
    """Return QuantLib's spot delta at the implied volatility of ``mid`` (DF = 1)."""
    guess = 0.2 * math.sqrt(years)
    std_dev = ql.blackFormulaImpliedStdDev(
        ql.Option.Put, float(strike), forward, mid, 1.0, 0.0, guess, 1e-12, 1000
    )
    return float(ql.BlackCalculator(_put(strike), forward, std_dev, 1.0).delta(spot))


def _errors(expiry: date, strike: Decimal) -> list[float]:
    """Return |delta - target| at every corner of the forward and mid uncertainty."""
    market = _market()
    target, _, _ = _delta_rule()
    spot, tick = float(market["index_start"]), float(market["tick"])
    years = _years(expiry)
    pinned = _pinned_mids(market).get((expiry, strike))
    if pinned is None:
        model = _model_mid(spot, strike, years, float(market["sigma"]))
        mids = [model - tick / 2, model + tick / 2]
    else:
        mids = [pinned]
    forwards = (spot - tick, spot, spot + tick)
    return [abs(_spot_delta(f, spot, strike, years, m) - target) for f in forwards for m in mids]


def test_each_expiry_has_exactly_one_put_within_tolerance_by_a_margin() -> None:
    market = _market()
    _, tolerance, _ = _delta_rule()
    for expiry in _expiries(market):
        errors = {strike: _errors(expiry, strike) for strike in _strikes(market)}
        inside = {k for k, e in errors.items() if max(e) <= tolerance - MARGIN}
        outside = {k for k, e in errors.items() if min(e) >= tolerance + MARGIN}
        assert inside == {CANDIDATES[expiry]}, (expiry, sorted(inside))
        assert inside | outside == set(errors), (expiry, sorted(set(errors) - inside - outside))


def test_each_candidate_and_its_long_leg_are_pinned_at_dec() -> None:
    pinned = _pinned_mids(_market())
    _, _, offset = _delta_rule()
    for expiry, strike in CANDIDATES.items():
        assert (expiry, strike) in pinned
        assert (expiry, strike + offset) in pinned


def test_the_expiries_tie_on_dte_and_the_later_one_wins_on_summed_error() -> None:
    rule = json.loads(STRATEGY.read_text(encoding="utf-8"))["legs"][0]["expiry_selection"]
    early, late = sorted(CANDIDATES)
    dte_errors = {abs((expiry - SESSION).days - rule["target_dte"]) for expiry in (early, late)}
    assert dte_errors == {1}
    # The long leg is a strike offset (error 0), so Σerror is the short put's error.
    assert max(_errors(late, CANDIDATES[late])) + MARGIN / 2 < min(
        _errors(early, CANDIDATES[early])
    )


def test_the_golden_enters_the_winning_package() -> None:
    _, _, offset = _delta_rule()
    expiry = max(CANDIDATES)
    short = f"SPXW:{expiry}:P:{CANDIDATES[expiry]}"
    long = f"SPXW:{expiry}:P:{CANDIDATES[expiry] + offset}"
    entry = _toml(SCENARIO)["expected"]["fills"][0]
    assert entry["purpose"] == "entry"
    assert [(leg[0], leg[1]) for leg in entry["legs"]] == [(short, -1), (long, 1)]
