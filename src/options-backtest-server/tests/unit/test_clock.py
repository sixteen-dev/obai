"""Event ids, quote-age policies and the run's session plan (ADR 0002 §7, §17 items 3, 6, 29-31)."""

from datetime import date, datetime, timedelta

import pytest
from data_builders import trading_session

from options_backtest.engine.clock import (
    MAX_SETTLEMENT_SESSIONS,
    QUOTE_MAX_AGE_NS,
    SPOT_MAX_AGE_NS,
    Phase,
    RunCalendar,
    event_id,
    run_calendar,
)
from options_backtest.reference.calendars import Slot

MON = date(2024, 3, 4)
# Two weeks of weekday sessions: 2024-03-04 .. 2024-03-15.
TABLE = tuple(
    trading_session(MON + timedelta(days=offset))
    for offset in range(12)
    if (MON + timedelta(days=offset)).weekday() < 5  # noqa: PLR2004 — Saturday is weekday 5
)


def _dates(sessions: tuple[object, ...]) -> list[str]:
    return [session.session_date.isoformat() for session in sessions]  # type: ignore[attr-defined]


def test_policies_are_the_adr_values() -> None:
    assert QUOTE_MAX_AGE_NS == 120_000_000_000
    assert SPOT_MAX_AGE_NS == 60_000_000_000
    assert MAX_SETTLEMENT_SESSIONS == 5


def test_phases_are_numbered_as_design_10_2() -> None:
    assert [(phase.name, int(phase)) for phase in Phase] == [
        ("SETTLE_DUE", 1),
        ("PUBLISH", 2),
        ("MARK", 3),
        ("FILL", 4),
        ("DECIDE", 5),
        ("LIFECYCLE", 6),
        ("SNAPSHOT", 7),
    ]


@pytest.mark.parametrize(
    ("slot", "phase", "seq", "expected"),
    [
        (Slot.OPEN, Phase.SETTLE_DUE, 1, "2024-03-04:OPEN:1:1"),
        (Slot.DEC, Phase.PUBLISH, 1, "2024-03-04:DEC:2:1"),
        (Slot.DEC, Phase.MARK, 1, "2024-03-04:DEC:3:1"),
        (Slot.DEC, Phase.DECIDE, 1, "2024-03-04:DEC:5:1"),
        (Slot.F1, Phase.FILL, 1, "2024-03-04:F1:4:1"),
        (Slot.F2, Phase.FILL, 1, "2024-03-04:F2:4:1"),
        (Slot.F3, Phase.FILL, 3, "2024-03-04:F3:4:3"),
        (Slot.CLOSE, Phase.MARK, 2, "2024-03-04:CLOSE:3:2"),
        (Slot.CUT, Phase.LIFECYCLE, 1, "2024-03-04:CUT:6:1"),
        (Slot.CUT, Phase.SNAPSHOT, 1, "2024-03-04:CUT:7:1"),
    ],
)
def test_event_id_spells_session_slot_phase_and_seq(
    slot: Slot, phase: Phase, seq: int, expected: str
) -> None:
    assert event_id(MON, slot, phase, seq) == expected


@pytest.mark.parametrize("seq", [0, -1])
def test_event_id_seq_counts_from_one(seq: int) -> None:
    with pytest.raises(ValueError, match="seq"):
        event_id(MON, Slot.F1, Phase.FILL, seq)


@pytest.mark.parametrize(
    ("slot", "phase"),
    [
        (Slot.OPEN, Phase.FILL),
        (Slot.DEC, Phase.FILL),
        (Slot.F1, Phase.DECIDE),
        (Slot.CLOSE, Phase.SNAPSHOT),
        (Slot.CUT, Phase.MARK),
    ],
)
def test_event_id_rejects_a_phase_its_slot_does_not_run(slot: Slot, phase: Phase) -> None:
    with pytest.raises(ValueError, match="phase"):
        event_id(MON, slot, phase, 1)


def test_event_id_rejects_wrong_types() -> None:
    with pytest.raises(TypeError, match="seq"):
        event_id(MON, Slot.F1, Phase.FILL, True)  # noqa: FBT003 — a bool is not a count
    with pytest.raises(TypeError, match="session_date"):
        event_id(datetime(2024, 3, 4, 15, 46), Slot.F1, Phase.FILL, 1)
    with pytest.raises(TypeError, match="slot"):
        event_id(MON, "F1", Phase.FILL, 1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="phase"):
        event_id(MON, Slot.F1, 4, 1)  # type: ignore[arg-type]


def test_run_calendar_is_the_inclusive_window_and_five_settle_only_sessions() -> None:
    calendar = run_calendar(TABLE, MON, date(2024, 3, 6))

    assert isinstance(calendar, RunCalendar)
    assert _dates(calendar.window) == ["2024-03-04", "2024-03-05", "2024-03-06"]
    assert _dates(calendar.after) == [
        "2024-03-07",
        "2024-03-08",
        "2024-03-11",
        "2024-03-12",
        "2024-03-13",
    ]


def test_run_calendar_keeps_fewer_settle_only_sessions_when_the_table_ends() -> None:
    calendar = run_calendar(TABLE, date(2024, 3, 13), date(2024, 3, 14))

    assert _dates(calendar.window) == ["2024-03-13", "2024-03-14"]
    assert _dates(calendar.after) == ["2024-03-15"]


def test_a_one_session_window() -> None:
    calendar = run_calendar(TABLE, MON, MON)

    assert _dates(calendar.window) == ["2024-03-04"]
    assert len(calendar.after) == MAX_SETTLEMENT_SESSIONS


def test_run_calendar_needs_a_session_after_the_window_for_t_plus_1_dues() -> None:
    with pytest.raises(ValueError, match="after"):
        run_calendar(TABLE, MON, date(2024, 3, 15))


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2024, 3, 9), date(2024, 3, 12)),  # a Saturday start
        (MON, date(2024, 3, 10)),  # a Sunday end
        (date(2024, 3, 1), MON),  # before the table
    ],
)
def test_run_calendar_bounds_must_be_table_sessions(start: date, end: date) -> None:
    with pytest.raises(ValueError, match="table session"):
        run_calendar(TABLE, start, end)


def test_run_calendar_rejects_a_reversed_window() -> None:
    with pytest.raises(ValueError, match="start_date"):
        run_calendar(TABLE, date(2024, 3, 6), MON)


def test_run_calendar_rejects_an_unsorted_table() -> None:
    with pytest.raises(ValueError, match="sorted"):
        run_calendar(tuple(reversed(TABLE)), MON, date(2024, 3, 6))
