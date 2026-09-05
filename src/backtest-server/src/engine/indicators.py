"""Indicator computation engine using polars-talib (Rust backend)."""

from __future__ import annotations

import importlib.metadata
from datetime import date, datetime, timedelta
from typing import Any, Callable

import numpy as np
import polars as pl
import polars_talib as ta  # type: ignore[import-untyped]
from numpy.lib.stride_tricks import sliding_window_view

from ..logging_config import get_logger
from ..models.indicator_catalog import (
    INDICATOR_CATALOG,
    IndicatorSpec,
    ParamSpec,
    parse_iso_date,
)
from ..models.strategy import BENCHMARK_CLOSE_COLUMN, RAW_PRICE_COLUMNS, IndicatorConfig
from .session import MARKET_OPEN

logger = get_logger(__name__)

# --- Candlestick pattern functions ---
# All cdl* functions from polars-talib take OHLC and return integer signals.
# Their catalog entries are generated from the same names in
# ``models/indicator_catalog.CDL_PATTERN_NAMES``.
_CDL_FUNCTIONS: list[tuple[str, Callable[..., Any]]] = [
    ("CDL_2CROWS", ta.cdl2crows),
    ("CDL_3BLACKCROWS", ta.cdl3blackcrows),
    ("CDL_3INSIDE", ta.cdl3inside),
    ("CDL_3LINESTRIKE", ta.cdl3linestrike),
    ("CDL_3OUTSIDE", ta.cdl3outside),
    ("CDL_3STARSINSOUTH", ta.cdl3starsinsouth),
    ("CDL_3WHITESOLDIERS", ta.cdl3whitesoldiers),
    ("CDL_ABANDONEDBABY", ta.cdlabandonedbaby),
    ("CDL_ADVANCEBLOCK", ta.cdladvanceblock),
    ("CDL_BELTHOLD", ta.cdlbelthold),
    ("CDL_BREAKAWAY", ta.cdlbreakaway),
    ("CDL_CLOSINGMARUBOZU", ta.cdlclosingmarubozu),
    ("CDL_CONCEALBABYSWALL", ta.cdlconcealbabyswall),
    ("CDL_COUNTERATTACK", ta.cdlcounterattack),
    ("CDL_DARKCLOUDCOVER", ta.cdldarkcloudcover),
    ("CDL_DOJI", ta.cdldoji),
    ("CDL_DOJISTAR", ta.cdldojistar),
    ("CDL_DRAGONFLYDOJI", ta.cdldragonflydoji),
    ("CDL_ENGULFING", ta.cdlengulfing),
    ("CDL_EVENINGDOJISTAR", ta.cdleveningdojistar),
    ("CDL_EVENINGSTAR", ta.cdleveningstar),
    ("CDL_GAPSIDESIDEWHITE", ta.cdlgapsidesidewhite),
    ("CDL_GRAVESTONEDOJI", ta.cdlgravestonedoji),
    ("CDL_HAMMER", ta.cdlhammer),
    ("CDL_HANGINGMAN", ta.cdlhangingman),
    ("CDL_HARAMI", ta.cdlharami),
    ("CDL_HARAMICROSS", ta.cdlharamicross),
    ("CDL_HIGHWAVE", ta.cdlhighwave),
    ("CDL_HIKKAKE", ta.cdlhikkake),
    ("CDL_HIKKAKEMOD", ta.cdlhikkakemod),
    ("CDL_HOMINGPIGEON", ta.cdlhomingpigeon),
    ("CDL_IDENTICAL3CROWS", ta.cdlidentical3crows),
    ("CDL_INNECK", ta.cdlinneck),
    ("CDL_INVERTEDHAMMER", ta.cdlinvertedhammer),
    ("CDL_KICKING", ta.cdlkicking),
    ("CDL_KICKINGBYLENGTH", ta.cdlkickingbylength),
    ("CDL_LADDERBOTTOM", ta.cdlladderbottom),
    ("CDL_LONGLEGGEDDOJI", ta.cdllongleggeddoji),
    ("CDL_LONGLINE", ta.cdllongline),
    ("CDL_MARUBOZU", ta.cdlmarubozu),
    ("CDL_MATCHINGLOW", ta.cdlmatchinglow),
    ("CDL_MATHOLD", ta.cdlmathold),
    ("CDL_MORNINGDOJISTAR", ta.cdlmorningdojistar),
    ("CDL_MORNINGSTAR", ta.cdlmorningstar),
    ("CDL_ONNECK", ta.cdlonneck),
    ("CDL_PIERCING", ta.cdlpiercing),
    ("CDL_RICKSHAWMAN", ta.cdlrickshawman),
    ("CDL_RISEFALL3METHODS", ta.cdlrisefall3methods),
    ("CDL_SEPARATINGLINES", ta.cdlseparatinglines),
    ("CDL_SHOOTINGSTAR", ta.cdlshootingstar),
    ("CDL_SHORTLINE", ta.cdlshortline),
    ("CDL_SPINNINGTOP", ta.cdlspinningtop),
    ("CDL_STALLEDPATTERN", ta.cdlstalledpattern),
    ("CDL_STICKSANDWICH", ta.cdlsticksandwich),
    ("CDL_TAKURI", ta.cdltakuri),
    ("CDL_TASUKIGAP", ta.cdltasukigap),
    ("CDL_THRUSTING", ta.cdlthrusting),
    ("CDL_TRISTAR", ta.cdltristar),
    ("CDL_UNIQUE3RIVER", ta.cdlunique3river),
    ("CDL_UPSIDEGAP2CROWS", ta.cdlupsidegap2crows),
    ("CDL_XSIDEGAP3METHODS", ta.cdlxsidegap3methods),
]

