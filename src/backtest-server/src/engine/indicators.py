"""Indicator computation engine using polars-talib (Rust backend)."""

from __future__ import annotations

from typing import Any, Callable

import polars as pl
import polars_talib as ta  # type: ignore[import-untyped]

from ..logging_config import get_logger
from ..models.strategy import RAW_PRICE_COLUMNS, IndicatorConfig

logger = get_logger(__name__)

# Map indicator type → polars-talib function + param mapping
# Each entry: (function, {config_param_name: talib_param_name}, multi_output_suffixes | None)
# Optional keys:
#   "input_type": "ohlc" | "dual" | "custom" — determines how _apply_indicator builds the expr
#   "second_source": default second column name for dual-input indicators
#   "intraday_only": True — indicator requires intraday data (not daily)
INDICATOR_REGISTRY: dict[str, dict[str, Any]] = {
    "SMA": {
        "fn": ta.sma,
        "params": {"length": "timeperiod"},
        "outputs": None,
        "output_scale": "price",
    },
    "EMA": {
        "fn": ta.ema,
        "params": {"length": "timeperiod"},
        "outputs": None,
        "output_scale": "price",
    },
    "WMA": {
        "fn": ta.wma,
        "params": {"length": "timeperiod"},
        "outputs": None,
        "output_scale": "price",
    },
    "DEMA": {
        "fn": ta.dema,
        "params": {"length": "timeperiod"},
        "outputs": None,
        "output_scale": "price",
    },
    "TEMA": {
        "fn": ta.tema,
        "params": {"length": "timeperiod"},
        "outputs": None,
        "output_scale": "price",
    },
    "RSI": {
        "fn": ta.rsi,
        "params": {"length": "timeperiod"},
        "outputs": None,
        "output_scale": "0-100",
    },
    "MOM": {
        "fn": ta.mom,
        "params": {"length": "timeperiod"},
        "outputs": None,
        "output_scale": "price_delta",
    },
    "ROC": {
        "fn": ta.roc,
        "params": {"length": "timeperiod"},
        "outputs": None,
        "output_scale": "percent",
    },
    "WILLR": {
        "fn": ta.willr,
        "params": {"length": "timeperiod"},
        "outputs": None,
        "output_scale": "-100-0",
    },
    "CCI": {
        "fn": ta.cci,
        "params": {"length": "timeperiod"},
        "outputs": None,
        "output_scale": "unbounded",
    },
    "ATR": {
        "fn": ta.atr,
        "params": {"length": "timeperiod"},
        "outputs": None,
        "output_scale": "price",
    },
    "ADX": {
        "fn": ta.adx,
        "params": {"length": "timeperiod"},
        "outputs": None,
        "output_scale": "0-100",
    },
    "MFI": {
        "fn": ta.mfi,
        "params": {"length": "timeperiod"},
        "outputs": None,
        "output_scale": "0-100",
    },
    "OBV": {"fn": ta.obv, "params": {}, "outputs": None, "output_scale": "volume"},
    "SAR": {
        "fn": ta.sar,
        "params": {"acceleration": "acceleration", "maximum": "maximum"},
        "outputs": None,
        "output_scale": "price",
    },
    "MACD": {
        "fn": ta.macd,
        "params": {
            "fast_length": "fastperiod",
            "slow_length": "slowperiod",
            "signal_length": "signalperiod",
        },
        "outputs": ["macd", "signal", "hist"],
        "output_scale": "price_delta",
    },
    "BBANDS": {
        "fn": ta.bbands,
        # `std_dev` is fanned out to both nbdevup/nbdevdn in
        # ``_build_talib_kwargs`` so the upper and lower bands stay
        # symmetric for non-default band widths.
        "params": {"length": "timeperiod", "std_dev": "nbdevup"},
        "outputs": ["upper", "middle", "lower"],
        "output_scale": "price",
    },
    "STOCH": {
        "fn": ta.stoch,
        "params": {
            "fastk_period": "fastk_period",
            "slowk_period": "slowk_period",
            "slowd_period": "slowd_period",
        },
        "outputs": ["slowk", "slowd"],
        "output_scale": "0-100",
    },
    "STOCHRSI": {
        "fn": ta.stochrsi,
        "params": {"length": "timeperiod"},
        "outputs": ["fastk", "fastd"],
        "output_scale": "0-100",
    },
    "AROON": {
        "fn": ta.aroon,
        "params": {"length": "timeperiod"},
        "outputs": ["down", "up"],
        "output_scale": "0-100",
    },
    # --- Statistical indicators ---
    "LINEARREG": {
        "fn": ta.linearreg,
        "params": {"length": "timeperiod"},
        "outputs": None,
        "output_scale": "price",
    },
    "LINEARREG_SLOPE": {
        "fn": ta.linearreg_slope,
        "params": {"length": "timeperiod"},
        "outputs": None,
        "output_scale": "unbounded",
    },
    "LINEARREG_ANGLE": {
        "fn": ta.linearreg_angle,
        "params": {"length": "timeperiod"},
        "outputs": None,
        "output_scale": "unbounded",
    },
    "STDDEV": {
        "fn": ta.stddev,
        "params": {"length": "timeperiod"},
        "outputs": None,
        "output_scale": "price",
    },
    # --- Dual-input statistical indicators ---
    "BETA": {
        "fn": ta.beta,
        "params": {"length": "timeperiod"},
        "outputs": None,
        "output_scale": "unbounded",
        "input_type": "dual",
        "second_source": "high",
    },
    "CORREL": {
        "fn": ta.correl,
        "params": {"length": "timeperiod"},
        "outputs": None,
        "output_scale": "unbounded",
        "input_type": "dual",
        "second_source": "high",
    },
    # --- VWAP (custom, intraday-only) ---
    "VWAP": {
        "fn": None,
        "params": {},
        "outputs": None,
        "output_scale": "price",
        "input_type": "custom",
        "intraday_only": True,
    },
}

