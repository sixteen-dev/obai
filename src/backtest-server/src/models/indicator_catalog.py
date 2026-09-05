"""Typed catalog of every indicator the engine can compute.

One entry per indicator type records the params it accepts with their kinds,
ranges and defaults, the price columns it reads, the columns it emits, how many
bars pass before its first defined value, and a one-line description. The
schema layer (``models/strategy.py``) derives its validation views from this
catalog and the engine (``engine/indicators.py``) derives its dispatch tables
from it, so a new indicator is registered in exactly one place.

Standard library only: both the schema layer and the engine import this module,
and neither may reach the other's dependencies through it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Literal, Mapping

ParamKind = Literal["lookback", "duration", "factor", "source_ref", "iso_date"]
Inputs = Literal["source", "source_volume", "volume", "hl", "hlc", "hlcv", "ohlc", "dual"]

# TA-Lib panics below an indicator's minimum period and above this ceiling. The
# panic reaches the engine as a "Failed to compute" warning that drops the
# indicator and lets the backtest run without it, so both ends are rejected at
# validation instead.
_MAX_LOOKBACK = 100_000

# Widest band multiplier worth accepting: beyond it the band never binds.
_MIN_BAND_FACTOR = 0.1
_MAX_BAND_FACTOR = 10.0

# A trailing percentile compares the current bar against its whole window at
# once, so the window is materialized rather than folded; this caps that block.
_MAX_PERCENTILE_LENGTH = 5_000

# Longest opening interval worth accepting: a full regular 09:30-16:00 session.
_MAX_SESSION_MINUTES = 390

# Largest candle-pattern lookback in TA-Lib (ten-bar body/shadow averages plus a
# four-bar pattern). Candle functions emit 0 rather than null while warming up,
# so this cannot be measured from the output and is an upper bound.
_CANDLE_LOOKBACK = 14


def parse_iso_date(value: Any) -> date | None:
    """Return the calendar date a value spells, or None when it spells none.

    Args:
        value: Supplied param value of unknown type.

    Returns:
        The parsed date for a "YYYY-MM-DD" string, else None. Used both to
        validate a date param and to read it once it is known good.

    """
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _is_real_number(value: Any) -> bool:
    """Return whether a value is a finite int or float, excluding bools.

    Args:
        value: Supplied param value of unknown type.

    Returns:
        True when the value is a finite number the plugin can decode as f64.

    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


@dataclass(frozen=True)
class ParamSpec:
    """One accepted parameter of an indicator.

    Attributes:
        kind: "lookback" for an integer bar count, "duration" for an integer
            count of minutes, "factor" for a real multiplier, "source_ref" for
            the name of another column, "iso_date" for a calendar date.
        default: Value used when the caller omits the param; None marks the
            param as required.
        min: Inclusive lower bound, for numeric kinds.
        max: Inclusive upper bound, for numeric kinds.
        talib_name: Keyword the polars-talib function takes, or None for params
            the engine consumes itself.

    """

    kind: ParamKind
    default: int | float | str | None
    min: int | float | None = None
    max: int | float | None = None
    talib_name: str | None = None

    def errors(self, indicator_id: str, indicator_type: str, name: str, value: Any) -> list[str]:
        """Validate one supplied value against this spec.

        Args:
            indicator_id: Id of the indicator carrying the param.
            indicator_type: Uppercased indicator type, for the message.
            name: Param name as the caller wrote it.
            value: Supplied value.

        Returns:
            A one-element list describing the violation, or an empty list.

        """
        if self._accepts(value):
            return []
        return [self._message(indicator_id, indicator_type, name, value, self._requirement())]

    def _accepts(self, value: Any) -> bool:
        """Return whether a supplied value satisfies this spec's kind and bounds."""
        if self.kind == "source_ref":
            return isinstance(value, str) and bool(value)
        if self.kind == "iso_date":
            return parse_iso_date(value) is not None
        if self.kind == "factor":
            return _is_real_number(value) and self._in_range(value)
        return type(value) is int and self._in_range(value)

    def _requirement(self) -> str:
        """Return the phrase naming what this spec accepts, for the message."""
        if self.kind == "source_ref":
            return "a column name"
        if self.kind == "iso_date":
            return "a calendar date as YYYY-MM-DD"
        if self.kind == "factor":
            return f"a number in [{self.min}, {self.max}]"
        return f"an integer in [{self.min}, {self.max}]"

    def _in_range(self, value: int | float) -> bool:
        """Return whether a numeric value sits inside the inclusive bounds."""
        if self.min is not None and value < self.min:
            return False
        return self.max is None or value <= self.max

    def _message(
        self,
        indicator_id: str,
        indicator_type: str,
        name: str,
        value: Any,
        requirement: str,
    ) -> str:
        """Render the one rejection message this spec produces."""
        return (
            f"Indicator '{indicator_id}' ({indicator_type}) param '{name}' "
            f"must be {requirement}; got {value}"
        )