# The native function behind every catalogued indicator that TA-Lib provides.
# `tests/test_indicators.py::TestCatalogEngineParity` pins this against the
# catalog: an entry with no binding validates but cannot compute.
TALIB_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "SMA": ta.sma,
    "EMA": ta.ema,
    "WMA": ta.wma,
    "DEMA": ta.dema,
    "TEMA": ta.tema,
    "KAMA": ta.kama,
    "RSI": ta.rsi,
    "MOM": ta.mom,
    "ROC": ta.roc,
    "WILLR": ta.willr,
    "CCI": ta.cci,
    "ATR": ta.atr,
    "NATR": ta.natr,
    "ADX": ta.adx,
    "PLUS_DI": ta.plus_di,
    "MINUS_DI": ta.minus_di,
    "MFI": ta.mfi,
    "OBV": ta.obv,
    "SAR": ta.sar,
    "MACD": ta.macd,
    "BBANDS": ta.bbands,
    "STOCH": ta.stoch,
    "STOCHRSI": ta.stochrsi,
    "AROON": ta.aroon,
    "LINEARREG": ta.linearreg,
    "LINEARREG_SLOPE": ta.linearreg_slope,
    "LINEARREG_ANGLE": ta.linearreg_angle,
    "STDDEV": ta.stddev,
    "MAX": ta.max,
    "MIN": ta.min,
    "BETA": ta.beta,
    "CORREL": ta.correl,
    **dict(_CDL_FUNCTIONS),
}

# Which price columns each input class feeds the native function, in order.
_INPUT_COLUMNS: dict[str, tuple[str, ...]] = {
    "ohlc": ("open", "high", "low", "close"),
    "hlc": ("high", "low", "close"),
    "hlcv": ("high", "low", "close", "volume"),
    "hl": ("high", "low"),
    "volume": ("volume",),
}

# Views the discovery output and the expression builder read, derived so that
# an indicator declares its inputs once, in the catalog.
HLC_INDICATORS: set[str] = {
    name for name, spec in INDICATOR_CATALOG.items() if spec.inputs in {"hl", "hlc", "hlcv"}
}
VOLUME_INDICATORS: set[str] = {
    name
    for name, spec in INDICATOR_CATALOG.items()
    if spec.inputs in {"source_volume", "volume", "hlcv"}
}
OHLC_INDICATORS: set[str] = {
    name for name, spec in INDICATOR_CATALOG.items() if spec.inputs == "ohlc"
}
DUAL_INPUT_INDICATORS: set[str] = {
    name for name, spec in INDICATOR_CATALOG.items() if spec.inputs == "dual"
}


def compute_indicators(
    df: pl.DataFrame,
    indicators: list[IndicatorConfig],
    timeframe: str = "daily",
) -> tuple[pl.DataFrame, list[str]]:
    """Compute all indicators and add as columns to the DataFrame.

    Args:
        df: OHLCV DataFrame with columns: date, open, high, low, close, volume.
        indicators: List of indicator configurations to compute.
        timeframe: Data timeframe (e.g. "daily", "5min"). Used for VWAP guard.

    Returns:
        Tuple of (DataFrame with indicator columns added, list of warnings).

    Raises:
        ValueError: If the frame's timestamps are not strictly increasing, or
            if VWAP is requested with daily timeframe.

    """
    _require_strictly_increasing_dates(df)
    warnings: list[str] = []
    result_df = df.clone()

    for config in indicators:
        indicator_type = config.type.upper()
        spec = INDICATOR_CATALOG.get(indicator_type)
        if spec is None:
            warnings.append(f"Skipping unsupported indicator: {config.type}")
            continue

        # Timeframe guard for intraday-only indicators
        if spec.intraday_only and timeframe == "daily":
            msg = (
                f"{indicator_type} requires intraday data. "
                "Use timeframe '5min', '15min', or '1hour'."
            )
            raise ValueError(msg)

        try:
            result_df = _apply_indicator(result_df, config, spec, warnings)
        except Exception as exc:
            warnings.append(f"Failed to compute {config.id}: {exc}")
            logger.warning("indicator_failed", indicator=config.id, error=str(exc))

    return _mark_warmup_bars_undefined(df, result_df), warnings


# Ordering is only meaningful once a frame holds more than one bar.
_MIN_ORDERED_BARS = 2


