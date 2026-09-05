"""Strategy definition models for structured backtest configuration."""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .indicator_catalog import INDICATOR_CATALOG, IndicatorSpec, parse_iso_date


@dataclass
class WindowResult:
    """Metrics for a single walk-forward validation window."""

    window_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_metrics: dict[str, Any]
    test_metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON output."""
        return {
            "window_id": self.window_id,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
            "train_metrics": self.train_metrics,
            "test_metrics": self.test_metrics,
        }


@dataclass
class WalkForwardResult:
    """Aggregate results from walk-forward validation across multiple windows."""

    windows: list[WindowResult]
    n_windows: int
    mean_test_sharpe: float
    std_test_sharpe: float
    mean_test_win_rate: float
    mean_test_max_drawdown: float
    consistency_score: float  # % of windows where test Sharpe > 0
    degradation: float  # mean(train_sharpe - test_sharpe)
    total_runtime_seconds: float
    execution_config: dict[str, Any]  # resolved slippage/commission/capital the windows ran under
    strategy: dict[str, Any]  # resolved definition, so a polled job is self-describing
    failed_windows: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON output."""
        return {
            "windows": [w.to_dict() for w in self.windows],
            "n_windows": self.n_windows,
            "failed_windows": self.failed_windows,
            "mean_test_sharpe": round(self.mean_test_sharpe, 4),
            "std_test_sharpe": round(self.std_test_sharpe, 4),
            "mean_test_win_rate": round(self.mean_test_win_rate, 2),
            "mean_test_max_drawdown": round(self.mean_test_max_drawdown, 2),
            "consistency_score": round(self.consistency_score, 2),
            "degradation": round(self.degradation, 4),
            "total_runtime_seconds": round(self.total_runtime_seconds, 2),
            "execution_config": self.execution_config,
            "strategy": self.strategy,
            "fill_timing": FILL_TIMING,
        }


# The engine evaluates conditions on a bar's close and fills at the next bar's
# open (see portfolio_backtester's use of ``opens[bar_idx]``). Naming it in the
# payload is what lets a later turn rule out look-ahead from a stored result.
FILL_TIMING = "signal_at_bar_close_fill_at_next_bar_open"

# Which fills carry the configured execution costs. Slippage and spread move
# signal exits against the position; level-based and forced exits do not, so a
# stop-heavy result reads better than it would with the same costs applied.
FILL_MODEL = (
    "slippage/spread on signal exits; stop/trailing/target at level or worse open; "
    "eod, time stop and end-of-backtest at close"
)


# Derived views over the one catalog every layer reads. Registering an
# indicator there is what makes it validate, compute and appear in discovery.
SUPPORTED_INDICATORS: frozenset[str] = frozenset(INDICATOR_CATALOG)

# Accepted `params` keys per indicator type. Any other key is dropped before
# the engine builds its call, so a typo would silently run library defaults.
INDICATOR_PARAM_NAMES: dict[str, frozenset[str]] = {
    name: frozenset(spec.params) for name, spec in INDICATOR_CATALOG.items()
}

INTRADAY_ONLY_INDICATORS: frozenset[str] = frozenset(
    name for name, spec in INDICATOR_CATALOG.items() if spec.intraday_only
)

# Raw OHLCV columns always present in DataFrames — valid as operand references
RAW_PRICE_COLUMNS: set[str] = {"open", "high", "low", "close", "volume"}

# Reserved source column holding the benchmark's close, aligned as of each bar.
# It is not a raw column: it exists only when `universe.benchmark` is set, and
# the engine attaches it before indicators are computed so an indicator can read
# it and a rule can compare it against the symbol's own series.
BENCHMARK_CLOSE_COLUMN = "benchmark_close"

SUPPORTED_OPERATORS: set[str] = {
    "greater_than",
    "less_than",
    "crosses_above",
    "crosses_below",
    "equals",
    "not_equals",
    "after_time",
    "before_time",
}

SUPPORTED_SIZING_METHODS: set[str] = {"equal_weight", "fixed_pct", "atr_risk"}

SUPPORTED_ALLOCATION_MODES: set[str] = {"independent", "portfolio"}

# Design doc: Phase 2.2 — timeframe constants
SUPPORTED_TIMEFRAMES: set[str] = {"daily", "1hour", "15min", "5min"}

TIMEFRAME_MAX_YEARS: dict[str, int] = {
    "daily": 30,
    "1hour": 5,
    "15min": 2,
    "5min": 2,
}

# Bars per trading day — validate 1hour against actual FMP data
BARS_PER_DAY: dict[str, int] = {
    "daily": 1,
    "1hour": 7,
    "15min": 26,
    "5min": 78,
}

# Minutes one bar covers, for the intraday timeframes only. Read by the session
# helpers and by any indicator whose parameter is a clock interval rather than
# a bar count, so the two cannot drift apart.
MINUTES_PER_BAR: dict[str, int] = {
    "1hour": 60,
    "15min": 15,
    "5min": 5,
}

