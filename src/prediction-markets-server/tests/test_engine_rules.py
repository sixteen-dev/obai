"""Tests for engine.rules — V1 schema validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.engine.rules import validate_rule


def _minimal_rule() -> dict[str, object]:
    return {
        "side": "YES",
        "entry": {"price_min": 0.01, "price_max": 0.15},
        "exit": {"type": "hold_to_resolution"},
    }


def test_validate_rule_accepts_minimal_valid_payload() -> None:
    """Bare-minimum YES + price band + hold_to_resolution should validate."""
    rule = validate_rule(_minimal_rule())
    assert rule.side == "YES"
    assert rule.entry.price_min == 0.01
    assert rule.exit.type == "hold_to_resolution"
    assert rule.filters.volume_filter_mode == "none"


def test_validate_rule_rejects_extra_top_level_field() -> None:
    """extra='forbid' on the top model means unknown fields fail loudly."""
    payload = _minimal_rule() | {"leverage": 2}
    with pytest.raises(ValidationError):
        validate_rule(payload)


def test_validate_rule_rejects_extra_filter_field() -> None:
    """Extra fields inside filters fail loudly too — no silent ignore."""
    payload = _minimal_rule()
    payload["filters"] = {"category": "politics", "min_volume": 1000}  # min_volume not valid
    with pytest.raises(ValidationError):
        validate_rule(payload)


def test_validate_rule_rejects_unsupported_side() -> None:
    """NO/BOTH/etc. should fail until V2 widens SUPPORTED_SIDES."""
    payload = _minimal_rule() | {"side": "NO"}
    with pytest.raises(ValidationError):
        validate_rule(payload)


def test_validate_rule_rejects_unsupported_exit_type() -> None:
    """Exit type discriminator must be one of the supported variants."""
    payload = _minimal_rule()
    payload["exit"] = {"type": "trailing_stop"}
    with pytest.raises(ValidationError):
        validate_rule(payload)


def test_validate_rule_accepts_stop_take_profit_stop_only() -> None:
    """Stop-only stop_take_profit with disjoint entry band validates."""
    payload = _minimal_rule()
    payload["entry"] = {"price_min": 0.10, "price_max": 0.20}
    payload["exit"] = {"type": "stop_take_profit", "stop_price": 0.05}
    rule = validate_rule(payload)
    assert rule.exit.type == "stop_take_profit"
    # narrow for type-checkers + assert payload fidelity
    assert rule.exit.stop_price == 0.05  # type: ignore[union-attr]
    assert rule.exit.take_profit_price is None  # type: ignore[union-attr]
    assert rule.exit.max_hold_days is None  # type: ignore[union-attr]


def test_validate_rule_accepts_stop_take_profit_all_triggers() -> None:
    """Stop + take-profit + max-hold combined, with disjoint entry band, validates."""
    payload = _minimal_rule()
    payload["entry"] = {"price_min": 0.10, "price_max": 0.20}
    payload["exit"] = {
        "type": "stop_take_profit",
        "stop_price": 0.05,
        "take_profit_price": 0.40,
        "max_hold_days": 14,
    }
    rule = validate_rule(payload)
    assert rule.exit.max_hold_days == 14  # type: ignore[union-attr]


def test_validate_rule_rejects_stop_take_profit_with_no_triggers() -> None:
    """stop_take_profit with all three triggers None has no semantics; must reject."""
    payload = _minimal_rule()
    payload["exit"] = {"type": "stop_take_profit"}
    with pytest.raises(ValidationError, match="at least one"):
        validate_rule(payload)


def test_validate_rule_rejects_stop_above_take_profit() -> None:
    """stop_price >= take_profit_price would make exit ordering undefined."""
    payload = _minimal_rule()
    payload["entry"] = {"price_min": 0.10, "price_max": 0.20}
    payload["exit"] = {
        "type": "stop_take_profit",
        "stop_price": 0.50,
        "take_profit_price": 0.40,
    }
    with pytest.raises(ValidationError, match="take_profit_price"):
        validate_rule(payload)


def test_validate_rule_rejects_stop_overlapping_entry_band() -> None:
    """stop_price within or above entry.price_min lets entry already satisfy stop."""
    payload = _minimal_rule()
    payload["entry"] = {"price_min": 0.10, "price_max": 0.20}
    payload["exit"] = {"type": "stop_take_profit", "stop_price": 0.10}
    with pytest.raises(ValidationError, match="stop_price"):
        validate_rule(payload)


def test_validate_rule_rejects_take_profit_overlapping_entry_band() -> None:
    """take_profit_price within or below entry.price_max lets entry already satisfy TP."""
    payload = _minimal_rule()
    payload["entry"] = {"price_min": 0.10, "price_max": 0.20}
    payload["exit"] = {"type": "stop_take_profit", "take_profit_price": 0.20}
    with pytest.raises(ValidationError, match="take_profit_price"):
        validate_rule(payload)


def test_validate_rule_rejects_max_hold_days_zero() -> None:
    """max_hold_days must be >= 1; 0 is meaningless."""
    payload = _minimal_rule()
    payload["entry"] = {"price_min": 0.10, "price_max": 0.20}
    payload["exit"] = {"type": "stop_take_profit", "max_hold_days": 0}
    with pytest.raises(ValidationError):
        validate_rule(payload)


def test_validate_rule_rejects_extra_field_in_stop_take_profit() -> None:
    """extra='forbid' on StopTakeProfitExit means unknown trigger fields fail loudly."""
    payload = _minimal_rule()
    payload["entry"] = {"price_min": 0.10, "price_max": 0.20}
    payload["exit"] = {
        "type": "stop_take_profit",
        "stop_price": 0.05,
        "trailing_stop": 0.02,
    }
    with pytest.raises(ValidationError):
        validate_rule(payload)


def test_validate_rule_rejects_inverted_price_band() -> None:
    """price_min > price_max is a programming error in the agent's rule construction."""
    payload = _minimal_rule()
    payload["entry"] = {"price_min": 0.5, "price_max": 0.1}
    with pytest.raises(ValidationError):
        validate_rule(payload)


def test_validate_rule_rejects_lifetime_volume_without_mode_flag() -> None:
    """min_lifetime_volume set + volume_filter_mode='none' is a contamination-hiding error."""
    payload = _minimal_rule()
    payload["filters"] = {"min_lifetime_volume": 1000, "volume_filter_mode": "none"}
    with pytest.raises(ValidationError, match="volume_filter_mode"):
        validate_rule(payload)


def test_validate_rule_accepts_lifetime_volume_with_mode_flag() -> None:
    """When the mode is acknowledged, min_lifetime_volume is fine."""
    payload = _minimal_rule()
    payload["filters"] = {"min_lifetime_volume": 1000, "volume_filter_mode": "lifetime_static"}
    rule = validate_rule(payload)
    assert rule.filters.min_lifetime_volume == 1000
    assert rule.filters.volume_filter_mode == "lifetime_static"


def test_validate_rule_rejects_inverted_ttr_filter() -> None:
    """min_days > max_days is a programming error in the rule."""
    payload = _minimal_rule()
    payload["filters"] = {"min_days_to_resolution": 30, "max_days_to_resolution": 7}
    with pytest.raises(ValidationError):
        validate_rule(payload)