def _require_strictly_increasing_dates(df: pl.DataFrame) -> None:
    """Reject an OHLCV frame whose bars are out of order or repeated.

    Every rolling window, crossover shift and session boundary downstream reads
    bar order as time order. An unsorted or duplicated frame does not fail
    there — it produces plausible numbers from a corrupted history — so the one
    entry point every production frame passes through rejects it up front.

    Empty frames and frames without a date column pass: callers construct those
    to exercise indicator math alone.

    Args:
        df: OHLCV frame about to be enriched.

    Raises:
        ValueError: If the date column is unsorted or holds duplicates.

    """
    if "date" not in df.columns or df.height < _MIN_ORDERED_BARS:
        return
    dates = df["date"]
    if dates.is_sorted() and dates.n_unique() == dates.len():
        return
    msg = "OHLCV frame must have strictly increasing timestamps (sorted, no duplicates)"
    raise ValueError(msg)


# Scratch column carrying the benchmark bar's own date through the as-of join,
# so a carried (stale) value can be counted before it is dropped.
_BENCHMARK_DATE_COLUMN = "_benchmark_date"


def attach_benchmark_close(
    df: pl.DataFrame,
    benchmark_df: pl.DataFrame,
    symbol: str,
) -> tuple[pl.DataFrame, list[str]]:
    """Attach the benchmark's close to a symbol frame, aligned as of each bar.

    The benchmark keeps its own calendar, so its close is matched backward: bar
    t carries the last benchmark close dated at or before t and never a later
    one. A symbol bar earlier than the benchmark's first bar has no value at
    all rather than the earliest one, which would be a future observation.

    Every symbol row survives the join: dropping the bars the benchmark is
    missing would silently shorten the equity curve.

    Args:
        df: Symbol OHLCV frame, sorted by date.
        benchmark_df: Benchmark frame on the same timeframe, sorted by date.
        symbol: Symbol the frame belongs to, named in the warning.

    Returns:
        Tuple of (frame with the benchmark close column added, warnings).

    Raises:
        ValueError: If either frame lacks a date or close column, or the
            benchmark frame is empty.

    """
    _require_benchmark_inputs(df, benchmark_df, symbol)
    bench = benchmark_df.select(
        pl.col("date"),
        pl.col("close").alias(BENCHMARK_CLOSE_COLUMN),
        pl.col("date").alias(_BENCHMARK_DATE_COLUMN),
    )
    joined = df.join_asof(bench, on="date", strategy="backward")
    carried = joined.filter(
        pl.col(_BENCHMARK_DATE_COLUMN).is_not_null()
        & (pl.col(_BENCHMARK_DATE_COLUMN) != pl.col("date"))
    ).height
    undefined = joined[BENCHMARK_CLOSE_COLUMN].null_count()
    attached = joined.drop(_BENCHMARK_DATE_COLUMN)
    if not carried and not undefined:
        return attached, []
    return attached, [
        f"{BENCHMARK_CLOSE_COLUMN} for {symbol}: {carried} bar(s) carried from an "
        f"earlier benchmark date, {undefined} bar(s) undefined before the first "
        "benchmark bar"
    ]


def _require_benchmark_inputs(
    df: pl.DataFrame,
    benchmark_df: pl.DataFrame,
    symbol: str,
) -> None:
    """Reject frames the as-of join cannot align.

    Args:
        df: Symbol OHLCV frame.
        benchmark_df: Benchmark frame.
        symbol: Symbol the frame belongs to, named in the message.

    Raises:
        ValueError: If a frame is missing the join columns, or the benchmark
            frame holds no bars to align against.

    """
    required = {"date", "close"}
    missing = [
        label
        for label, frame in (("symbol", df), ("benchmark", benchmark_df))
        if not required <= set(frame.columns)
    ]
    if missing:
        msg = (
            f"{BENCHMARK_CLOSE_COLUMN} for {symbol}: the "
            f"{' and '.join(missing)} frame needs date and close columns"
        )
        raise ValueError(msg)
    if benchmark_df.is_empty():
        msg = f"{BENCHMARK_CLOSE_COLUMN} for {symbol}: the benchmark frame has no bars"
        raise ValueError(msg)


def indicator_stack_versions() -> dict[str, str]:
    """Report the versions of the stack that computes indicator values.

    An upgrade to the wrapper or the native library can move a value under an
    unchanged strategy, so results record what produced them and the cache key
    includes it. ``polars_talib`` exposes no ``__version__``, so the wrapper
    version comes from installed distribution metadata.

    Returns:
        Mapping of "polars", "polars_talib" and "talib" to version strings.

    """
    return {
        "polars": pl.__version__,
        "polars_talib": importlib.metadata.version("polars-talib"),
        "talib": str(ta.__talib_version__),
    }