# Multi-output indicators produce columns named {id}_{suffix}.
# Rules can reference either the bare id or the suffixed name.
MULTI_OUTPUT_SUFFIXES: dict[str, list[str]] = {
    name: list(spec.outputs) for name, spec in INDICATOR_CATALOG.items() if spec.outputs
}

SUPPORTED_LOGIC: set[str] = {"AND", "OR"}


@dataclass
class IndicatorConfig:
    """Configuration for a single technical indicator."""

    id: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)
    source: str = "close"

    def validate(self) -> list[str]:
        """Validate indicator config, return list of errors."""
        errors: list[str] = []
        indicator_type = self.type.upper()
        spec = INDICATOR_CATALOG.get(indicator_type)
        if spec is None:
            errors.append(
                f"Unsupported indicator '{self.type}'. Supported: {sorted(SUPPORTED_INDICATORS)}"
            )
        if not self.id:
            errors.append("Indicator id cannot be empty")
        if spec is None:
            return errors
        errors.extend(self._unknown_param_errors(indicator_type))
        errors.extend(self._param_value_errors(spec, indicator_type))
        errors.extend(self._cross_param_errors(spec, indicator_type))
        return errors

    def _param_value_errors(self, spec: IndicatorSpec, indicator_type: str) -> list[str]:
        """Check each supplied param against its kind and range.

        A period the native library rejects surfaces as a compute warning that
        drops the indicator, so the strategy runs without the filter it was
        built around. Catching it here keeps that from reaching the engine.

        Args:
            spec: Catalog entry for this indicator type.
            indicator_type: Uppercased indicator type, already known supported.

        Returns:
            One error per offending or missing param.

        """
        errors: list[str] = []
        for name, param in spec.params.items():
            if name in self.params:
                errors.extend(param.errors(self.id, indicator_type, name, self.params[name]))
            elif param.default is None:
                errors.append(f"Indicator '{self.id}' ({indicator_type}) requires param '{name}'")
        return errors

    def _cross_param_errors(self, spec: IndicatorSpec, indicator_type: str) -> list[str]:
        """Reject param combinations the native library silently reinterprets.

        TA-Lib swaps MACD's periods when the fast one is not the shorter, so a
        reversed pair computes a working indicator that means the opposite of
        what was asked for.

        Args:
            spec: Catalog entry for this indicator type.
            indicator_type: Uppercased indicator type.

        Returns:
            One error naming the rule, or an empty list.

        """
        if indicator_type != "MACD":
            return []
        resolved = spec.resolve_params(self.params)
        fast, slow = resolved.get("fast_length"), resolved.get("slow_length")
        if not isinstance(fast, int) or not isinstance(slow, int) or fast < slow:
            return []
        return [
            f"Indicator '{self.id}' (MACD) requires fast_length < slow_length; "
            f"got fast_length={fast}, slow_length={slow}."
        ]

    def _unknown_param_errors(self, indicator_type: str) -> list[str]:
        """Reject params the engine would drop instead of honouring.

        Args:
            indicator_type: Uppercased indicator type, already checked above.

        Returns:
            One error naming the offending keys and the accepted names, or
            an empty list. Unsupported types yield nothing — the type error
            above already tells the caller what to fix.

        """
        accepted = INDICATOR_PARAM_NAMES.get(indicator_type)
        if accepted is None:
            return []
        unknown = sorted(set(self.params) - accepted)
        if not unknown:
            return []
        return [
            f"Indicator '{self.id}' ({indicator_type}) has unsupported param(s): "
            f"{', '.join(unknown)}. Accepted params: {', '.join(sorted(accepted)) or 'none'}."
        ]


@dataclass
class Operand:
    """Left or right side of a condition — indicator ref, constant, or time.

    Phase 4.1: Extended with time_of_day and time fields for session-aware rules.
    """

    indicator: str | None = None
    constant: float | None = None
    time_of_day: str | None = None  # "current" — resolves to bar's HH:MM as numeric
    time: str | None = None  # "10:00" — explicit HH:MM string

    def validate(self) -> list[str]:
        """Validate operand has exactly one value set."""
        set_count = sum(
            v is not None for v in [self.indicator, self.constant, self.time_of_day, self.time]
        )
        if set_count == 0:
            return ["Operand must have one of: indicator, constant, time_of_day, time"]
        if set_count > 1:
            return ["Operand must have exactly one field set"]
        if self.time is not None:
            parts = self.time.split(":")
            if len(parts) != 2:  # noqa: PLR2004
                return [f"Invalid time format '{self.time}', expected HH:MM"]
        return []


@dataclass
class Condition:
    """A single comparison condition for entry/exit rules."""

    left: Operand
    operator: str
    right: Operand

    def validate(self) -> list[str]:
        """Validate condition components."""
        errors = self.left.validate() + self.right.validate()
        if self.operator not in SUPPORTED_OPERATORS:
            errors.append(
                f"Unsupported operator '{self.operator}'. Supported: {sorted(SUPPORTED_OPERATORS)}"
            )
        return errors


