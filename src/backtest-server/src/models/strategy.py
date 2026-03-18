"""Strategy definition models for structured backtest configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

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
}

SUPPORTED_OPERATORS: set[str] = {
    "greater_than",
    "less_than",
    "crosses_above",
    "crosses_below",
}

SUPPORTED_SIZING_METHODS: set[str] = {"equal_weight", "fixed_pct"}

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
    """Left or right side of a condition — indicator ref or constant."""

    indicator: str | None = None
    constant: float | None = None

    def validate(self) -> list[str]:
        """Validate operand has exactly one value set."""
        if self.indicator is None and self.constant is None:
            return ["Operand must have either 'indicator' or 'constant'"]
        if self.indicator is not None and self.constant is not None:
            return ["Operand cannot have both 'indicator' and 'constant'"]
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

    def validate(self) -> list[str]:
        """Validate position sizing."""
        errors: list[str] = []
        if self.method not in SUPPORTED_SIZING_METHODS:
            errors.append(
                f"Unsupported sizing method '{self.method}'. "
                f"Supported: {sorted(SUPPORTED_SIZING_METHODS)}"
            )
        return errors


@dataclass
class RiskManagement:
    """Risk management parameters."""

    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    trailing_stop_pct: float | None = None


_MIN_BACKTEST_DAYS = 30


@dataclass
class DataConfig:
    """Date range and train/test split configuration."""

    start_date: str
    end_date: str
    train_end_date: str | None = None

    def get_train_end(self) -> date:
        """Get train end date, defaulting to 75% of the range."""
        if self.train_end_date:
            return date.fromisoformat(self.train_end_date)
        start = date.fromisoformat(self.start_date)
        end = date.fromisoformat(self.end_date)
        split_days = int((end - start).days * 0.75)
        return start + timedelta(days=split_days)

    def validate(self) -> list[str]:
        """Validate date configuration."""
        errors: list[str] = []
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
        # Validate indicator references in rules.
        # Expand multi-output indicators so {id}_{suffix} refs are valid too.
        defined_ids = {ind.id for ind in self.indicators}
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
            },
            "risk_management": {
                "stop_loss_pct": self.risk_management.stop_loss_pct,
                "take_profit_pct": self.risk_management.take_profit_pct,
                "trailing_stop_pct": self.risk_management.trailing_stop_pct,
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
            ),
            indicators=indicators,
            entry_rules=entry_rules,
            exit_rules=exit_rules,
            position_sizing=PositionSizing(
                method=sizing_data.get("method", "equal_weight"),
                max_position_pct=sizing_data.get("max_position_pct", 20.0),
                max_positions=sizing_data.get("max_positions", 5),
            ),
            risk_management=RiskManagement(
                stop_loss_pct=risk_data.get("stop_loss_pct"),
                take_profit_pct=risk_data.get("take_profit_pct"),
                trailing_stop_pct=risk_data.get("trailing_stop_pct"),
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