def _mark_warmup_bars_undefined(source: pl.DataFrame, computed: pl.DataFrame) -> pl.DataFrame:
    """Represent an indicator's warm-up bars as null rather than NaN.

    TA-Lib emits NaN for the bars before an indicator has enough history, and
    in Polars a NaN compares True against a threshold: ``adx > 25`` is True on
    every one of ADX(14)'s 27 warm-up bars, so a strategy enters on an
    indicator that has no value yet. Null propagates through the comparison,
    so an undefined indicator cannot satisfy a rule.

    Only the columns this pass added are touched, so NaN arriving in the
    source OHLCV keeps whatever meaning the caller gave it.

    Args:
        source: The frame as it was before indicator columns were added.
        computed: The frame returned by the indicator pass.

    Returns:
        ``computed`` with NaN replaced by null in the float columns it added.

    """
    added = [col for col in computed.columns if col not in source.columns]
    floats = [col for col in added if computed.schema[col].is_float()]
    if not floats:
        return computed
    return computed.with_columns([pl.col(col).fill_nan(None) for col in floats])


def get_supported_indicators() -> dict[str, Any]:
    """Return the catalog of supported indicators with parameter info.

    Returns:
        Dict with 'indicators' (one entry per catalogued type) and
        'raw_columns' (always-valid operands).

    """
    indicators = {name: _describe_indicator(spec) for name, spec in INDICATOR_CATALOG.items()}
    return {
        "indicators": indicators,
        "raw_columns": sorted(RAW_PRICE_COLUMNS),
        "raw_columns_note": (
            "These OHLCV columns are always available as operand references "
            'in conditions (e.g., {"indicator": "close"} to compare price '
            "against a computed indicator like VWAP)."
        ),
        "benchmark_column": BENCHMARK_CLOSE_COLUMN,
        "benchmark_column_note": (
            "When `universe.benchmark` is set, this reserved column holds the "
            "benchmark's close aligned as of each bar. It can be an indicator's "
            "`source` or `second_source`, or a rule operand, so a measure of the "
            "benchmark can be compared against the same measure of the symbol."
        ),
        "source_note": (
            "An indicator's `source` accepts a raw column or the `id` of any "
            "indicator declared before it, so indicators can be built on one "
            "another. Indicators are computed in list order in a single pass: "
            "a source naming an indicator declared later, or a name that is "
            "neither, is rejected at validation."
        ),
    }


def _describe_indicator(spec: IndicatorSpec) -> dict[str, Any]:
    """Render one catalog entry as discovery output.

    Args:
        spec: Catalog entry to describe.

    Returns:
        The metadata an agent needs to declare the indicator: accepted params
        with their ranges, what it emits, which columns it needs, how many bars
        it warms up for at its defaults, and what it measures.

    """
    return {
        "params": list(spec.params),
        "param_specs": {name: _describe_param(param) for name, param in spec.params.items()},
        "output_scale": spec.output_scale,
        "multi_output": spec.outputs is not None,
        "output_columns": list(spec.outputs) if spec.outputs else None,
        "needs_hlc": spec.name in HLC_INDICATORS,
        "needs_volume": spec.name in VOLUME_INDICATORS,
        "needs_ohlc": spec.name in OHLC_INDICATORS,
        "dual_input": spec.name in DUAL_INPUT_INDICATORS,
        "intraday_only": spec.intraday_only,
        "lookback_bars_at_defaults": spec.lookback(spec.resolve_params({})),
        "recursive": spec.recursive,
        "description": spec.description,
    }


def _describe_param(param: ParamSpec) -> dict[str, Any]:
    """Render one accepted param as discovery output."""
    return {
        "kind": param.kind,
        "default": param.default,
        "min": param.min,
        "max": param.max,
        "required": param.default is None,
    }


def _apply_indicator(
    df: pl.DataFrame,
    config: IndicatorConfig,
    spec: IndicatorSpec,
    warnings: list[str],
) -> pl.DataFrame:
    """Apply a single indicator to the DataFrame.

    Args:
        df: Input DataFrame.
        config: Indicator configuration.
        spec: Catalog entry for the indicator's type.
        warnings: Warning list to append to.

    Returns:
        DataFrame with indicator column(s) added.

    """
    resolved = spec.resolve_params(config.params)
    builder = _CUSTOM_BUILDERS.get(spec.name)
    if builder is not None:
        return builder(df, config, resolved)
    return _apply_talib_indicator(df, config, spec, resolved, warnings)


def _apply_talib_indicator(
    df: pl.DataFrame,
    config: IndicatorConfig,
    spec: IndicatorSpec,
    resolved: dict[str, Any],
    warnings: list[str],
) -> pl.DataFrame:
    """Apply a polars-talib indicator to the DataFrame.

    Args:
        df: Input DataFrame.
        config: Indicator configuration.
        spec: Catalog entry for the indicator's type.
        resolved: Params with the catalog's defaults filled in.
        warnings: Warning list to append to.

    Returns:
        DataFrame with indicator column(s) added.

    """
    lookback = spec.lookback(resolved)
    if lookback and len(df) <= lookback:
        warnings.append(
            f"Insufficient data for {config.id}: need {lookback + 1} rows, have {len(df)}"
        )

    expr = _build_indicator_expr(spec, config, resolved, _build_talib_kwargs(spec, resolved))

    if spec.outputs is None:
        return df.with_columns(expr.alias(config.id))

    computed = _apply_multi_output(df, config.id, expr, spec.outputs)
    if spec.name != "BBANDS":
        return computed
    return _add_bbands_derived(computed, config)