@dataclass(frozen=True)
class IndicatorSpec:
    """Everything the schema layer and the engine need about one indicator.

    Attributes:
        name: Uppercase indicator type, matching its catalog key.
        inputs: Which price columns the engine feeds the indicator.
        params: Accepted params, keyed by the name the caller writes.
        outputs: Column suffixes for a multi-output indicator, else None.
        output_scale: Units of the emitted value, for the discovery output.
        lookback: First row index that holds a value, given resolved params.
        description: One sentence naming inputs, units and window convention.
        recursive: Whether the value depends on all prior bars, so a truncated
            history shifts it rather than only delaying its first value.
        intraday_only: Whether daily bars make the indicator meaningless.

    """

    name: str
    inputs: Inputs
    params: Mapping[str, ParamSpec]
    outputs: tuple[str, ...] | None
    output_scale: str
    lookback: Callable[[Mapping[str, Any]], int]
    description: str
    recursive: bool = False
    intraday_only: bool = False

    def resolve_params(self, user_params: Mapping[str, Any]) -> dict[str, Any]:
        """Overlay the caller's params on this indicator's defaults.

        Args:
            user_params: Params exactly as the caller supplied them.

        Returns:
            Defaults for every param that has one, overwritten by the supplied
            values for known param names. Unknown names are dropped, and a
            required param the caller omitted stays absent.

        """
        resolved: dict[str, Any] = {
            name: param.default for name, param in self.params.items() if param.default is not None
        }
        resolved.update({k: v for k, v in user_params.items() if k in self.params})
        return resolved

    def source_refs(self, source: str, params: Mapping[str, Any]) -> tuple[str, ...]:
        """Return the frame columns this indicator reads by name.

        Args:
            source: The indicator's ``source`` field.
            params: Resolved params, which carry any second series name.

        Returns:
            Column names that must already exist when the indicator computes.
            Indicators fed fixed price columns reference none.

        """
        if self.inputs in {"source", "source_volume"}:
            return (source,)
        if self.inputs != "dual":
            return ()
        second = params.get("second_source")
        return (source, second) if isinstance(second, str) else (source,)


def _lookback_window(params: Mapping[str, Any]) -> int:
    """First index of a full ``length``-bar window."""
    return int(params["length"]) - 1


def _lookback_period(params: Mapping[str, Any]) -> int:
    """First index of an indicator that also consumes the bar before its window."""
    return int(params["length"])


def _lookback_dema(params: Mapping[str, Any]) -> int:
    """DEMA runs an EMA over an EMA, so it needs two EMA warm-ups."""
    return 2 * (int(params["length"]) - 1)


def _lookback_tema(params: Mapping[str, Any]) -> int:
    """TEMA stacks three EMAs, so it needs three EMA warm-ups."""
    return 3 * (int(params["length"]) - 1)


def _lookback_adx(params: Mapping[str, Any]) -> int:
    """ADX smooths the directional index a second time over ``length`` bars."""
    return 2 * int(params["length"]) - 1