# --- Candlestick pattern batch registration ---
# All cdl* functions from polars-talib take OHLC and return integer signals.
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

for _cdl_name, _cdl_fn in _CDL_FUNCTIONS:
    INDICATOR_REGISTRY[_cdl_name] = {
        "fn": _cdl_fn,
        "params": {},
        "outputs": None,
        "output_scale": "signal",
        "input_type": "ohlc",
    }

# Indicators that need high, low, close (not just source column)
HLC_INDICATORS: set[str] = {"ATR", "ADX", "CCI", "WILLR", "STOCH", "SAR", "MFI", "VWAP"}
VOLUME_INDICATORS: set[str] = {"OBV", "MFI", "VWAP"}
# Indicators that need open, high, low, close
OHLC_INDICATORS: set[str] = {name for name, _ in _CDL_FUNCTIONS}
# Dual-input indicators that take two column expressions
DUAL_INPUT_INDICATORS: set[str] = {"BETA", "CORREL"}


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
        ValueError: If VWAP is requested with daily timeframe.

    """
    warnings: list[str] = []
    result_df = df.clone()

    for config in indicators:
        indicator_type = config.type.upper()
        if indicator_type not in INDICATOR_REGISTRY:
            warnings.append(f"Skipping unsupported indicator: {config.type}")
            continue

        registry_entry = INDICATOR_REGISTRY[indicator_type]

        # Timeframe guard for intraday-only indicators
        if registry_entry.get("intraday_only") and timeframe == "daily":
            msg = "VWAP requires intraday data. Use timeframe '5min', '15min', or '1hour'."
            raise ValueError(msg)

        try:
            result_df = _apply_indicator(result_df, config, registry_entry, warnings)
        except Exception as exc:
            warnings.append(f"Failed to compute {config.id}: {exc}")
            logger.warning("indicator_failed", indicator=config.id, error=str(exc))

    return result_df, warnings


def get_supported_indicators() -> dict[str, Any]:
    """Return registry of supported indicators with parameter info.

    Returns:
        Dict with 'indicators' (registry) and 'raw_columns' (always-valid operands).

    """
    indicators: dict[str, dict[str, Any]] = {}
    for name, entry in INDICATOR_REGISTRY.items():
        indicators[name] = {
            "params": list(entry["params"].keys()),
            "output_scale": entry["output_scale"],
            "multi_output": entry["outputs"] is not None,
            "output_columns": entry["outputs"],
            "needs_hlc": name in HLC_INDICATORS,
            "needs_volume": name in VOLUME_INDICATORS,
            "needs_ohlc": name in OHLC_INDICATORS,
            "dual_input": name in DUAL_INPUT_INDICATORS,
            "intraday_only": bool(entry.get("intraday_only")),
        }
    return {
        "indicators": indicators,
        "raw_columns": sorted(RAW_PRICE_COLUMNS),
        "raw_columns_note": (
            "These OHLCV columns are always available as operand references "
            'in conditions (e.g., {"indicator": "close"} to compare price '
            "against a computed indicator like VWAP)."
        ),
        "source_note": (
            "An indicator's `source` accepts a raw column or the `id` of any "
            "indicator declared before it, so indicators can be built on one "
            "another. Indicators are computed in list order in a single pass: "
            "a source naming an indicator declared later has no column to read "
            "and is reported as a warning."
        ),
    }


def _apply_indicator(
    df: pl.DataFrame,
    config: IndicatorConfig,
    registry_entry: dict[str, Any],
    warnings: list[str],
) -> pl.DataFrame:
    """Apply a single indicator to the DataFrame.

    Args:
        df: Input DataFrame.
        config: Indicator configuration.
        registry_entry: Registry entry with function and param mapping.
        warnings: Warning list to append to.

    Returns:
        DataFrame with indicator column(s) added.

    """
    input_type = registry_entry.get("input_type")

    # Custom indicators (VWAP) bypass the normal talib path
    if input_type == "custom":
        return _apply_custom_indicator(df, config)

    return _apply_talib_indicator(df, config, registry_entry, warnings)


def _apply_talib_indicator(
    df: pl.DataFrame,
    config: IndicatorConfig,
    registry_entry: dict[str, Any],
    warnings: list[str],
) -> pl.DataFrame:
    """Apply a polars-talib indicator to the DataFrame.

    Args:
        df: Input DataFrame.
        config: Indicator configuration.
        registry_entry: Registry entry with function and param mapping.
        warnings: Warning list to append to.

    Returns:
        DataFrame with indicator column(s) added.

    """
    indicator_type = config.type.upper()
    fn = registry_entry["fn"]
    param_map: dict[str, str] = registry_entry["params"]
    outputs: list[str] | None = registry_entry["outputs"]
    input_type = registry_entry.get("input_type")

    talib_kwargs = _build_talib_kwargs(indicator_type, param_map, config.params)

    # Check minimum data length for period-based indicators
    period = config.params.get("length", config.params.get("slow_length", 0))
    if period and len(df) < period:
        warnings.append(f"Insufficient data for {config.id}: need {period} rows, have {len(df)}")

    # Build the expression based on indicator input type
    expr = _build_indicator_expr(
        fn,
        indicator_type,
        input_type,
        config,
        talib_kwargs,
        registry_entry=registry_entry,
    )

    # Handle multi-output vs single-output
    if outputs is not None:
        return _apply_multi_output(df, config.id, expr, outputs)

    return df.with_columns(expr.alias(config.id))


def _build_talib_kwargs(
    indicator_type: str,
    param_map: dict[str, str],
    user_params: dict[str, Any],
) -> dict[str, Any]:
    """Translate user-facing param names into talib kwargs.

    Most indicators have a 1:1 mapping. Bollinger Bands is special: the user
    provides one `std_dev`, which must be applied to both the upper and
    lower band parameters so the bands stay symmetric.
    """
    talib_kwargs: dict[str, Any] = {}
    for config_key, talib_key in param_map.items():
        if config_key in user_params:
            talib_kwargs[talib_key] = user_params[config_key]
    if indicator_type == "BBANDS" and "std_dev" in user_params:
        talib_kwargs["nbdevdn"] = user_params["std_dev"]
    return talib_kwargs


def _build_indicator_expr(  # noqa: PLR0913
    fn: Callable[..., Any],
    indicator_type: str,
    input_type: str | None,
    config: IndicatorConfig,
    talib_kwargs: dict[str, Any],
    registry_entry: dict[str, Any] | None = None,
) -> pl.Expr:
    """Build the Polars expression for a talib indicator call.

    Args:
        fn: The polars-talib function to call.
        indicator_type: Uppercase indicator type name.
        input_type: Registry input_type ("ohlc", "dual", or None).
        config: Indicator configuration.
        talib_kwargs: Mapped talib keyword arguments.
        registry_entry: Registry metadata for the indicator (defaults, etc.).

    Returns:
        Polars expression for the indicator computation.

    """
    if input_type == "ohlc":
        return fn(  # type: ignore[no-any-return]
            pl.col("open"),
            pl.col("high"),
            pl.col("low"),
            pl.col("close"),
            **talib_kwargs,
        )

    if input_type == "dual":
        # Precedence: explicit user param > registry default > config.source.
        # The registry's `second_source` carries each dual-input indicator's
        # natural counterpart (e.g. BETA against `high`); falling all the way
        # through to `config.source` would compare a column to itself and
        # produce a tautology (beta=1, correl=1).
        registry_default = registry_entry.get("second_source") if registry_entry else None
        second_col = config.params.get("second_source") or registry_default or config.source
        return fn(  # type: ignore[no-any-return]
            pl.col(config.source),
            pl.col(second_col),
            **talib_kwargs,
        )

    if indicator_type in HLC_INDICATORS:
        return fn(  # type: ignore[no-any-return]
            pl.col("high"),
            pl.col("low"),
            pl.col("close"),
            **talib_kwargs,
        )

    if indicator_type in VOLUME_INDICATORS and indicator_type != "MFI":
        return fn(pl.col(config.source), pl.col("volume"), **talib_kwargs)  # type: ignore[no-any-return]

    return fn(pl.col(config.source), **talib_kwargs)  # type: ignore[no-any-return]


def _apply_multi_output(
    df: pl.DataFrame,
    indicator_id: str,
    expr: pl.Expr,
    outputs: list[str],
) -> pl.DataFrame:
    """Apply a multi-output indicator expression and name the columns.

    Args:
        df: Input DataFrame.
        indicator_id: Base indicator id for column naming.
        expr: The multi-output Polars expression.
        outputs: List of output suffix names.

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