def _build_talib_kwargs(spec: IndicatorSpec, resolved: dict[str, Any]) -> dict[str, Any]:
    """Translate resolved params into the keywords the native function takes.

    Most params map one to one. Bollinger Bands is special: the caller gives
    one `std_dev`, which must reach both the upper and lower band parameters so
    the bands stay symmetric.

    Args:
        spec: Catalog entry for the indicator's type.
        resolved: Params with the catalog's defaults filled in.

    Returns:
        Keyword arguments for the bound polars-talib function.

    """
    talib_kwargs: dict[str, Any] = {}
    for name, param in spec.params.items():
        if param.talib_name is None or name not in resolved:
            continue
        talib_kwargs[param.talib_name] = resolved[name]
    if spec.name == "BBANDS" and "std_dev" in resolved:
        talib_kwargs["nbdevdn"] = resolved["std_dev"]
    return talib_kwargs


def _build_indicator_expr(
    spec: IndicatorSpec,
    config: IndicatorConfig,
    resolved: dict[str, Any],
    talib_kwargs: dict[str, Any],
) -> pl.Expr:
    """Build the Polars expression for a talib indicator call.

    Args:
        spec: Catalog entry, whose `inputs` decides which columns are passed.
        config: Indicator configuration, which carries `source`.
        resolved: Params with the catalog's defaults filled in.
        talib_kwargs: Mapped talib keyword arguments.

    Returns:
        Polars expression for the indicator computation.

    """
    fn = TALIB_FUNCTIONS[spec.name]
    fixed = _INPUT_COLUMNS.get(spec.inputs)
    if fixed is not None:
        return fn(*(pl.col(name) for name in fixed), **talib_kwargs)  # type: ignore[no-any-return]

    if spec.inputs == "dual":
        # The second series is a named column: the catalog's default carries
        # each dual-input indicator's natural counterpart, and comparing a
        # column with itself would produce a tautology (beta=1, correl=1).
        return fn(  # type: ignore[no-any-return]
            pl.col(config.source),
            pl.col(resolved["second_source"]),
            **talib_kwargs,
        )

    if spec.inputs == "source_volume":
        return fn(pl.col(config.source), pl.col("volume"), **talib_kwargs)  # type: ignore[no-any-return]

    return fn(pl.col(config.source), **talib_kwargs)  # type: ignore[no-any-return]


def _apply_multi_output(
    df: pl.DataFrame,
    indicator_id: str,
    expr: pl.Expr,
    outputs: tuple[str, ...],
) -> pl.DataFrame:
    """Apply a multi-output indicator expression and name the columns.

    Args:
        df: Input DataFrame.
        indicator_id: Base indicator id for column naming.
        expr: The multi-output Polars expression.
        outputs: Output suffix names, in the order the plugin emits them.

    Returns:
        DataFrame with named output columns added.

    """
    result = df.select(expr)
    # polars-talib returns struct column for multi-output indicators
    if result.width == 1 and len(outputs) > 1:
        result = result.unnest(result.columns[0])
    for idx, suffix in enumerate(outputs):
        col_name = f"{indicator_id}_{suffix}"
        if idx < result.width:
            df = df.with_columns(result.get_column(result.columns[idx]).alias(col_name))
    return df


def _add_bbands_derived(df: pl.DataFrame, config: IndicatorConfig) -> pl.DataFrame:
    """Add the two band-derived outputs to a frame that already holds the bands.

    Both read the bands the native call just produced rather than recomputing
    them, so one BBANDS declaration answers "where in the band is price" and
    "how wide is the band" at the parameters the caller chose. A collapsed band
    or a zero centre line leaves its own output undefined: an infinite %B
    compares True against every threshold and would fire the rule it feeds.

    Args:
        df: Frame carrying the indicator's upper, middle and lower columns.
        config: Indicator configuration, which names the source and the outputs.

    Returns:
        DataFrame with the percent_b and bandwidth columns added.

    """
    upper, middle = pl.col(f"{config.id}_upper"), pl.col(f"{config.id}_middle")
    lower = pl.col(f"{config.id}_lower")
    width = upper - lower
    source = pl.col(config.source).cast(pl.Float64)
    return df.with_columns(
        pl.when(width != 0)
        .then((source - lower) / width)
        .otherwise(None)
        .alias(f"{config.id}_percent_b"),
        pl.when(middle != 0).then(width / middle).otherwise(None).alias(f"{config.id}_bandwidth"),
    )


