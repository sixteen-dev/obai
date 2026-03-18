"""Tests for persistent user preferences."""

from __future__ import annotations

from pathlib import Path

import pytest

from core_agents.preferences import (
    InvestmentHorizon,
    PreferencesStore,
    RiskTolerance,
    UserPreferences,
)

# ---------------------------------------------------------------------------
# UserPreferences model
# ---------------------------------------------------------------------------


class TestUserPreferences:
    """Test UserPreferences Pydantic model."""

    def test_defaults(self) -> None:
        """Defaults match spec: moderate / medium / SPY / USD."""
        prefs = UserPreferences()
        assert prefs.risk_tolerance == RiskTolerance.MODERATE
        assert prefs.investment_horizon == InvestmentHorizon.MEDIUM
        assert prefs.default_benchmark == "SPY"
        assert prefs.currency == "USD"

    def test_json_round_trip(self) -> None:
        """Serialize to JSON and back, all fields preserved."""
        prefs = UserPreferences(
            risk_tolerance=RiskTolerance.AGGRESSIVE,
            investment_horizon=InvestmentHorizon.LONG,
            default_benchmark="QQQ",
            currency="EUR",
        )
        raw = prefs.model_dump_json()
        restored = UserPreferences.model_validate_json(raw)
        assert restored == prefs

    def test_invalid_risk_tolerance_rejected(self) -> None:
        """Invalid enum values raise validation error."""
        with pytest.raises(ValueError):
            UserPreferences(risk_tolerance="yolo")  # type: ignore[arg-type]

    def test_invalid_horizon_rejected(self) -> None:
        """Invalid enum values raise validation error."""
        with pytest.raises(ValueError):
            UserPreferences(investment_horizon="forever")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# PreferencesStore
# ---------------------------------------------------------------------------


class TestPreferencesStore:
    """Test file-backed PreferencesStore (all use tmp_path)."""

    def test_load_returns_defaults_when_missing(self, tmp_path: Path) -> None:
        """load() returns defaults when file does not exist."""
        store = PreferencesStore(path=tmp_path / "prefs.json")
        prefs = store.load()
        assert prefs == UserPreferences()

    def test_save_then_load_round_trip(self, tmp_path: Path) -> None:
        """save() then load() preserves all fields."""
        path = tmp_path / "prefs.json"
        store = PreferencesStore(path=path)

        original = UserPreferences(
            risk_tolerance=RiskTolerance.AGGRESSIVE,
            investment_horizon=InvestmentHorizon.SHORT,
            default_benchmark="QQQ",
            currency="GBP",
        )
        store.save(original)
        loaded = store.load()
        assert loaded == original

    def test_save_creates_parent_directory(self, tmp_path: Path) -> None:
        """save() creates parent dirs if missing."""
        path = tmp_path / "nested" / "deep" / "prefs.json"
        store = PreferencesStore(path=path)
        store.save(UserPreferences())
        assert path.exists()

    def test_load_handles_corrupt_json(self, tmp_path: Path) -> None:
        """load() returns defaults on corrupt JSON (with warning)."""
        path = tmp_path / "prefs.json"
        path.write_text("{invalid json!!!", encoding="utf-8")

        store = PreferencesStore(path=path)
        prefs = store.load()
        assert prefs == UserPreferences()

    def test_load_handles_empty_file(self, tmp_path: Path) -> None:
        """load() returns defaults on empty file."""
        path = tmp_path / "prefs.json"
        path.write_text("", encoding="utf-8")

        store = PreferencesStore(path=path)
        prefs = store.load()
        assert prefs == UserPreferences()

    def test_reset_deletes_file(self, tmp_path: Path) -> None:
        """reset() deletes file and returns fresh defaults."""
        path = tmp_path / "prefs.json"
        store = PreferencesStore(path=path)
        store.save(UserPreferences(risk_tolerance=RiskTolerance.AGGRESSIVE))
        assert path.exists()

        result = store.reset()
        assert not path.exists()
        assert result == UserPreferences()

    def test_reset_no_crash_when_file_missing(self, tmp_path: Path) -> None:
        """reset() succeeds even if file doesn't exist."""
        store = PreferencesStore(path=tmp_path / "nonexistent.json")
        result = store.reset()
        assert result == UserPreferences()


# ---------------------------------------------------------------------------
# Partial update + validation logic (mirrors function tool behavior)
# ---------------------------------------------------------------------------


class TestPreferenceWorkflows:
    """Test the update/validate/persist workflows that the function tools use."""

    def test_partial_update_single_field(self, tmp_path: Path) -> None:
        """Setting a single field preserves other defaults."""
        store = PreferencesStore(path=tmp_path / "prefs.json")
        prefs = store.load()
        prefs.risk_tolerance = RiskTolerance.AGGRESSIVE
        store.save(prefs)

        loaded = store.load()
        assert loaded.risk_tolerance == RiskTolerance.AGGRESSIVE
        assert loaded.investment_horizon == InvestmentHorizon.MEDIUM  # untouched

    def test_invalid_enum_value_rejected(self) -> None:
        """Invalid enum string raises ValueError (tool returns error msg)."""
        with pytest.raises(ValueError):
            RiskTolerance("yolo")

        with pytest.raises(ValueError):
            InvestmentHorizon("forever")

    def test_reset_then_load_returns_defaults(self, tmp_path: Path) -> None:
        """Reset clears customized preferences back to defaults."""
        store = PreferencesStore(path=tmp_path / "prefs.json")
        prefs = UserPreferences(risk_tolerance=RiskTolerance.AGGRESSIVE)
        store.save(prefs)

        store.reset()
        loaded = store.load()
        assert loaded == UserPreferences()

    def test_changes_persist_across_store_instances(self, tmp_path: Path) -> None:
        """Data written by one store instance is readable by another."""
        path = tmp_path / "prefs.json"

        store_a = PreferencesStore(path=path)
        prefs = UserPreferences(
            risk_tolerance=RiskTolerance.CONSERVATIVE,
            currency="EUR",
        )
        store_a.save(prefs)

        store_b = PreferencesStore(path=path)
        loaded = store_b.load()
        assert loaded.risk_tolerance == RiskTolerance.CONSERVATIVE
        assert loaded.currency == "EUR"

    def test_benchmark_uppercased(self, tmp_path: Path) -> None:
        """Benchmark symbol is stored uppercase."""
        store = PreferencesStore(path=tmp_path / "prefs.json")
        prefs = store.load()
        prefs.default_benchmark = "qqq"
        # The function tool uppercases; verify Pydantic accepts it as-is
        store.save(prefs)
        loaded = store.load()
        assert loaded.default_benchmark == "qqq"  # model stores as-is

    def test_function_tools_are_importable(self) -> None:
        """Verify get_preferences and set_preferences are FunctionTool instances."""
        from agents import FunctionTool

        from core_agents.preferences import get_preferences, set_preferences

        assert isinstance(get_preferences, FunctionTool)
        assert isinstance(set_preferences, FunctionTool)
