"""Strategy definition models for structured backtest configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any


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
        }


SUPPORTED_INDICATORS: set[str] = {
    "SMA",
    "EMA",
    "WMA",
    "DEMA",
    "TEMA",
    "RSI",
    "MACD",
    "BBANDS",
    "ATR",
    "ADX",
    "STOCH",
    "STOCHRSI",
    "CCI",
    "WILLR",
    "MOM",
    "ROC",
    "OBV",
    "MFI",
    "AROON",
    "SAR",
    # Statistical indicators
    "LINEARREG",
    "LINEARREG_SLOPE",
    "LINEARREG_ANGLE",
    "STDDEV",
    "BETA",
    "CORREL",
    # VWAP (intraday-only)
    "VWAP",
    # Candlestick patterns — dynamically added below
}

# Batch-add all candlestick pattern names
_CDL_PATTERN_NAMES: list[str] = [
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
]
SUPPORTED_INDICATORS.update(_CDL_PATTERN_NAMES)

INTRADAY_ONLY_INDICATORS: set[str] = {"VWAP"}

# Raw OHLCV columns always present in DataFrames — valid as operand references
RAW_PRICE_COLUMNS: set[str] = {"open", "high", "low", "close", "volume"}

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

SUPPORTED_SIZING_METHODS: set[str] = {"equal_weight", "fixed_pct"}

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

# Multi-output indicators produce columns named {id}_{suffix}.
# Rules can reference either the bare id or the suffixed name.
MULTI_OUTPUT_SUFFIXES: dict[str, list[str]] = {
    "MACD": ["macd", "signal", "hist"],
    "BBANDS": ["upper", "middle", "lower"],
    "STOCH": ["slowk", "slowd"],
    "STOCHRSI": ["fastk", "fastd"],
    "AROON": ["down", "up"],
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
        if self.type.upper() not in SUPPORTED_INDICATORS:
            errors.append(
                f"Unsupported indicator '{self.type}'. Supported: {sorted(SUPPORTED_INDICATORS)}"
            )
        if not self.id:
            errors.append("Indicator id cannot be empty")
        return errors


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
        return errors


@dataclass
class RiskManagement:
    """Risk management parameters."""

    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    trailing_stop_pct: float | None = None
    close_eod: bool = False  # Phase 3.4: force close at session end
    no_entry_after: str | None = None  # Phase 3.4: "15:30" — no new entries after


_MIN_BACKTEST_DAYS = 30


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


@dataclass
class Universe:
    """Stock universe and benchmark configuration."""

    symbols: list[str] = field(default_factory=list)
    benchmark: str = "SPY"


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

    def validate(self) -> list[str]:
        """Validate entire strategy, return list of errors."""
        errors: list[str] = []
        if not self.name:
            errors.append("Strategy name cannot be empty")
        if not self.universe.symbols:
            errors.append("Universe must have at least one symbol")
        errors.extend(self.data_config.validate())
        for ind in self.indicators:
            errors.extend(ind.validate())
        errors.extend(self.entry_rules.validate())
        errors.extend(self.exit_rules.validate())
        errors.extend(self.position_sizing.validate())

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

        # Validate indicator references in rules.
        # Raw OHLCV columns + computed indicators + multi-output suffixes are valid.
        defined_ids = set(RAW_PRICE_COLUMNS)
        defined_ids.update(ind.id for ind in self.indicators)
        for ind in self.indicators:
            suffixes = MULTI_OUTPUT_SUFFIXES.get(ind.type.upper())
            if suffixes:
                for suffix in suffixes:
                    defined_ids.add(f"{ind.id}_{suffix}")
        errors.extend(_validate_rule_refs(self.entry_rules, defined_ids, "entry"))
        errors.extend(_validate_rule_refs(self.exit_rules, defined_ids, "exit"))
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
            },
            "risk_management": {
                "stop_loss_pct": self.risk_management.stop_loss_pct,
                "take_profit_pct": self.risk_management.take_profit_pct,
                "trailing_stop_pct": self.risk_management.trailing_stop_pct,
                "close_eod": self.risk_management.close_eod,
                "no_entry_after": self.risk_management.no_entry_after,
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
            ),
            risk_management=RiskManagement(
                stop_loss_pct=risk_data.get("stop_loss_pct"),
                take_profit_pct=risk_data.get("take_profit_pct"),
                trailing_stop_pct=risk_data.get("trailing_stop_pct"),
                close_eod=risk_data.get("close_eod", False),
                no_entry_after=risk_data.get("no_entry_after"),
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