def _build_vwap(
    df: pl.DataFrame,
    config: IndicatorConfig,
    _resolved: dict[str, Any],
) -> pl.DataFrame:
    """Compute session-resetting VWAP.

    VWAP = cumsum(typical_price * volume) / cumsum(volume), resetting at each
    new trading session (date boundary).

    Args:
        df: OHLCV DataFrame with a datetime 'date' column.
        config: Indicator configuration, which names the output column.
        _resolved: Resolved params; VWAP takes none.

    Returns:
        DataFrame with the VWAP column added.

    """
    typical_price = (pl.col("high") + pl.col("low") + pl.col("close")) / 3.0
    tp_volume = typical_price * pl.col("volume")

    # Group by date portion of datetime for session reset
    session_date = pl.col("date").cast(pl.Date)

    return df.with_columns(
        (
            tp_volume.cum_sum().over(session_date) / pl.col("volume").cum_sum().over(session_date)
        ).alias(config.id)
    )


def _build_avwap(
    df: pl.DataFrame,
    config: IndicatorConfig,
    resolved: dict[str, Any],
) -> pl.DataFrame:
    """Accumulate a volume-weighted average price from a fixed anchor date.

    Where session VWAP restarts every day, this one restarts once, on a date
    the caller fixed in advance — the only kind of anchor a bar can be scored
    against causally. Bars before the anchor contribute nothing and hold no
    value, and neither does a bar the anchor has not yet been followed by any
    traded volume, which would otherwise divide by zero.

    Args:
        df: OHLCV frame with a date column (daily or intraday).
        config: Indicator configuration, which names the output column.
        resolved: Resolved params, carrying ``anchor_date``.

    Returns:
        DataFrame with the anchored VWAP column added.

    Raises:
        ValueError: If the anchor is not an ISO date. Validation rejects that
            first; a caller reaching the engine directly gets a named error.

    """
    anchor = parse_iso_date(resolved.get("anchor_date"))
    if anchor is None:
        msg = f"AVWAP requires anchor_date as YYYY-MM-DD; got {resolved.get('anchor_date')}"
        raise ValueError(msg)
    anchored = pl.col("date").cast(pl.Date) >= anchor
    typical_price = (pl.col("high") + pl.col("low") + pl.col("close")) / 3.0
    traded = pl.when(anchored).then(typical_price * pl.col("volume")).otherwise(0.0).cum_sum()
    volume = pl.when(anchored).then(pl.col("volume")).otherwise(0).cum_sum()
    return df.with_columns(
        pl.when(anchored & (volume > 0)).then(traded / volume).otherwise(None).alias(config.id)
    )


def _build_opening_range(
    df: pl.DataFrame,
    config: IndicatorConfig,
    resolved: dict[str, Any],
) -> pl.DataFrame:
    """Publish each session's opening high and low once the interval has closed.

    The window is a clock interval measured from the session open, not a bar
    count, so a missing bar narrows the sample the levels are taken from but
    never moves the window. Both levels stay undefined on every bar that starts
    inside the interval: the last of those bars only knows the range including
    itself, and a close-confirmed breakout could not fire there anyway.

    ``source`` is ignored: the range reads the high and low columns.

    Args:
        df: Intraday OHLCV frame whose date column holds bar-start timestamps.
        config: Indicator configuration, which names the output columns.
        resolved: Resolved params, carrying ``minutes``.

    Returns:
        DataFrame with the range high and low columns added.

    """
    minutes = int(resolved["minutes"])
    opened = datetime.combine(date(2000, 1, 1), MARKET_OPEN)
    range_end = (opened + timedelta(minutes=minutes)).time()
    session = pl.col("date").cast(pl.Date)
    bar_time = pl.col("date").dt.time()
    inside = (bar_time >= MARKET_OPEN) & (bar_time < range_end)
    published = bar_time >= range_end
    highest = pl.when(inside).then(pl.col("high")).otherwise(None).max().over(session)
    lowest = pl.when(inside).then(pl.col("low")).otherwise(None).min().over(session)
    return df.with_columns(
        pl.when(published).then(highest).otherwise(None).alias(f"{config.id}_high"),
        pl.when(published).then(lowest).otherwise(None).alias(f"{config.id}_low"),
    )


def _build_lag(
    df: pl.DataFrame,
    config: IndicatorConfig,
    resolved: dict[str, Any],
) -> pl.DataFrame:
    """Carry the value a column held ``periods`` bars ago onto the current bar.

    The shift is backward only, so bar t reads bar t-periods and the first
    ``periods`` bars are undefined. The column keeps the source's dtype: an
    integer candle signal stays an integer, with nulls for the warm-up.

    Args:
        df: Frame holding the source column.
        config: Indicator configuration, which names the source and the output.
        resolved: Resolved params, carrying ``periods``.

    Returns:
        DataFrame with the lagged column added.

    """
    periods = int(resolved["periods"])
    return df.with_columns(pl.col(config.source).shift(periods).alias(config.id))


