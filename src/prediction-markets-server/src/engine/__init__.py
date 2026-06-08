"""Public engine surface for calibration, buckets, observations, longshot."""

from __future__ import annotations

from .backtest import (
    BacktestMarket,
    Trade,
    build_monte_carlo_input,
    simulate_rule,
    summarize_trades,
    trade_to_dict,
)
from .buckets import (
    DEFAULT_PRICE_BUCKET_SIZE,
    TTR_BUCKETS,
    PriceBucketBounds,
    all_ttr_buckets,
    price_bucket,
    remaining_seconds,
    time_to_resolution_bucket,
    ttr_bucket_seconds_upper,
)
from .calibration import (
    CalibrationBucket,
    CalibrationSummary,
    aggregate_calibration,
    summary_to_dict,
)
from .edge import EdgeEstimate, EdgeReason, estimate_edge, estimate_to_dict
from .holdout import LOW_N_FLOOR, HoldoutSpec, build_out_of_sample
from .longshot import LongshotResult, Side, TailStats, evaluate_longshot_bias, result_to_dict
from .observations import (
    MarketContext,
    Observation,
    SamplingMode,
    bucket_observations,
    select_earliest_eligible_observation,
    split_by_entry,
)
from .risk import MAX_NUM_PATHS, MonteCarloResult, SamplingMethod, run_monte_carlo
from .risk import result_to_dict as monte_carlo_to_dict
from .rules import (
    SUPPORTED_EXIT_TYPES,
    SUPPORTED_SIDES,
    SUPPORTED_VOLUME_FILTER_MODES,
    EntryRule,
    ExitRule,
    HoldToResolutionExit,
    PredictionRule,
    RuleFilters,
    StopTakeProfitExit,
    validate_rule,
)
from .sizing import (
    KELLY_GRID,
    KellyEstimates,
    SizingResult,
    drawdown_constrained_fraction,
    estimate_kelly,
    estimate_sizing,
)

__all__ = [
    "DEFAULT_PRICE_BUCKET_SIZE",
    "SUPPORTED_EXIT_TYPES",
    "SUPPORTED_SIDES",
    "SUPPORTED_VOLUME_FILTER_MODES",
    "TTR_BUCKETS",
    "BacktestMarket",
    "CalibrationBucket",
    "CalibrationSummary",
    "EdgeEstimate",
    "EdgeReason",
    "LOW_N_FLOOR",
    "EntryRule",
    "ExitRule",
    "HoldToResolutionExit",
    "HoldoutSpec",
    "LongshotResult",
    "MarketContext",
    "Observation",
    "PredictionRule",
    "PriceBucketBounds",
    "RuleFilters",
    "SamplingMode",
    "Side",
    "StopTakeProfitExit",
    "TailStats",
    "Trade",
    "aggregate_calibration",
    "all_ttr_buckets",
    "bucket_observations",
    "build_monte_carlo_input",
    "build_out_of_sample",
    "evaluate_longshot_bias",
    "price_bucket",
    "remaining_seconds",
    "result_to_dict",
    "select_earliest_eligible_observation",
    "simulate_rule",
    "split_by_entry",
    "summarize_trades",
    "summary_to_dict",
    "time_to_resolution_bucket",
    "trade_to_dict",
    "ttr_bucket_seconds_upper",
    "validate_rule",
    "KELLY_GRID",
    "KellyEstimates",
    "MAX_NUM_PATHS",
    "MonteCarloResult",
    "SamplingMethod",
    "SizingResult",
    "drawdown_constrained_fraction",
    "estimate_edge",
    "estimate_kelly",
    "estimate_sizing",
    "estimate_to_dict",
    "monte_carlo_to_dict",
    "run_monte_carlo",
]