@dataclass
class RuleSet:
    """A set of conditions combined with AND/OR logic."""

    logic: str
    conditions: list[Condition] = field(default_factory=list)

    def validate(self) -> list[str]:
        """Validate ruleset."""
        errors: list[str] = []
        if self.logic not in SUPPORTED_LOGIC:
            errors.append(f"Logic must be 'AND' or 'OR', got '{self.logic}'")
        for cond in self.conditions:
            errors.extend(cond.validate())
        return errors


@dataclass
class PositionSizing:
    """Position sizing configuration."""

    method: str = "equal_weight"
    max_position_pct: float = 20.0
    max_positions: int = 5
    allocation_mode: str = "independent"
    # Share of equity the ``atr_risk`` method budgets to the loss its ATR stop
    # would realize. Read by no other method.
    risk_pct: float | None = None

    def validate(self) -> list[str]:
        """Validate position sizing."""
        errors: list[str] = []
        if self.method not in SUPPORTED_SIZING_METHODS:
            errors.append(
                f"Unsupported sizing method '{self.method}'. "
                f"Supported: {sorted(SUPPORTED_SIZING_METHODS)}"
            )
        if self.allocation_mode not in SUPPORTED_ALLOCATION_MODES:
            errors.append(
                f"Unsupported allocation_mode '{self.allocation_mode}'. "
                f"Supported: {sorted(SUPPORTED_ALLOCATION_MODES)}"
            )
        if not 0 < self.max_position_pct <= _MAX_PCT:
            errors.append(f"max_position_pct must be in (0, 100]; got {self.max_position_pct}")
        if self.max_positions < 1:
            errors.append(f"max_positions must be >= 1; got {self.max_positions}")
        errors.extend(self._risk_pct_errors())
        return errors

    def _risk_pct_errors(self) -> list[str]:
        """Check the risk budget is present, usable, and read by this method.

        Returns:
            One error when ``atr_risk`` has no budget or an out-of-range one,
            and one when another method carries a budget nothing would read.

        """
        if self.method != "atr_risk":
            return [] if self.risk_pct is None else ["risk_pct applies only to method atr_risk"]
        if self.risk_pct is None:
            return ["method atr_risk requires risk_pct"]
        if not 0 < self.risk_pct <= _MAX_PCT:
            return [f"risk_pct must be in (0, 100]; got {self.risk_pct}"]
        return []


def _is_invalid_bar_count(value: int | None) -> bool:
    """Report whether a bar-count limit is not a whole number of bars.

    ``from_dict`` reads these limits straight out of JSON, so a float or a
    bool arrives typed as an int and would compare its way through the
    engine's bar arithmetic unnoticed.

    Args:
        value: The configured limit, or None when the rule is off.

    Returns:
        True when the value is present but is not an integer of at least 1.

    """
    if value is None:
        return False
    return isinstance(value, bool) or not isinstance(value, int) or value < 1