def _apply_custom_indicator(
    df: pl.DataFrame,
    config: IndicatorConfig,
) -> pl.DataFrame:
    """Apply a custom (non-talib) indicator.

    Currently supports: VWAP (session-resetting intraday VWAP).

    Args:
        df: Input OHLCV DataFrame with datetime column.
        config: Indicator configuration.

    Returns:
        DataFrame with the custom indicator column added.

    """
    indicator_type = config.type.upper()
    if indicator_type == "VWAP":
        return _compute_vwap(df, config.id)
    msg = f"Unknown custom indicator: {indicator_type}"
    raise ValueError(msg)


def _compute_vwap(df: pl.DataFrame, col_name: str) -> pl.DataFrame:
    """Compute session-resetting VWAP.

    VWAP = cumsum(typical_price * volume) / cumsum(volume),
    resetting at each new trading session (date boundary).

    Args:
        df: OHLCV DataFrame with a datetime 'date' column.
        col_name: Name for the output VWAP column.

    Returns:
        DataFrame with VWAP column added.

    """
    typical_price = (pl.col("high") + pl.col("low") + pl.col("close")) / 3.0
    tp_volume = typical_price * pl.col("volume")

    # Group by date portion of datetime for session reset
    session_date = pl.col("date").cast(pl.Date)

    return df.with_columns(
        (
            tp_volume.cum_sum().over(session_date) / pl.col("volume").cum_sum().over(session_date)
        ).alias(col_name)
    )
