"""Tests for prompt loading and date-variable substitution."""

from datetime import datetime, timezone, tzinfo
from unittest.mock import patch

import pytest

from core_agents import prompt_loader

# A template that satisfies specialist validation and exposes the injected
# date variables so tests can assert on the substituted values.
_TEMPLATE = (
    "# Workflow: THINK\n\n"
    "Your expertise: testing date injection.\n\n"
    "# Output Guidelines\n\n"
    "TODAY_DATE=$TODAY_DATE\n"
    "TODAY_DATETIME=$TODAY_DATETIME\n"
    "CURRENT_YEAR=$CURRENT_YEAR\n" + ("filler line to exceed the minimum prompt length. " * 4)
)


class _FixedClock:
    """Stand-in for ``datetime`` returning a fixed UTC instant.

    ``now`` honors the requested timezone via ``astimezone`` so the code under
    test can convert the instant into any zone it chooses.
    """

    # 2026-01-15T02:00:00Z is 2026-01-14T21:00:00 in America/New_York (EST),
    # so the UTC calendar date (15th) and the Eastern date (14th) differ.
    _fixed = datetime(2026, 1, 15, 2, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return cls._fixed.astimezone(tz) if tz is not None else cls._fixed


def test_today_date_is_eastern(monkeypatch: pytest.MonkeyPatch) -> None:
    """TODAY_DATE must be the US Eastern market date, not the UTC date.

    At 2026-01-15T02:00:00Z it is still 2026-01-14 in New York, so a
    UTC-derived date would wrongly report the 15th.
    """
    monkeypatch.setattr(prompt_loader, "datetime", _FixedClock)

    with patch.object(prompt_loader, "get_prompt_from_opik", return_value=_TEMPLATE):
        prompt = prompt_loader.load_prompt("events_news")

    assert "TODAY_DATE=2026-01-14" in prompt
    assert "TODAY_DATE=2026-01-15" not in prompt
    # TODAY_DATETIME carries the Eastern offset (EST = -05:00 in January).
    assert "TODAY_DATETIME=2026-01-14T21:00:00-05:00" in prompt
    assert "CURRENT_YEAR=2026" in prompt