def _second_source_expr(config: IndicatorConfig, resolved: dict[str, Any]) -> pl.Expr:
    """Read the second series a two-input composite combines with its source.

    Args:
        config: Indicator configuration, which names the type for the message.
        resolved: Resolved params, which must carry ``second_source``.

    Returns:
        Expression reading that column as a float.

    Raises:
        ValueError: If no column name was supplied. Validation rejects that
            first; a caller reaching the engine directly gets a named error
            instead of a missing-key failure.

    """
    name = resolved.get("second_source")
    if not isinstance(name, str) or not name:
        msg = f"{config.type.upper()} requires a 'second_source' column name"
        raise ValueError(msg)
    return pl.col(name).cast(pl.Float64)


def _build_ratio(
    df: pl.DataFrame,
    config: IndicatorConfig,
    resolved: dict[str, Any],
) -> pl.DataFrame:
    """Divide the source series by a second named series, bar by bar.

    A zero divisor yields null rather than an infinity: an infinite ratio
    compares True against every threshold and would fire the rule it feeds.

    Args:
        df: Frame holding both columns.
        config: Indicator configuration, which names the source and the output.
        resolved: Resolved params, carrying ``second_source``.

    Returns:
        DataFrame with the ratio column added.

    """
    numerator = pl.col(config.source).cast(pl.Float64)
    denominator = _second_source_expr(config, resolved)
    return df.with_columns(
        pl.when(denominator != 0).then(numerator / denominator).otherwise(None).alias(config.id)
    )


def _build_diff(
    df: pl.DataFrame,
    config: IndicatorConfig,
    resolved: dict[str, Any],
) -> pl.DataFrame:
    """Subtract a second named series from the source series, bar by bar.

    Args:
        df: Frame holding both columns.
        config: Indicator configuration, which names the source and the output.
        resolved: Resolved params, carrying ``second_source``.

    Returns:
        DataFrame with the difference column added.

    """
    left = pl.col(config.source).cast(pl.Float64)
    return df.with_columns((left - _second_source_expr(config, resolved)).alias(config.id))


def _build_donchian(
    df: pl.DataFrame,
    config: IndicatorConfig,
    resolved: dict[str, Any],
) -> pl.DataFrame:
    """Compute the price channel of the bars before the current one.

    The window is shifted one bar back, so the channel a rule compares the
    close against never contains that close's own bar: an inclusive channel
    puts the current high in the upper band, which no close can exceed. A bar
    whose window is short or holds a missing high or low stays undefined.

    ``source`` is ignored: the channel reads the high and low columns.

    Args:
        df: OHLCV frame.
        config: Indicator configuration, which names the output columns.
        resolved: Resolved params, carrying ``length``.

    Returns:
        DataFrame with the upper, middle and lower channel columns added.

    """
    length = int(resolved["length"])
    upper = pl.col("high").cast(pl.Float64).shift(1).rolling_max(length)
    lower = pl.col("low").cast(pl.Float64).shift(1).rolling_min(length)
    return df.with_columns(
        upper.alias(f"{config.id}_upper"),
        ((upper + lower) / 2.0).alias(f"{config.id}_middle"),
        lower.alias(f"{config.id}_lower"),
    )


def _build_zscore(
    df: pl.DataFrame,
    config: IndicatorConfig,
    resolved: dict[str, Any],
) -> pl.DataFrame:
    """Standardize a series against its own trailing window.

    The divisor is the population deviation (ddof 0), which is the convention
    TA-Lib's STDDEV uses; Polars defaults to the sample one, and mixing the two
    moves every threshold a strategy sets. A flat window has no dispersion to
    divide by, so the bar is undefined rather than infinite.

    Args:
        df: Frame holding the source column.
        config: Indicator configuration, which names the source and the output.
        resolved: Resolved params, carrying ``length``.

    Returns:
        DataFrame with the z-score column added.

    """
    length = int(resolved["length"])
    values = pl.col(config.source).cast(pl.Float64)
    deviation = values.rolling_std(length, ddof=0)
    displacement = values - values.rolling_mean(length)
    return df.with_columns(
        pl.when(deviation > 0).then(displacement / deviation).otherwise(None).alias(config.id)
    )


def _build_rvol(
    df: pl.DataFrame,
    config: IndicatorConfig,
    resolved: dict[str, Any],
) -> pl.DataFrame:
    """Compare this bar's volume against the volume of the bars before it.

    The reference window is shifted one bar back, so the bar being judged is
    never part of its own reference: an inclusive mean pulls itself toward the
    spike it is supposed to measure. A dead reference window divides by zero,
    so the bar is undefined rather than infinite.

    ``source`` is ignored: the ratio reads the volume column.

    Args:
        df: OHLCV frame.
        config: Indicator configuration, which names the output column.
        resolved: Resolved params, carrying ``length``.

    Returns:
        DataFrame with the relative-volume column added.

    """
    length = int(resolved["length"])
    volume = pl.col("volume").cast(pl.Float64)
    reference = volume.shift(1).rolling_mean(length)
    return df.with_columns(
        pl.when(reference > 0).then(volume / reference).otherwise(None).alias(config.id)
    )