def _lookback_stochrsi(params: Mapping[str, Any]) -> int:
    """RSI warm-up plus the fixed fast %K (5) and %D (3) smoothing."""
    return int(params["length"]) + 6


def _lookback_macd(params: Mapping[str, Any]) -> int:
    """Return the slower EMA's warm-up plus the signal EMA's own."""
    fast, slow = int(params["fast_length"]), int(params["slow_length"])
    return max(fast, slow) - 1 + int(params["signal_length"]) - 1


def _lookback_stoch(params: Mapping[str, Any]) -> int:
    """Raw %K window plus both smoothing windows."""
    return (
        int(params["fastk_period"]) + int(params["slowk_period"]) + int(params["slowd_period"]) - 3
    )


def _lookback_first_bar(_params: Mapping[str, Any]) -> int:
    """Return 0: the indicator is defined from the first bar."""
    return 0


def _lookback_second_bar(_params: Mapping[str, Any]) -> int:
    """Return 1: the first bar that has a previous bar to read."""
    return 1


def _lookback_lag(params: Mapping[str, Any]) -> int:
    """First bar that has a bar ``periods`` back to carry forward."""
    return int(params["periods"])


def _lookback_keltner(params: Mapping[str, Any]) -> int:
    """First bar where both the EMA centre and the ATR width are defined."""
    return max(int(params["length"]) - 1, int(params["atr_length"]))


def _lookback_candle(_params: Mapping[str, Any]) -> int:
    """Upper bound on any TA-Lib candle pattern's warm-up."""
    return _CANDLE_LOOKBACK


def _period(default: int, *, minimum: int = 2, talib_name: str = "timeperiod") -> ParamSpec:
    """Build a lookback param bounded by TA-Lib's accepted period range.

    Args:
        default: Value the plugin's own signature uses, so resolving a param
            the caller omitted computes exactly what it computed before.
        minimum: Smallest period the native function accepts.
        talib_name: Keyword the plugin takes.

    Returns:
        The lookback ParamSpec.

    """
    return ParamSpec(
        kind="lookback",
        default=default,
        min=minimum,
        max=_MAX_LOOKBACK,
        talib_name=talib_name,
    )


def _band_factor(default: float, talib_name: str) -> ParamSpec:
    """Build a band-width multiplier param."""
    return ParamSpec(
        kind="factor",
        default=default,
        min=_MIN_BAND_FACTOR,
        max=_MAX_BAND_FACTOR,
        talib_name=talib_name,
    )


def _rate(default: float, talib_name: str) -> ParamSpec:
    """Build a [0, 1] rate param, such as a parabolic acceleration step."""
    return ParamSpec(kind="factor", default=default, min=0.0, max=1.0, talib_name=talib_name)


CDL_PATTERN_NAMES: tuple[str, ...] = (
    "CDL_2CROWS",
    "CDL_3BLACKCROWS",
    "CDL_3INSIDE",
    "CDL_3LINESTRIKE",
    "CDL_3OUTSIDE",
    "CDL_3STARSINSOUTH",
    "CDL_3WHITESOLDIERS",
    "CDL_ABANDONEDBABY",
    "CDL_ADVANCEBLOCK",
    "CDL_BELTHOLD",
    "CDL_BREAKAWAY",
    "CDL_CLOSINGMARUBOZU",
    "CDL_CONCEALBABYSWALL",
    "CDL_COUNTERATTACK",
    "CDL_DARKCLOUDCOVER",
    "CDL_DOJI",
    "CDL_DOJISTAR",
    "CDL_DRAGONFLYDOJI",
    "CDL_ENGULFING",
    "CDL_EVENINGDOJISTAR",
    "CDL_EVENINGSTAR",
    "CDL_GAPSIDESIDEWHITE",
    "CDL_GRAVESTONEDOJI",
    "CDL_HAMMER",
    "CDL_HANGINGMAN",
    "CDL_HARAMI",
    "CDL_HARAMICROSS",
    "CDL_HIGHWAVE",
    "CDL_HIKKAKE",
    "CDL_HIKKAKEMOD",
    "CDL_HOMINGPIGEON",
    "CDL_IDENTICAL3CROWS",
    "CDL_INNECK",
    "CDL_INVERTEDHAMMER",
    "CDL_KICKING",
    "CDL_KICKINGBYLENGTH",
    "CDL_LADDERBOTTOM",
    "CDL_LONGLEGGEDDOJI",
    "CDL_LONGLINE",
    "CDL_MARUBOZU",
    "CDL_MATCHINGLOW",
    "CDL_MATHOLD",
    "CDL_MORNINGDOJISTAR",
    "CDL_MORNINGSTAR",
    "CDL_ONNECK",
    "CDL_PIERCING",
    "CDL_RICKSHAWMAN",
    "CDL_RISEFALL3METHODS",
    "CDL_SEPARATINGLINES",
    "CDL_SHOOTINGSTAR",
    "CDL_SHORTLINE",
    "CDL_SPINNINGTOP",
    "CDL_STALLEDPATTERN",
    "CDL_STICKSANDWICH",
    "CDL_TAKURI",
    "CDL_TASUKIGAP",
    "CDL_THRUSTING",
    "CDL_TRISTAR",
    "CDL_UNIQUE3RIVER",
    "CDL_UPSIDEGAP2CROWS",
    "CDL_XSIDEGAP3METHODS",
)


