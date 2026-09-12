"""Known signal cases, including the backtest engine's equality-edge crossings."""

from typing import Any

import pytest

from lib.signals import evaluate_rules, evaluate_signals


def rules(operator: str = "crosses_above") -> dict[str, Any]:
    """Build a one-condition ruleset comparing rsi14 against 30."""
    return {
        "logic": "AND",
        "conditions": [
            {"left": {"indicator": "rsi14"}, "operator": operator, "right": {"constant": 30}}
        ],
    }


@pytest.mark.parametrize(
    ("operator", "previous", "current", "expected"),
    [
        ("crosses_above", 29, 30, True),
        ("crosses_above", 30, 31, False),
        ("crosses_below", 31, 30, True),
        ("crosses_below", 30, 29, False),
        ("less_than", 20, 29, True),
        ("less_than", 29, 30, False),
        ("greater_than", 29, 31, True),
        ("equals", 29, 30, True),
        ("not_equals", 29, 30, False),
    ],
)
def test_predicates(operator: str, previous: float, current: float, expected: bool) -> None:
    assert evaluate_rules(rules(operator), {"rsi14": previous}, {"rsi14": current}) is expected


@pytest.fixture()
def inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    """A frozen strategy and snapshot that mirror what obai-strategy emits."""
    strategy = {
        "universe": {"symbols": ["AAPL"]},
        "data_config": {"timeframe": "daily"},
        # The backtest indicator schema names the lookback `length`, not `period`.
        "indicators": [{"id": "rsi14", "type": "RSI", "params": {"length": 14}}],
        "entry_rules": rules(),
        "exit_rules": {"logic": "OR", "conditions": []},
    }
    snapshot = {
        "symbol": "AAPL",
        "timeframe": "daily",
        "price_basis": "adjusted",
        "warmup_complete": True,
        "previous": {"date": "2026-09-09", "complete": True, "values": {"rsi14": 29}},
        "current": {"date": "2026-09-10", "complete": True, "values": {"rsi14": 30}},
    }
    return strategy, snapshot


def evaluate(inputs: tuple[dict[str, Any], dict[str, Any]]) -> dict[str, str | bool]:
    """Run the helper with the calendar-derived date and validated basis."""
    return evaluate_signals(
        *inputs, expected_bar_date="2026-09-10", expected_price_basis="adjusted"
    )


def test_completed_snapshot_returns_predicates_without_order_decision(
    inputs: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    assert evaluate(inputs) == {
        "symbol": "AAPL",
        "signal_bar_date": "2026-09-10",
        "entry_signal": True,
        "exit_signal": False,
    }


def test_engine_normalized_indicator_type_is_accepted(
    inputs: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    """The engine uppercases `type` before validating, so a lowercase type backtested."""
    strategy, _ = inputs
    strategy["indicators"][0]["type"] = "rsi"
    assert evaluate(inputs)["entry_signal"] is True


# Each case must be rejected by the gate its name refers to, not by a
# neighbouring one, so every message is pinned separately.
_BAD_SNAPSHOTS: list[tuple[str, str]] = [
    ("not_expected_session", "expected completed session"),
    ("out_of_order", "out-of-order"),
    ("incomplete", "completed"),
    ("warmup", "warm-up"),
    ("basis", "Price basis"),
    ("missing", "Missing indicator value"),
    ("nan", "finite numbers"),
]

_BAD_STRATEGIES: list[tuple[str, str]] = [
    ("unsupported_type", "does not support"),
    ("unsupported_param", "does not support"),
    ("duplicate_id", "Duplicate indicator ID"),
    ("undeclared", "Undeclared indicator"),
    ("intraday", "daily-bar"),
    ("malformed_rules", "Unsupported ruleset"),
]


@pytest.mark.parametrize(("invalid", "message"), _BAD_SNAPSHOTS)
def test_unusable_snapshots_do_not_become_signals(
    inputs: tuple[dict[str, Any], dict[str, Any]],
    invalid: str,
    message: str,
) -> None:
    _, snapshot = inputs
    if invalid == "not_expected_session":
        # Ordering stays valid, so only the exchange-calendar gate can reject.
        snapshot["previous"]["date"] = "2026-09-08"
        snapshot["current"]["date"] = "2026-09-09"
    elif invalid == "out_of_order":
        snapshot["previous"]["date"] = "2026-09-10"
    elif invalid == "incomplete":
        snapshot["current"]["complete"] = False
    elif invalid == "warmup":
        snapshot["warmup_complete"] = False
    elif invalid == "basis":
        snapshot["price_basis"] = "raw"
    elif invalid == "missing":
        snapshot["previous"]["values"] = {}
    elif invalid == "nan":
        snapshot["current"]["values"]["rsi14"] = float("nan")
    with pytest.raises(ValueError, match=message):
        evaluate(inputs)


@pytest.mark.parametrize(("invalid", "message"), _BAD_STRATEGIES)
def test_unsupported_strategies_do_not_become_signals(
    inputs: tuple[dict[str, Any], dict[str, Any]],
    invalid: str,
    message: str,
) -> None:
    strategy, _ = inputs
    if invalid == "unsupported_type":
        strategy["indicators"][0]["type"] = "MACD"
    elif invalid == "unsupported_param":
        strategy["indicators"][0]["params"] = {"length": 14, "signal_length": 9}
    elif invalid == "duplicate_id":
        strategy["indicators"].append(dict(strategy["indicators"][0]))
    elif invalid == "undeclared":
        strategy["entry_rules"]["conditions"][0]["left"] = {"indicator": "guessed"}
    elif invalid == "intraday":
        strategy["data_config"]["timeframe"] = "1hour"
    elif invalid == "malformed_rules":
        strategy["exit_rules"] = {"conditions": []}
    with pytest.raises(ValueError, match=message):
        evaluate(inputs)


def test_or_condition_does_not_hide_missing_data() -> None:
    conditions = rules("greater_than")
    conditions["logic"] = "OR"
    conditions["conditions"].append(
        {"left": {"indicator": "missing"}, "operator": "greater_than", "right": {"constant": 0}}
    )
    with pytest.raises(ValueError, match="Missing"):
        evaluate_rules(conditions, {"rsi14": 40}, {"rsi14": 50})