def _build_percentile_rank(
    df: pl.DataFrame,
    config: IndicatorConfig,
    resolved: dict[str, Any],
) -> pl.DataFrame:
    """Rank a series against its own trailing window, on a 0-100 scale.

    Args:
        df: Frame holding the source column.
        config: Indicator configuration, which names the source and the output.
        resolved: Resolved params, carrying ``length``.

    Returns:
        DataFrame with the percentile column added.

    """
    length = int(resolved["length"])
    values = df[config.source].cast(pl.Float64).fill_null(float("nan")).to_numpy()
    ranks = _trailing_percentiles(values, length)
    return df.with_columns(pl.Series(config.id, ranks).fill_nan(None))


# Rows compared per pass. Each pass holds one bool block of this many rows by
# ``length`` values, so the widest accepted window stays inside ~10 MB.
_PERCENTILE_CHUNK_ROWS = 2048


def _trailing_percentiles(
    values: np.ndarray[Any, np.dtype[np.float64]],
    length: int,
) -> np.ndarray[Any, np.dtype[np.float64]]:
    """Rank every value against the ``length`` values before it.

    The comparison is vectorized over a strided view of the series, which is
    why it is written in numpy: Polars has no rolling rank, and ``rolling_map``
    runs a Python callable per bar. The view is walked in fixed-size chunks so
    memory stays bounded no matter how long the history is.

    Args:
        values: The series, with missing bars carried as NaN.
        length: Window size, the number of prior values each bar is ranked in.

    Returns:
        An array of the same length holding percentiles in [0, 100], NaN for
        every bar without a complete window behind it.

    """
    ranks = np.full(len(values), np.nan)
    if len(values) <= length:
        return ranks
    windows = sliding_window_view(values[:-1], length)
    currents = values[length:]
    for start in range(0, len(currents), _PERCENTILE_CHUNK_ROWS):
        stop = min(start + _PERCENTILE_CHUNK_ROWS, len(currents))
        ranks[length + start : length + stop] = _percentile_chunk(
            windows[start:stop], currents[start:stop]
        )
    return ranks


def _percentile_chunk(
    windows: np.ndarray[Any, np.dtype[np.float64]],
    currents: np.ndarray[Any, np.dtype[np.float64]],
) -> np.ndarray[Any, np.dtype[np.float64]]:
    """Rank one block of bars against their windows.

    Ties count a half each, so a value equal to its whole window ranks at the
    middle rather than at either extreme. A window with one missing value has
    no defined rank at all: filling it would rank against a shorter history and
    silently change the scale.

    Args:
        windows: One prior window per row, shaped (rows, length).
        currents: The value being ranked in each of those rows.

    Returns:
        One percentile per row, NaN where the window or the value is missing.

    """
    column = currents[:, None]
    below = (windows < column).sum(axis=1)
    equal = (windows == column).sum(axis=1)
    defined = ~np.isnan(windows).any(axis=1) & ~np.isnan(currents)
    scaled = 100.0 * (below + 0.5 * equal) / windows.shape[1]
    return np.where(defined, scaled, np.nan)


def _build_keltner(
    df: pl.DataFrame,
    config: IndicatorConfig,
    resolved: dict[str, Any],
) -> pl.DataFrame:
    """Compute an ATR-width channel around an EMA of the close.

    "Keltner" names several variants, so this one is fixed: the centre is the
    EMA of close over ``length`` and the half-width is ``multiplier`` Wilder
    average true ranges over ``atr_length``, symmetric about the centre. Both
    legs come from the native library, so their seeding matches every other EMA
    and ATR the engine computes.

    ``source`` is ignored: the channel reads the high, low and close columns.

    Args:
        df: OHLCV frame.
        config: Indicator configuration, which names the output columns.
        resolved: Resolved params, carrying ``length``, ``atr_length`` and
            ``multiplier``.

    Returns:
        DataFrame with the upper, middle and lower channel columns added.

    """
    middle = ta.ema(pl.col("close"), timeperiod=int(resolved["length"]))
    width = float(resolved["multiplier"]) * ta.atr(
        pl.col("high"),
        pl.col("low"),
        pl.col("close"),
        timeperiod=int(resolved["atr_length"]),
    )
    return df.with_columns(
        (middle + width).alias(f"{config.id}_upper"),
        middle.alias(f"{config.id}_middle"),
        (middle - width).alias(f"{config.id}_lower"),
    )


# Indicators the engine computes itself, keyed the same way as the native ones.
_CUSTOM_BUILDERS: dict[
    str, Callable[[pl.DataFrame, IndicatorConfig, dict[str, Any]], pl.DataFrame]
] = {
    "VWAP": _build_vwap,
    "AVWAP": _build_avwap,
    "OPENING_RANGE": _build_opening_range,
    "LAG": _build_lag,
    "RATIO": _build_ratio,
    "DIFF": _build_diff,
    "DONCHIAN": _build_donchian,
    "ZSCORE": _build_zscore,
    "RVOL": _build_rvol,
    "PERCENTILE_RANK": _build_percentile_rank,
    "KELTNER": _build_keltner,
}
