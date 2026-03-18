"""Indicator computation engine using polars-talib (Rust backend)."""

from __future__ import annotations

from typing import Any

import polars as pl
import polars_talib as ta  # type: ignore[import-untyped]

from ..logging_config import get_logger
from ..models.strategy import IndicatorConfig

logger = get_logger(__name__)

# Map indicator type → polars-talib function + param mapping
# Each entry: (function, {config_param_name: talib_param_name}, multi_output_suffixes | None)
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
}

# Indicators that need high, low, close (not just source column)
HLC_INDICATORS: set[str] = {"ATR", "ADX", "CCI", "WILLR", "STOCH", "SAR", "MFI"}
VOLUME_INDICATORS: set[str] = {"OBV", "MFI"}


def compute_indicators(
    df: pl.DataFrame,
    indicators: list[IndicatorConfig],
) -> tuple[pl.DataFrame, list[str]]:
    """Compute all indicators and add as columns to the DataFrame.

    Args:
        df: OHLCV DataFrame with columns: date, open, high, low, close, volume.
        indicators: List of indicator configurations to compute.

    Returns:
        Tuple of (DataFrame with indicator columns added, list of warnings).

    """
    warnings: list[str] = []
    result_df = df.clone()

    for config in indicators:
        indicator_type = config.type.upper()
        if indicator_type not in INDICATOR_REGISTRY:
            warnings.append(f"Skipping unsupported indicator: {config.type}")
            continue

        registry_entry = INDICATOR_REGISTRY[indicator_type]
        try:
            result_df = _apply_indicator(result_df, config, registry_entry, warnings)
        except Exception as exc:
            warnings.append(f"Failed to compute {config.id}: {exc}")
            logger.warning("indicator_failed", indicator=config.id, error=str(exc))

    return result_df, warnings


def get_supported_indicators() -> dict[str, dict[str, Any]]:
    """Return registry of supported indicators with parameter info.

    Returns:
        Dict mapping indicator type to params and description.

    """
    result: dict[str, dict[str, Any]] = {}
    for name, entry in INDICATOR_REGISTRY.items():
        result[name] = {
            "params": list(entry["params"].keys()),
            "output_scale": entry["output_scale"],
            "multi_output": entry["outputs"] is not None,
            "output_columns": entry["outputs"],
            "needs_hlc": name in HLC_INDICATORS,
            "needs_volume": name in VOLUME_INDICATORS,
        }
    return result


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
    indicator_type = config.type.upper()
    fn = registry_entry["fn"]
    param_map: dict[str, str] = registry_entry["params"]
    outputs: list[str] | None = registry_entry["outputs"]

    # Build talib kwargs from config params
    talib_kwargs: dict[str, Any] = {}
    for config_key, talib_key in param_map.items():
        if config_key in config.params:
            talib_kwargs[talib_key] = config.params[config_key]

    # Check minimum data length for period-based indicators
    period = config.params.get("length", config.params.get("slow_length", 0))
    if period and len(df) < period:
        warnings.append(f"Insufficient data for {config.id}: need {period} rows, have {len(df)}")

    # Build the expression based on indicator type
    if indicator_type in HLC_INDICATORS:
        expr = fn(pl.col("high"), pl.col("low"), pl.col("close"), **talib_kwargs)
    elif indicator_type in VOLUME_INDICATORS and indicator_type != "MFI":
        expr = fn(pl.col(config.source), pl.col("volume"), **talib_kwargs)
    else:
        expr = fn(pl.col(config.source), **talib_kwargs)

    # Handle multi-output vs single-output
    if outputs is not None:
        result = df.select(expr)
        # polars-talib returns struct column for multi-output indicators
        if result.width == 1 and len(outputs) > 1:
            result = result.unnest(result.columns[0])
        for idx, suffix in enumerate(outputs):
            col_name = f"{config.id}_{suffix}"
            if idx < result.width:
                df = df.with_columns(result.get_column(result.columns[idx]).alias(col_name))
    else:
        df = df.with_columns(expr.alias(config.id))

    return df
