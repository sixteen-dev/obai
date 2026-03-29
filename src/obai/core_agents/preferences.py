"""Persistent user preferences stored at ~/.obai/preferences.json.

Preferences are cross-cutting (risk tolerance, investment horizon) and owned
by the agent system, not by any individual MCP server.  The Hub exposes
``get_preferences`` and ``set_preferences`` as local function tools so the
LLM can read/write them without routing to a specialist.
"""

from __future__ import annotations

import json
import logging
import tempfile
from enum import StrEnum
from pathlib import Path

from agents import function_tool
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

_OBAI_DIR = Path.home() / ".obai"
_DEFAULT_PATH = _OBAI_DIR / "preferences.json"


class RiskTolerance(StrEnum):
    """User's risk appetite level."""

    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


class InvestmentHorizon(StrEnum):
    """User's investment time horizon."""

    SHORT = "short"  # < 3 years
    MEDIUM = "medium"  # 3-10 years
    LONG = "long"  # > 10 years


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class UserPreferences(BaseModel, extra="forbid"):
    """Persistent user investment preferences."""

    risk_tolerance: RiskTolerance = RiskTolerance.MODERATE
    investment_horizon: InvestmentHorizon = InvestmentHorizon.MEDIUM
    default_benchmark: str = "SPY"
    initial_capital: float = 100_000.0
    currency: str = "USD"
    market: str = "US"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class PreferencesStore:
    """File-backed preferences store (~/.obai/preferences.json).

    Args:
        path: Override file path (use ``tmp_path`` in tests).
    """

    def __init__(self, path: Path = _DEFAULT_PATH) -> None:
        """Initialize store with file path."""
        self._path = path

    def load(self) -> UserPreferences:
        """Load preferences from disk, returning defaults on any error."""
        if not self._path.exists():
            return UserPreferences()
        try:
            raw = self._path.read_text(encoding="utf-8")
            if not raw.strip():
                return UserPreferences()
            return UserPreferences.model_validate_json(raw)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Corrupt preferences at %s — using defaults", self._path)
            return UserPreferences()

    def save(self, prefs: UserPreferences) -> None:
        """Atomically write preferences to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Write to temp file in same dir, then atomic rename
        tmp = Path(
            tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")[1],
        )
        try:
            tmp.write_text(prefs.model_dump_json(indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def reset(self) -> UserPreferences:
        """Delete file and return fresh defaults."""
        self._path.unlink(missing_ok=True)
        return UserPreferences()


# Module-level singleton
_store = PreferencesStore()


# ---------------------------------------------------------------------------
# Function tools (attached to the Hub agent)
# ---------------------------------------------------------------------------


@function_tool(strict_mode=False)
def get_preferences() -> str:
    """Get the user's saved investment preferences.

    Returns current risk tolerance, investment horizon, default benchmark,
    initial capital, currency, and market scope.  Preferences persist across
    sessions.

    Returns:
        JSON string of current preferences.
    """
    prefs = _store.load()
    return prefs.model_dump_json(indent=2)


@function_tool(strict_mode=False)
def set_preferences(
    risk_tolerance: str | None = None,
    investment_horizon: str | None = None,
    default_benchmark: str | None = None,
    initial_capital: float | None = None,
    currency: str | None = None,
    market: str | None = None,
    reset: bool = False,
) -> str:
    """Set or update the user's investment preferences.

    All parameters are optional.  Only provided fields are updated.
    Changes persist across sessions (saved to disk).

    Args:
        risk_tolerance: "conservative", "moderate", or "aggressive".
        investment_horizon: "short" (<3yr), "medium" (3-10yr), or "long" (>10yr).
        default_benchmark: Benchmark symbol (e.g. "SPY", "QQQ").
        initial_capital: Starting capital for backtests (e.g. 50000, 100000).
        currency: Currency code (e.g. "USD", "EUR").
        market: Default market scope (e.g. "US", "global").
        reset: If true, reset all preferences to defaults first.

    Returns:
        Confirmation message with updated preferences.
    """
    prefs = _store.reset() if reset else _store.load()

    # Validate and apply partial updates
    if risk_tolerance is not None:
        try:
            prefs.risk_tolerance = RiskTolerance(risk_tolerance.lower())
        except ValueError:
            opts = [r.value for r in RiskTolerance]
            return f"Invalid risk_tolerance: {risk_tolerance}. Valid options: {opts}"

    if investment_horizon is not None:
        try:
            prefs.investment_horizon = InvestmentHorizon(investment_horizon.lower())
        except ValueError:
            opts = [h.value for h in InvestmentHorizon]
            return f"Invalid investment_horizon: {investment_horizon}. Valid options: {opts}"

    if default_benchmark is not None:
        prefs.default_benchmark = default_benchmark.upper()

    if initial_capital is not None:
        if initial_capital <= 0:
            return f"Invalid initial_capital: {initial_capital}. Must be positive."
        prefs.initial_capital = initial_capital

    if currency is not None:
        prefs.currency = currency.upper()

    if market is not None:
        prefs.market = market.upper()

    _store.save(prefs)

    changed = [
        k
        for k, v in {
            "risk_tolerance": risk_tolerance,
            "investment_horizon": investment_horizon,
            "default_benchmark": default_benchmark,
            "initial_capital": initial_capital,
            "currency": currency,
            "market": market,
        }.items()
        if v is not None
    ]
    action = "Reset to defaults" if reset else "No changes"
    if changed:
        action = f"Updated: {', '.join(changed)}"
    elif reset:
        action = "Reset to defaults"

    return f"{action}\n{prefs.model_dump_json(indent=2)}"