@dataclass
class RiskManagement:
    """Risk management parameters."""

    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    close_eod: bool = False  # Phase 3.4: force close at session end
    no_entry_after: str | None = None  # Phase 3.4: "15:30" — no new entries after
    # Id of a declared ATR indicator, read in price units by the ATR stop.
    atr_indicator: str | None = None
    # Multiples of that ATR the initial stop sits below the fill.
    stop_atr_multiple: float | None = None
    # Percent of the highest high since entry the trailing stop sits below.
    trailing_stop_pct: float | None = None
    # Multiples of the completed bar's ATR the trailing stop sits below that
    # same highest high. Exclusive with the percent form.
    trailing_stop_atr_multiple: float | None = None
    # Bars a position may be held before it is closed at that bar's close.
    # The entry bar is the first one counted.
    max_holding_bars: int | None = None
    # Bars after a symbol's last exit during which a new entry cannot fill.
    reentry_cooldown_bars: int | None = None

    def validate(self) -> list[str]:
        """Reject negative or absurd stop/take-profit values."""
        errors: list[str] = []
        if self.stop_loss_pct is not None and not 0 < self.stop_loss_pct <= _MAX_PCT:
            errors.append(f"stop_loss_pct must be in (0, 100]; got {self.stop_loss_pct}")
        if self.take_profit_pct is not None and self.take_profit_pct <= 0:
            errors.append(f"take_profit_pct must be positive; got {self.take_profit_pct}")
        errors.extend(self._atr_stop_errors())
        errors.extend(self._trailing_stop_errors())
        errors.extend(self._bar_limit_errors())
        return errors

    def reads_atr(self) -> bool:
        """Report whether any risk rule consumes the named ATR.

        Returns:
            True when a rule reads ``atr_indicator``, which is what makes
            naming one meaningful.

        """
        return self.stop_atr_multiple is not None or self.trailing_stop_atr_multiple is not None

    def _bar_limit_errors(self) -> list[str]:
        """Check every limit counted in bars is a whole number of them.

        Returns:
            One error per limit that is present but is not an integer of at
            least 1; empty when no such limit is set.

        """
        errors: list[str] = []
        if _is_invalid_bar_count(self.max_holding_bars):
            errors.append(f"max_holding_bars must be an integer >= 1; got {self.max_holding_bars}")
        if _is_invalid_bar_count(self.reentry_cooldown_bars):
            errors.append(
                f"reentry_cooldown_bars must be an integer >= 1; got {self.reentry_cooldown_bars}"
            )
        return errors

    def _trailing_stop_errors(self) -> list[str]:
        """Check the trail has exactly one distance and can be walked.

        A trail may sit under a fixed stop — they combine into one effective
        level — but the two ways of measuring the trail itself are exclusive:
        a percent of the high water mark and a multiple of the ATR would place
        two different levels with no rule for which one wins.

        Returns:
            One error per unusable distance, and one when both forms are set
            or the ATR form has no ATR to read; empty when no trail is set.

        """
        errors: list[str] = []
        pct, multiple = self.trailing_stop_pct, self.trailing_stop_atr_multiple
        if pct is not None and not 0 < pct < _MAX_PCT:
            errors.append(f"trailing_stop_pct must be in (0, 100); got {pct}")
        if multiple is not None and (not math.isfinite(multiple) or multiple <= 0):
            errors.append(
                f"trailing_stop_atr_multiple must be a positive, finite number; got {multiple}"
            )
        if pct is not None and multiple is not None:
            errors.append(
                "trailing_stop_pct and trailing_stop_atr_multiple are mutually exclusive; set one"
            )
        if multiple is not None and self.atr_indicator is None:
            errors.append("trailing_stop_atr_multiple requires atr_indicator")
        return errors

    def _atr_stop_errors(self) -> list[str]:
        """Check the ATR stop is a usable distance and has exactly one stop rule.

        Returns:
            One error per unusable multiple, per competing percent stop, and
            per missing ATR reference; empty when no ATR stop is configured.

        """
        if self.stop_atr_multiple is None:
            return []
        errors: list[str] = []
        if not math.isfinite(self.stop_atr_multiple) or self.stop_atr_multiple <= 0:
            errors.append(
                f"stop_atr_multiple must be a positive, finite number; got {self.stop_atr_multiple}"
            )
        if self.stop_loss_pct is not None:
            errors.append("stop_loss_pct and stop_atr_multiple are mutually exclusive; set one")
        if self.atr_indicator is None:
            errors.append("stop_atr_multiple requires atr_indicator")
        return errors


@dataclass
class ExecutionConfig:
    """Execution cost parameters for backtesting.

    Controls slippage, commissions, and starting capital.
    Defaults match prior hardcoded values for backward compatibility.
    """

    slippage_pct: float = 0.1
    commission_pct: float = 0.1
    initial_capital: float = 100_000.0
    volume_scaled_slippage: bool = False
    estimate_spread: bool = False

    def validate(self) -> list[str]:
        """Reject negative fees and non-positive starting capital."""
        errors: list[str] = []
        if self.slippage_pct < 0:
            errors.append(f"slippage_pct must be >= 0; got {self.slippage_pct}")
        if self.commission_pct < 0:
            errors.append(f"commission_pct must be >= 0; got {self.commission_pct}")
        if self.initial_capital <= 0:
            errors.append(f"initial_capital must be > 0; got {self.initial_capital}")
        return errors


_MIN_BACKTEST_DAYS = 30
_MAX_PCT = 100.0


@dataclass
class DataConfig:
    """Date range, train/test split, and timeframe configuration."""

    start_date: str
    end_date: str
    train_end_date: str | None = None
    timeframe: str = "daily"

    def get_train_end(self) -> date:
        """Get train end date, defaulting to 75% of the range."""
        if self.train_end_date:
            return date.fromisoformat(self.train_end_date)
        start = date.fromisoformat(self.start_date)
        end = date.fromisoformat(self.end_date)
        split_days = int((end - start).days * 0.75)
        return start + timedelta(days=split_days)

    def validate(self) -> list[str]:
        """Validate date configuration and timeframe."""
        errors: list[str] = []

        # Timeframe validation (Phase 2.2)
        if self.timeframe not in SUPPORTED_TIMEFRAMES:
            errors.append(
                f"Unsupported timeframe '{self.timeframe}'. "
                f"Supported: {sorted(SUPPORTED_TIMEFRAMES)}"
            )

        try:
            start = date.fromisoformat(self.start_date)
        except ValueError:
            errors.append(f"Invalid start_date format: {self.start_date}")
            return errors
        try:
            end = date.fromisoformat(self.end_date)
        except ValueError:
            errors.append(f"Invalid end_date format: {self.end_date}")
            return errors
        if start >= end:
            errors.append(f"start_date ({start}) must be before end_date ({end})")
        elif (end - start).days < _MIN_BACKTEST_DAYS:
            errors.append(f"Date range must be at least {_MIN_BACKTEST_DAYS} days")

        # Retention limit enforcement (Phase 2.2)
        if start < end:
            years_span = (end - start).days / 365.25
            max_years = TIMEFRAME_MAX_YEARS.get(self.timeframe, 30)
            if years_span > max_years:
                errors.append(
                    f"Timeframe '{self.timeframe}' limited to {max_years} years, "
                    f"requested {years_span:.1f}"
                )

        if self.train_end_date:
            try:
                train_end = date.fromisoformat(self.train_end_date)
            except ValueError:
                errors.append(f"Invalid train_end_date: {self.train_end_date}")
                return errors
            if train_end <= start or train_end >= end:
                errors.append("train_end_date must be between start_date and end_date")
        return errors