_NATIVE_SPECS: tuple[IndicatorSpec, ...] = (
    IndicatorSpec(
        name="SMA",
        inputs="source",
        params={"length": _period(30)},
        outputs=None,
        output_scale="price",
        lookback=_lookback_window,
        description="Simple average of `source` over the last `length` bars, in price units.",
    ),
    IndicatorSpec(
        name="EMA",
        inputs="source",
        params={"length": _period(30)},
        outputs=None,
        output_scale="price",
        lookback=_lookback_window,
        description=(
            "Exponentially weighted average of `source` seeded from its first `length` "
            "bars, in price units."
        ),
        recursive=True,
    ),
    IndicatorSpec(
        name="WMA",
        inputs="source",
        params={"length": _period(30)},
        outputs=None,
        output_scale="price",
        lookback=_lookback_window,
        description=(
            "Linearly weighted average of `source` over the last `length` bars, in price units."
        ),
    ),
    IndicatorSpec(
        name="DEMA",
        inputs="source",
        params={"length": _period(30)},
        outputs=None,
        output_scale="price",
        lookback=_lookback_dema,
        description=(
            "Double exponential average of `source`, in price units; it needs twice an "
            "EMA's history before its first value."
        ),
        recursive=True,
    ),
    IndicatorSpec(
        name="TEMA",
        inputs="source",
        params={"length": _period(30)},
        outputs=None,
        output_scale="price",
        lookback=_lookback_tema,
        description=(
            "Triple exponential average of `source`, in price units; it needs three times "
            "an EMA's history before its first value."
        ),
        recursive=True,
    ),
    IndicatorSpec(
        name="KAMA",
        inputs="source",
        params={"length": _period(30)},
        outputs=None,
        output_scale="price",
        lookback=_lookback_period,
        description=(
            "Kaufman adaptive moving average of `source`: an EMA whose gain rises with "
            "the efficiency ratio over `length` bars; the fast and slow smoothing "
            "constants are fixed by the library."
        ),
        recursive=True,
    ),
    IndicatorSpec(
        name="RSI",
        inputs="source",
        params={"length": _period(14)},
        outputs=None,
        output_scale="0-100",
        lookback=_lookback_period,
        description=(
            "Wilder's relative strength index of `source` over `length` bars, on a 0-100 scale."
        ),
        recursive=True,
    ),
    IndicatorSpec(
        name="MOM",
        inputs="source",
        params={"length": _period(10, minimum=1)},
        outputs=None,
        output_scale="price_delta",
        lookback=_lookback_period,
        description="Change in `source` over `length` bars, in price units.",
    ),
    IndicatorSpec(
        name="ROC",
        inputs="source",
        params={"length": _period(10, minimum=1)},
        outputs=None,
        output_scale="percent",
        lookback=_lookback_period,
        description="Percent change in `source` over `length` bars, where 2.0 means 2%.",
    ),
    IndicatorSpec(
        name="WILLR",
        inputs="hlc",
        params={"length": _period(14)},
        outputs=None,
        output_scale="-100-0",
        lookback=_lookback_window,
        description=(
            "Williams %R: where the close sits in the `length`-bar high/low range, "
            "on a -100 to 0 scale."
        ),
    ),
    IndicatorSpec(
        name="CCI",
        inputs="hlc",
        params={"length": _period(14)},
        outputs=None,
        output_scale="unbounded",
        lookback=_lookback_window,
        description=(
            "Commodity channel index of the high/low/close typical price over `length` "
            "bars, unbounded around zero."
        ),
    ),
    IndicatorSpec(
        name="ATR",
        inputs="hlc",
        params={"length": _period(14, minimum=1)},
        outputs=None,
        output_scale="price",
        lookback=_lookback_period,
        description=(
            "Wilder-smoothed average true range over `length` bars, in price units; "
            "length 1 is the raw true range."
        ),
        recursive=True,
    ),
    IndicatorSpec(
        name="NATR",
        inputs="hlc",
        params={"length": _period(14, minimum=1)},
        outputs=None,
        output_scale="percent",
        lookback=_lookback_period,
        description=(
            "Average true range as a percent of close (100*ATR/close, Wilder smoothing), "
            "where 2.0 means 2%; comparable across differently priced symbols."
        ),
        recursive=True,
    ),
    IndicatorSpec(
        name="ADX",
        inputs="hlc",
        params={"length": _period(14)},
        outputs=None,
        output_scale="0-100",
        lookback=_lookback_adx,
        description=(
            "Wilder's average directional index over `length` bars, on a 0-100 scale; "
            "its second smoothing needs about twice `length` bars."
        ),
        recursive=True,
    ),
    IndicatorSpec(
        name="PLUS_DI",
        inputs="hlc",
        params={"length": _period(14, minimum=1)},
        outputs=None,
        output_scale="0-100",
        lookback=_lookback_period,
        description=(
            "Wilder plus directional indicator over `length` bars, on a 0-100 scale: "
            "smoothed up-move as a percent of smoothed true range; pair with MINUS_DI "
            "for direction and ADX for strength."
        ),
        recursive=True,
    ),
    IndicatorSpec(
        name="MINUS_DI",
        inputs="hlc",
        params={"length": _period(14, minimum=1)},
        outputs=None,
        output_scale="0-100",
        lookback=_lookback_period,
        description=(
            "Wilder minus directional indicator over `length` bars, on a 0-100 scale: "
            "smoothed down-move as a percent of smoothed true range."
        ),
        recursive=True,
    ),
    IndicatorSpec(
        name="MFI",
        inputs="hlcv",
        params={"length": _period(14)},
        outputs=None,
        output_scale="0-100",
        lookback=_lookback_period,
        description=(
            "Money flow index over `length` bars from typical price and volume, on a 0-100 scale."
        ),
    ),
    IndicatorSpec(
        name="OBV",
        inputs="source_volume",
        params={},
        outputs=None,
        output_scale="volume",
        lookback=_lookback_first_bar,
        description=(
            "On-balance volume: running total of volume signed by the `source` change, "
            "in share units."
        ),
    ),
    IndicatorSpec(
        name="SAR",
        inputs="hl",
        params={"acceleration": _rate(0.02, "acceleration"), "maximum": _rate(0.2, "maximum")},
        outputs=None,
        output_scale="price",
        lookback=_lookback_second_bar,
        description=(
            "Parabolic stop-and-reverse level from the high/low series, in price units; "
            "the step grows by `acceleration` up to `maximum`."
        ),
        recursive=True,
    ),
    IndicatorSpec(
        name="MACD",
        inputs="source",
        params={
            "fast_length": _period(12, talib_name="fastperiod"),
            "slow_length": _period(26, talib_name="slowperiod"),
            "signal_length": _period(9, talib_name="signalperiod"),
        },
        outputs=("macd", "signal", "hist"),
        output_scale="price_delta",
        lookback=_lookback_macd,
        description=(
            "Difference between the `fast_length` and `slow_length` EMAs of `source`, its "
            "`signal_length` EMA and their histogram, in price units."
        ),
        recursive=True,
    ),
    IndicatorSpec(
        name="BBANDS",
        inputs="source",
        params={"length": _period(5), "std_dev": _band_factor(2.0, "nbdevup")},
        outputs=("upper", "middle", "lower", "percent_b", "bandwidth"),
        output_scale="price",
        lookback=_lookback_window,
        description=(
            "Bands `std_dev` population deviations above and below a `length`-bar average "
            "of `source`, in price units; percent_b = (source - lower)/(upper - lower) as "
            "a fraction that can leave [0, 1]; bandwidth = (upper - lower)/middle as a "
            "fraction; both undefined when their denominator is zero."
        ),
    ),
    IndicatorSpec(
        name="STOCH",
        inputs="hlc",
        params={
            "fastk_period": _period(5, minimum=1, talib_name="fastk_period"),
            "slowk_period": _period(3, minimum=1, talib_name="slowk_period"),
            "slowd_period": _period(3, minimum=1, talib_name="slowd_period"),
        },
        outputs=("slowk", "slowd"),
        output_scale="0-100",
        lookback=_lookback_stoch,
        description=(
            "Slow stochastic of the close in the `fastk_period` high/low range, smoothed "
            "by `slowk_period` and `slowd_period`, on a 0-100 scale."
        ),
    ),
    IndicatorSpec(
        name="STOCHRSI",
        inputs="source",
        params={"length": _period(14)},
        outputs=("fastk", "fastd"),
        output_scale="0-100",
        lookback=_lookback_stochrsi,
        description=(
            "Stochastic of RSI over `length` bars, on a 0-100 scale; its fast %K and %D "
            "smoothing are fixed at the library's five and three bars."
        ),
        recursive=True,
    ),
    # AROON is bound to `source` rather than the high/low pair: the engine has
    # always passed `source` where TA-Lib expects the high series, taking `low`
    # from the frame, and this catalog records that binding rather than
    # silently moving every AROON value ever computed.
    IndicatorSpec(
        name="AROON",
        inputs="source",
        params={"length": _period(14)},
        outputs=("down", "up"),
        output_scale="0-100",
        lookback=_lookback_period,
        description=(
            "Bars since the `length`-bar extreme of `source` and of the low, on a 0-100 scale."
        ),
    ),
    IndicatorSpec(
        name="LINEARREG",
        inputs="source",
        params={"length": _period(14)},
        outputs=None,
        output_scale="price",
        lookback=_lookback_window,
        description=(
            "Endpoint of a least-squares line through the last `length` bars of `source`, "
            "in price units."
        ),
    ),
    IndicatorSpec(
        name="LINEARREG_SLOPE",
        inputs="source",
        params={"length": _period(14)},
        outputs=None,
        output_scale="unbounded",
        lookback=_lookback_window,
        description=(
            "Slope of a least-squares line through the last `length` bars of `source`, "
            "in price units per bar."
        ),
    ),
    IndicatorSpec(
        name="LINEARREG_ANGLE",
        inputs="source",
        params={"length": _period(14)},
        outputs=None,
        output_scale="unbounded",
        lookback=_lookback_window,
        description=(
            "Angle in degrees of a least-squares line through the last `length` bars of `source`."
        ),
    ),
    IndicatorSpec(
        name="STDDEV",
        inputs="source",
        params={"length": _period(5)},
        outputs=None,
        output_scale="price",
        lookback=_lookback_window,
        description=(
            "Population standard deviation of `source` over the last `length` bars, in "
            "the units of `source`."
        ),
    ),
    IndicatorSpec(
        name="MAX",
        inputs="source",
        params={"length": _period(30)},
        outputs=None,
        output_scale="price",
        lookback=_lookback_window,
        description=(
            "Highest value of `source` over the last `length` bars, the current bar "
            "included; for a channel that excludes the current bar use DONCHIAN."
        ),
    ),
    IndicatorSpec(
        name="MIN",
        inputs="source",
        params={"length": _period(30)},
        outputs=None,
        output_scale="price",
        lookback=_lookback_window,
        description=(
            "Lowest value of `source` over the last `length` bars, the current bar "
            "included; for a channel that excludes the current bar use DONCHIAN."
        ),
    ),
    IndicatorSpec(
        name="BETA",
        inputs="dual",
        params={
            "length": _period(5, minimum=1),
            "second_source": ParamSpec(kind="source_ref", default="high"),
        },
        outputs=None,
        output_scale="unbounded",
        lookback=_lookback_period,
        description=(
            "Slope of `source` regressed on `second_source` over `length` bars, unitless."
        ),
    ),
    IndicatorSpec(
        name="CORREL",
        inputs="dual",
        params={
            "length": _period(30, minimum=1),
            "second_source": ParamSpec(kind="source_ref", default="high"),
        },
        outputs=None,
        output_scale="unbounded",
        lookback=_lookback_window,
        description=(
            "Pearson correlation of `source` and `second_source` over `length` bars, in [-1, 1]."
        ),
    ),
    IndicatorSpec(
        name="VWAP",
        inputs="hlcv",
        params={},
        outputs=None,
        output_scale="price",
        lookback=_lookback_first_bar,
        description=(
            "Volume-weighted average of the high/low/close typical price since the "
            "session open, in price units; intraday timeframes only."
        ),
        intraday_only=True,
    ),
)