MAX_UNIVERSE_SIZE = 250


@dataclass
class Universe:
    """Stock universe and benchmark configuration."""

    symbols: list[str] = field(default_factory=list)
    benchmark: str = "SPY"

    def validate(self) -> list[str]:
        """Reject universes too large to download safely.

        Each symbol triggers at least one FMP candle request and as many
        indicator computations as the strategy defines. Without a cap a
        strategy with `symbols: [...500 names]` can exhaust provider
        quotas or available memory mid-backtest.
        """
        errors: list[str] = []
        if len(self.symbols) > MAX_UNIVERSE_SIZE:
            errors.append(
                f"Universe has {len(self.symbols)} symbols; max supported is "
                f"{MAX_UNIVERSE_SIZE}. Narrow with the screener before backtesting."
            )
        return errors


@dataclass
class StrategyDefinition:
    """Complete strategy definition output by the agent."""

    name: str
    universe: Universe
    data_config: DataConfig
    indicators: list[IndicatorConfig]
    entry_rules: RuleSet
    exit_rules: RuleSet
    position_sizing: PositionSizing = field(default_factory=PositionSizing)
    risk_management: RiskManagement = field(default_factory=RiskManagement)
    execution_config: ExecutionConfig = field(default_factory=ExecutionConfig)

    def validate(self) -> list[str]:
        """Validate entire strategy, return list of errors."""
        errors: list[str] = []
        if not self.name:
            errors.append("Strategy name cannot be empty")
        if not self.universe.symbols:
            errors.append("Universe must have at least one symbol")
        errors.extend(self.universe.validate())
        errors.extend(self.data_config.validate())
        for ind in self.indicators:
            errors.extend(ind.validate())
        duplicate_ids = sorted(
            ind_id for ind_id, count in Counter(i.id for i in self.indicators).items() if count > 1
        )
        if duplicate_ids:
            errors.append(
                f"Duplicate indicator id(s): {', '.join(duplicate_ids)}. "
                "Each indicator id must be unique; a repeat overwrites the earlier column."
            )
        errors.extend(self.entry_rules.validate())
        errors.extend(self.exit_rules.validate())
        errors.extend(self.position_sizing.validate())
        errors.extend(self.risk_management.validate())
        errors.extend(self.execution_config.validate())

        # Portfolio mode requires daily timeframe (intraday bars collapse to same date key)
        if (
            self.position_sizing.allocation_mode == "portfolio"
            and self.data_config.timeframe != "daily"
        ):
            errors.append(
                "Portfolio allocation mode requires daily timeframe. "
                "Intraday timeframes (5min, 15min, 1hour) are not supported "
                "in portfolio mode."
            )

        # VWAP timeframe validation: reject at parse time if daily
        timeframe = self.data_config.timeframe
        for ind in self.indicators:
            if ind.type.upper() in INTRADAY_ONLY_INDICATORS and timeframe == "daily":
                errors.append(
                    f"Indicator '{ind.type}' requires intraday data. "
                    f"Use timeframe '5min', '15min', or '1hour' instead of 'daily'."
                )

        errors.extend(self._session_anchor_errors())
        errors.extend(self._source_reference_errors())
        errors.extend(self._atr_indicator_errors())
        errors.extend(self._atr_risk_sizing_errors())
        if self.references_benchmark_close() and not self.universe.benchmark:
            errors.append(
                f"{BENCHMARK_CLOSE_COLUMN} is referenced but universe.benchmark is not set"
            )

        defined_ids = self._rule_reference_ids()
        errors.extend(_validate_rule_refs(self.entry_rules, defined_ids, "entry"))
        errors.extend(_validate_rule_refs(self.exit_rules, defined_ids, "exit"))
        return errors

    def _rule_reference_ids(self) -> set[str]:
        """Return every column name an entry or exit rule may name.

        Returns:
            Raw OHLCV columns, the reserved benchmark column when a benchmark
            is configured, every indicator id, and the suffixed column of every
            multi-output indicator.

        """
        defined_ids = set(RAW_PRICE_COLUMNS) | self._benchmark_columns()
        defined_ids.update(ind.id for ind in self.indicators)
        for ind in self.indicators:
            suffixes = MULTI_OUTPUT_SUFFIXES.get(ind.type.upper(), [])
            defined_ids.update(f"{ind.id}_{suffix}" for suffix in suffixes)
        return defined_ids

    def _atr_indicator_errors(self) -> list[str]:
        """Check the ATR reference names a declared ATR and that something reads it.

        Only this scope can settle it: the id has to resolve against the
        strategy's own indicator list, and NATR is excluded because it is a
        percent of close, not the price distance a stop needs.

        Returns:
            One error when the id names no declared ATR, and one when the id
            is set but no rule consumes it; empty when no ATR is referenced.

        """
        atr_id = self.risk_management.atr_indicator
        if atr_id is None:
            return []
        declared_atrs = {ind.id for ind in self.indicators if ind.type.upper() == "ATR"}
        errors: list[str] = []
        if atr_id not in declared_atrs:
            errors.append(f"atr_indicator '{atr_id}' must name a declared ATR indicator")
        if not self.risk_management.reads_atr():
            errors.append("atr_indicator is set but nothing uses it")
        return errors

    def _atr_risk_sizing_errors(self) -> list[str]:
        """Check ``atr_risk`` sizes to a stop this run would actually place.

        Only this scope can settle it: the budget lives on the sizing object
        and the stop distance on the risk object, and a budget measured
        against an unplaced stop is a number no trade would honour.

        Returns:
            One error when ``atr_risk`` has no ATR stop to size against;
            empty otherwise.

        """
        if self.position_sizing.method != "atr_risk":
            return []
        if self.risk_management.stop_atr_multiple is not None:
            return []
        return ["atr_risk sizes to the ATR stop; set risk_management.stop_atr_multiple"]

    def _session_anchor_errors(self) -> list[str]:
        """Check the params only this run's own window and timeframe can settle.

        An anchor date and an opening interval are valid or not against the
        data the run will fetch, which an indicator on its own cannot see.

        Returns:
            One error per anchor outside the data window and per opening
            interval that is not a whole number of bars.

        """
        errors: list[str] = []
        for ind in self.indicators:
            indicator_type = ind.type.upper()
            if indicator_type == "AVWAP":
                errors.extend(_avwap_anchor_errors(ind, self.data_config))
            elif indicator_type == "OPENING_RANGE":
                errors.extend(_opening_range_errors(ind, self.data_config.timeframe))
        return errors

    def references_benchmark_close(self) -> bool:
        """Report whether anything in this strategy reads the benchmark column.

        Aligning the benchmark onto every symbol frame costs a download and a
        join, so only a strategy that names the column pays for it.

        Returns:
            True if an indicator sources it, a dual-input indicator names it as
            its second series, or an entry or exit rule compares against it.

        """
        for ind in self.indicators:
            if BENCHMARK_CLOSE_COLUMN in (ind.source, ind.params.get("second_source")):
                return True
        operands = [
            operand
            for ruleset in (self.entry_rules, self.exit_rules)
            for condition in ruleset.conditions
            for operand in (condition.left, condition.right)
        ]
        return any(operand.indicator == BENCHMARK_CLOSE_COLUMN for operand in operands)

    def _benchmark_columns(self) -> set[str]:
        """Return the reserved benchmark column, when a benchmark is configured.

        Returns:
            The one-element set naming the column, or an empty set when the run
            has no benchmark and so nothing to align.

        """
        return {BENCHMARK_CLOSE_COLUMN} if self.universe.benchmark else set()

    def _source_reference_errors(self) -> list[str]:
        """Reject an indicator that reads a column not yet available to it.

        Indicators are computed in declaration order in a single pass, so a
        `source` naming an indicator declared later, itself, or nothing at all
        has no column to read. The engine warns and carries on; rejecting it
        here stops a strategy that is missing a rule from being backtested.

        Returns:
            One error per unresolvable source reference.

        """
        available = set(RAW_PRICE_COLUMNS) | self._benchmark_columns()
        errors: list[str] = []
        for ind in self.indicators:
            spec = INDICATOR_CATALOG.get(ind.type.upper())
            if spec is None:
                continue
            refs = spec.source_refs(ind.source, spec.resolve_params(ind.params))
            errors.extend(_undeclared_source_errors(ind.id, refs, available))
            available.add(ind.id)
            available.update(f"{ind.id}_{suffix}" for suffix in spec.outputs or ())
        return errors

    def cache_key(self) -> str:
        """Return deterministic JSON string for cache hashing."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON output."""
        return {
            "name": self.name,
            "universe": {
                "symbols": sorted(self.universe.symbols),
                "benchmark": self.universe.benchmark,
            },
            "data_config": {
                "start_date": self.data_config.start_date,
                "end_date": self.data_config.end_date,
                "train_end_date": self.data_config.train_end_date,
                "timeframe": self.data_config.timeframe,
            },
            "indicators": [
                {
                    "id": ind.id,
                    "type": ind.type,
                    "params": ind.params,
                    "source": ind.source,
                }
                for ind in self.indicators
            ],
            "entry_rules": _ruleset_to_dict(self.entry_rules),
            "exit_rules": _ruleset_to_dict(self.exit_rules),
            "position_sizing": {
                "method": self.position_sizing.method,
                "max_position_pct": self.position_sizing.max_position_pct,
                "max_positions": self.position_sizing.max_positions,
                "allocation_mode": self.position_sizing.allocation_mode,
                "risk_pct": self.position_sizing.risk_pct,
            },
            "risk_management": {
                "stop_loss_pct": self.risk_management.stop_loss_pct,
                "take_profit_pct": self.risk_management.take_profit_pct,
                "close_eod": self.risk_management.close_eod,
                "no_entry_after": self.risk_management.no_entry_after,
                "atr_indicator": self.risk_management.atr_indicator,
                "stop_atr_multiple": self.risk_management.stop_atr_multiple,
                "trailing_stop_pct": self.risk_management.trailing_stop_pct,
                "trailing_stop_atr_multiple": self.risk_management.trailing_stop_atr_multiple,
                "max_holding_bars": self.risk_management.max_holding_bars,
                "reentry_cooldown_bars": self.risk_management.reentry_cooldown_bars,
            },
            "execution_config": {
                "slippage_pct": self.execution_config.slippage_pct,
                "commission_pct": self.execution_config.commission_pct,
                "initial_capital": self.execution_config.initial_capital,
                "volume_scaled_slippage": self.execution_config.volume_scaled_slippage,
                "estimate_spread": self.execution_config.estimate_spread,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StrategyDefinition:
        """Parse a JSON-compatible dict into a StrategyDefinition."""
        universe_data = data.get("universe", {})
        data_config_data = data.get("data_config", {})

        indicators = [
            IndicatorConfig(
                id=ind["id"],
                type=ind["type"],
                params=ind.get("params", {}),
                source=ind.get("source", "close"),
            )
            for ind in data.get("indicators", [])
        ]

        entry_rules = _ruleset_from_dict(data.get("entry_rules", {}))
        exit_rules = _ruleset_from_dict(data.get("exit_rules", {}))

        sizing_data = data.get("position_sizing", {})
        risk_data = data.get("risk_management", {})
        exec_data = data.get("execution_config", {})

        strategy = cls(
            name=data.get("name", ""),
            universe=Universe(
                symbols=universe_data.get("symbols", []),
                benchmark=universe_data.get("benchmark", "SPY"),
            ),
            data_config=DataConfig(
                start_date=data_config_data.get("start_date", ""),
                end_date=data_config_data.get("end_date", ""),
                train_end_date=data_config_data.get("train_end_date"),
                timeframe=data_config_data.get("timeframe", "daily"),
            ),
            indicators=indicators,
            entry_rules=entry_rules,
            exit_rules=exit_rules,
            position_sizing=PositionSizing(
                method=sizing_data.get("method", "equal_weight"),
                max_position_pct=sizing_data.get("max_position_pct", 20.0),
                max_positions=sizing_data.get("max_positions", 5),
                allocation_mode=sizing_data.get("allocation_mode", "independent"),
                risk_pct=sizing_data.get("risk_pct"),
            ),
            risk_management=RiskManagement(
                stop_loss_pct=risk_data.get("stop_loss_pct"),
                take_profit_pct=risk_data.get("take_profit_pct"),
                close_eod=risk_data.get("close_eod", False),
                no_entry_after=risk_data.get("no_entry_after"),
                atr_indicator=risk_data.get("atr_indicator"),
                stop_atr_multiple=risk_data.get("stop_atr_multiple"),
                trailing_stop_pct=risk_data.get("trailing_stop_pct"),
                trailing_stop_atr_multiple=risk_data.get("trailing_stop_atr_multiple"),
                max_holding_bars=risk_data.get("max_holding_bars"),
                reentry_cooldown_bars=risk_data.get("reentry_cooldown_bars"),
            ),
            execution_config=ExecutionConfig(
                slippage_pct=exec_data.get("slippage_pct", 0.1),
                commission_pct=exec_data.get("commission_pct", 0.1),
                initial_capital=exec_data.get("initial_capital", 100_000.0),
                volume_scaled_slippage=exec_data.get("volume_scaled_slippage", False),
                estimate_spread=exec_data.get("estimate_spread", False),
            ),
        )

        errors = strategy.validate()
        if errors:
            msg = f"Invalid strategy: {'; '.join(errors)}"
            raise ValueError(msg)

        return strategy


def _ruleset_to_dict(ruleset: RuleSet) -> dict[str, Any]:
    """Serialize a RuleSet to dict."""
    return {
        "logic": ruleset.logic,
        "conditions": [
            {
                "left": _operand_to_dict(c.left),
                "operator": c.operator,
                "right": _operand_to_dict(c.right),
            }
            for c in ruleset.conditions
        ],
    }


def _operand_to_dict(operand: Operand) -> dict[str, Any]:
    """Serialize an Operand to dict."""
    if operand.indicator is not None:
        return {"indicator": operand.indicator}
    if operand.time_of_day is not None:
        return {"time_of_day": operand.time_of_day}
    if operand.time is not None:
        return {"time": operand.time}
    return {"constant": operand.constant}


def _ruleset_from_dict(data: dict[str, Any]) -> RuleSet:
    """Parse a dict into a RuleSet."""
    conditions = [
        Condition(
            left=_operand_from_dict(c.get("left", {})),
            operator=c.get("operator", ""),
            right=_operand_from_dict(c.get("right", {})),
        )
        for c in data.get("conditions", [])
    ]
    return RuleSet(logic=data.get("logic", "AND"), conditions=conditions)


def _operand_from_dict(data: dict[str, Any]) -> Operand:
    """Parse a dict into an Operand."""
    return Operand(
        indicator=data.get("indicator"),
        constant=data.get("constant"),
        time_of_day=data.get("time_of_day"),
        time=data.get("time"),
    )


def _avwap_anchor_errors(ind: IndicatorConfig, data_config: DataConfig) -> list[str]:
    """Reject an anchor the run's own window does not contain.

    No history is fetched before start_date beyond the indicator pre-roll, and
    the pre-roll is not planned from an anchor, so an earlier anchor would
    accumulate from whatever bar happened to arrive first. A walk-forward fold
    whose window excludes the anchor fails for the same reason: an anchor names
    one event, not a rolling reference.

    Args:
        ind: The AVWAP indicator config.
        data_config: The run's date range.

    Returns:
        One error when the anchor sits outside the window, else an empty list.
        A malformed anchor is left to the param check, which names it already.

    """
    anchor = parse_iso_date(ind.params.get("anchor_date"))
    start = parse_iso_date(data_config.start_date)
    end = parse_iso_date(data_config.end_date)
    if anchor is None or start is None or end is None or start <= anchor <= end:
        return []
    return [
        f"Indicator '{ind.id}' (AVWAP) anchor_date {anchor.isoformat()} must fall inside "
        f"the data window [{data_config.start_date}, {data_config.end_date}]; history "
        "before start_date is not fetched."
    ]


def _opening_range_errors(ind: IndicatorConfig, timeframe: str) -> list[str]:
    """Reject an opening interval that is not a whole number of bars.

    A 20-minute range on 15-minute bars can only ever be measured over one bar
    or two, so it silently becomes a 15- or 30-minute range instead.

    Args:
        ind: The OPENING_RANGE indicator config.
        timeframe: The run's bar timeframe.

    Returns:
        One error when the interval does not divide into bars, else an empty
        list. A non-integer interval, or a daily timeframe, is rejected by the
        param check and the intraday-only check respectively.

    """
    minutes = ind.params.get("minutes")
    bar_minutes = MINUTES_PER_BAR.get(timeframe)
    if type(minutes) is not int or bar_minutes is None or minutes % bar_minutes == 0:
        return []
    return [
        f"Indicator '{ind.id}' (OPENING_RANGE) minutes must be a multiple of the bar size "
        f"for timeframe '{timeframe}' ({bar_minutes} minutes); got {minutes}."
    ]


def _undeclared_source_errors(
    indicator_id: str,
    refs: tuple[str, ...],
    available: set[str],
) -> list[str]:
    """Name every source reference that is not available yet.

    Args:
        indicator_id: Id of the indicator doing the reading.
        refs: Column names the indicator reads.
        available: Raw columns plus the ids and suffixed columns declared above it.

    Returns:
        One error per reference missing from ``available``.

    """
    return [
        f"Indicator '{indicator_id}' sources '{ref}', which is not a raw column "
        "or an indicator declared before it."
        for ref in refs
        if ref not in available
    ]


def _validate_rule_refs(
    ruleset: RuleSet,
    defined_ids: set[str],
    label: str,
) -> list[str]:
    """Check that all indicator references in rules exist in defined_ids.

    Args:
        ruleset: Entry or exit rule set to check.
        defined_ids: Set of indicator IDs defined in the strategy.
        label: Human-readable label for error messages (e.g. "entry").

    Returns:
        List of error strings for undefined references.

    """
    errors: list[str] = []
    for cond in ruleset.conditions:
        for side, operand in [("left", cond.left), ("right", cond.right)]:
            if operand.indicator and operand.indicator not in defined_ids:
                errors.append(
                    f"{label} rule {side} references undefined indicator "
                    f"'{operand.indicator}'. Defined: {sorted(defined_ids)}"
                )
    return errors