# Indicators the engine composes itself in Polars, with no native function
# behind them. Their bindings live in ``engine/indicators._CUSTOM_BUILDERS``.
_COMPOSITE_SPECS: tuple[IndicatorSpec, ...] = (
    IndicatorSpec(
        name="LAG",
        inputs="source",
        params={"periods": ParamSpec(kind="lookback", default=1, min=1, max=_MAX_LOOKBACK)},
        outputs=None,
        output_scale="same_as_source",
        lookback=_lookback_lag,
        description=(
            "`source` as it was `periods` bars earlier (positive offsets only); "
            "undefined for the first `periods` bars."
        ),
    ),
    IndicatorSpec(
        name="RATIO",
        inputs="dual",
        params={"second_source": ParamSpec(kind="source_ref", default=None)},
        outputs=None,
        output_scale="ratio",
        lookback=_lookback_first_bar,
        description=(
            "`source` divided by `second_source`, unitless; undefined when `second_source` is zero."
        ),
    ),
    IndicatorSpec(
        name="DIFF",
        inputs="dual",
        params={"second_source": ParamSpec(kind="source_ref", default=None)},
        outputs=None,
        output_scale="price_delta",
        lookback=_lookback_first_bar,
        description="`source` minus `second_source`, in the units of those two series.",
    ),
    IndicatorSpec(
        name="DONCHIAN",
        inputs="hl",
        params={"length": ParamSpec(kind="lookback", default=20, min=1, max=_MAX_LOOKBACK)},
        outputs=("upper", "middle", "lower"),
        output_scale="price",
        lookback=_lookback_period,
        description=(
            "Channel of the prior `length` bars, excluding the current bar: upper is "
            "the highest high, lower the lowest low, middle their mean, in price "
            "units; a close above upper is a valid breakout."
        ),
    ),
    IndicatorSpec(
        name="ZSCORE",
        inputs="source",
        params={"length": ParamSpec(kind="lookback", default=20, min=2, max=_MAX_LOOKBACK)},
        outputs=None,
        output_scale="unbounded",
        lookback=_lookback_window,
        description=(
            "`source` minus its `length`-bar mean, divided by the population standard "
            "deviation (ddof 0, the convention STDDEV uses) of the same window, in "
            "deviations; undefined when the window is flat."
        ),
    ),
    IndicatorSpec(
        name="RVOL",
        inputs="volume",
        params={"length": ParamSpec(kind="lookback", default=20, min=1, max=_MAX_LOOKBACK)},
        outputs=None,
        output_scale="ratio",
        lookback=_lookback_period,
        description=(
            "Bar volume divided by the mean volume of the preceding `length` bars, the "
            "current bar excluded from that mean; on intraday bars the reference is the "
            "preceding bars, not the same time of day in earlier sessions. Undefined "
            "when the reference mean is zero."
        ),
    ),
    IndicatorSpec(
        name="PERCENTILE_RANK",
        inputs="source",
        params={
            "length": ParamSpec(kind="lookback", default=100, min=1, max=_MAX_PERCENTILE_LENGTH)
        },
        outputs=None,
        output_scale="0-100",
        lookback=_lookback_period,
        description=(
            "Percentile of the current `source` value against the preceding `length` "
            "values, the current bar excluded: 100*(count below + 0.5*count equal)/length; "
            "undefined until `length` prior values are all defined."
        ),
    ),
    IndicatorSpec(
        name="KELTNER",
        inputs="hlc",
        params={
            "length": ParamSpec(kind="lookback", default=20, min=2, max=_MAX_LOOKBACK),
            "atr_length": ParamSpec(kind="lookback", default=10, min=1, max=_MAX_LOOKBACK),
            "multiplier": ParamSpec(
                kind="factor", default=2.0, min=_MIN_BAND_FACTOR, max=_MAX_BAND_FACTOR
            ),
        },
        outputs=("upper", "middle", "lower"),
        output_scale="price",
        lookback=_lookback_keltner,
        description=(
            "Channel around an EMA of close, in price units: middle is the EMA over "
            "`length` (seeded from the mean of the first `length` closes) and upper and "
            "lower sit `multiplier` Wilder average true ranges over `atr_length` above "
            "and below it; `source` is ignored."
        ),
        recursive=True,
    ),
    IndicatorSpec(
        name="AVWAP",
        inputs="hlcv",
        params={"anchor_date": ParamSpec(kind="iso_date", default=None)},
        outputs=None,
        output_scale="price",
        lookback=_lookback_first_bar,
        description=(
            "Volume-weighted average of the high/low/close typical price accumulated "
            "from `anchor_date` onward, in price units; undefined before that date and "
            "while no volume has traded since it. The anchor must fall inside the data "
            "window and is valid on daily and intraday bars."
        ),
    ),
    IndicatorSpec(
        name="OPENING_RANGE",
        inputs="hl",
        params={
            "minutes": ParamSpec(kind="duration", default=None, min=1, max=_MAX_SESSION_MINUTES)
        },
        outputs=("high", "low"),
        output_scale="price",
        lookback=_lookback_first_bar,
        description=(
            "Highest high and lowest low of the first `minutes` of each session, in "
            "price units; both outputs stay undefined until that interval has closed, "
            "and `minutes` must be a whole number of bars. Intraday timeframes only."
        ),
        intraday_only=True,
    ),
)


def _cdl_specs() -> tuple[IndicatorSpec, ...]:
    """Build one spec per candlestick pattern.

    Returns:
        A spec for every name in ``CDL_PATTERN_NAMES``; they share their inputs,
        scale and lookback and take no params.

    """
    return tuple(
        IndicatorSpec(
            name=name,
            inputs="ohlc",
            params={},
            outputs=None,
            output_scale="signal",
            lookback=_lookback_candle,
            description=(
                f"Candlestick pattern {name.removeprefix('CDL_')}: reads open/high/low/close "
                "and emits -100, 0 or 100 on the bar that completes the pattern."
            ),
        )
        for name in CDL_PATTERN_NAMES
    )


INDICATOR_CATALOG: dict[str, IndicatorSpec] = {
    spec.name: spec for spec in (*_NATIVE_SPECS, *_COMPOSITE_SPECS, *_cdl_specs())
}
