"""Sessions, DTE, the slot clock and entry schedules (ADR 0002 §3, §17 item 14, design §10.1)."""

from datetime import date, timedelta

import pytest
from data_builders import MON, local_ns, trading_session

from options_backtest.data.records import TradingSession
from options_backtest.models.strategy import DailySchedule, MonthlySchedule, WeeklySchedule
from options_backtest.reference.calendars import (
    FILL_SLOTS,
    QUOTE_SLOTS,
    Slot,
    Slots,
    dte,
    next_session,
    scheduled,
    sessions_between,
    slot_instant,
    slot_times,
)

GOOD_FRIDAY = date(2024, 3, 29)


def table(first: date, last: date, holidays: tuple[date, ...] = ()) -> tuple[TradingSession, ...]:
    days = (first + timedelta(days=n) for n in range((last - first).days + 1))
    return tuple(trading_session(day) for day in days if day.weekday() < 5 and day not in holidays)


MARCH = table(date(2024, 2, 26), date(2024, 4, 5), (GOOD_FRIDAY,))
DAILY = DailySchedule.model_validate({"frequency": "daily"})


def weekly(weekday: int) -> WeeklySchedule:
    return WeeklySchedule.model_validate(
        {"frequency": "weekly", "weekday": weekday, "holiday_policy": "next_session_same_week"}
    )


def monthly(ordinal: int) -> MonthlySchedule:
    return MonthlySchedule.model_validate({"frequency": "monthly", "session_ordinal": ordinal})


def entry_dates(
    schedule: DailySchedule | WeeklySchedule | MonthlySchedule,
    sessions: tuple[TradingSession, ...] = MARCH,
) -> list[date]:
    return [s.session_date for s in sessions if scheduled(schedule, s, sessions)]


def test_slot_vocabularies() -> None:
    assert [slot.value for slot in Slot] == ["OPEN", "DEC", "F1", "F2", "F3", "CLOSE", "CUT"]
    assert FILL_SLOTS == (Slot.F1, Slot.F2, Slot.F3)
    assert QUOTE_SLOTS == (Slot.DEC, Slot.F1, Slot.F2, Slot.F3, Slot.CLOSE)


@pytest.mark.parametrize(("early", "close_hour"), [(False, 16), (True, 13)])
def test_slot_times_count_back_from_the_close(early: bool, close_hour: int) -> None:
    session = trading_session(MON, early_close=early)
    assert slot_times(session) == Slots(
        dec=local_ns(MON, close_hour - 1, 45),
        f1=local_ns(MON, close_hour - 1, 46),
        f2=local_ns(MON, close_hour - 1, 47),
        f3=local_ns(MON, close_hour - 1, 48),
        close=local_ns(MON, close_hour),
        cut=local_ns(MON, 23, 59, 59),
    )


def test_slot_instant_names_every_slot() -> None:
    session = trading_session(MON)
    slots = slot_times(session)
    assert [slot_instant(session, slot) for slot in Slot] == [
        session.open_ns,
        slots.dec,
        slots.f1,
        slots.f2,
        slots.f3,
        slots.close,
        slots.cut,
    ]


def test_slot_times_refuse_a_non_session() -> None:
    with pytest.raises(TypeError):
        slot_times(MON)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        slot_instant(trading_session(MON), "DEC")  # type: ignore[arg-type]


def test_sessions_between_is_inclusive_and_skips_holidays() -> None:
    dates = [s.session_date for s in sessions_between(MARCH, date(2024, 3, 27), GOOD_FRIDAY)]
    assert dates == [date(2024, 3, 27), date(2024, 3, 28)]
    assert sessions_between(MARCH, date(2024, 3, 5), date(2024, 3, 4)) == ()
    assert sessions_between(MARCH, date(2025, 1, 1), date(2025, 2, 1)) == ()


def test_next_session_is_strict() -> None:
    after_holiday = next_session(MARCH, date(2024, 3, 28))
    assert after_holiday is not None
    assert after_holiday.session_date == date(2024, 4, 1)
    from_holiday = next_session(MARCH, GOOD_FRIDAY)
    assert from_holiday is not None
    assert from_holiday.session_date == date(2024, 4, 1)
    assert next_session(MARCH, date(2024, 4, 5)) is None


@pytest.mark.parametrize(
    "sessions",
    [
        (trading_session(date(2024, 3, 5)), trading_session(MON)),
        (trading_session(MON), trading_session(MON)),
    ],
)
def test_session_functions_refuse_an_unsorted_table(sessions: tuple[TradingSession, ...]) -> None:
    with pytest.raises(ValueError, match="sorted"):
        sessions_between(sessions, MON, MON)
    with pytest.raises(ValueError, match="sorted"):
        next_session(sessions, MON)


def test_dte_counts_calendar_days() -> None:
    assert dte(MON, date(2024, 4, 19)) == 46
    assert dte(MON, MON) == 0
    assert dte(date(2024, 3, 5), MON) == -1
    with pytest.raises(TypeError):
        dte(MON, local_ns(MON, 16))  # type: ignore[arg-type]


def test_daily_schedules_every_session() -> None:
    assert entry_dates(DAILY) == [s.session_date for s in MARCH]


def test_weekly_moves_a_holiday_to_the_next_session_of_the_same_week() -> None:
    fridays = entry_dates(weekly(5))
    assert date(2024, 3, 22) in fridays
    assert date(2024, 3, 28) not in fridays
    assert date(2024, 4, 1) not in fridays
    assert fridays == [date(2024, 3, 1), date(2024, 3, 8), date(2024, 3, 15)] + [
        date(2024, 3, 22),
        date(2024, 4, 5),
    ]
    thursdays = entry_dates(weekly(4))
    assert date(2024, 3, 28) in thursdays
    mondays = entry_dates(weekly(1), table(MON, date(2024, 3, 15), (date(2024, 3, 11),)))
    assert mondays == [MON, date(2024, 3, 12)]


def test_weekly_groups_by_iso_week_across_a_year_end() -> None:
    sessions = table(date(2024, 12, 30), date(2025, 1, 10), (date(2024, 12, 30), date(2025, 1, 1)))
    assert entry_dates(weekly(1), sessions) == [date(2024, 12, 31), date(2025, 1, 6)]
    assert entry_dates(weekly(3), sessions) == [date(2025, 1, 2), date(2025, 1, 8)]


def test_monthly_counts_table_sessions_of_the_calendar_month() -> None:
    assert entry_dates(monthly(1)) == [date(2024, 2, 26), date(2024, 3, 1), date(2024, 4, 1)]
    assert entry_dates(monthly(3)) == [date(2024, 2, 28), date(2024, 3, 5), date(2024, 4, 3)]
    assert entry_dates(monthly(20)) == [date(2024, 3, 28)]
    mid_month = table(date(2024, 3, 13), date(2024, 3, 22))
    assert entry_dates(monthly(1), mid_month) == [date(2024, 3, 13)]


def test_scheduled_refuses_a_session_outside_the_table() -> None:
    with pytest.raises(ValueError, match="not in"):
        scheduled(DAILY, trading_session(GOOD_FRIDAY), MARCH)
    with pytest.raises(TypeError):
        scheduled({"frequency": "daily"}, MARCH[0], MARCH)  # type: ignore[arg-type]


def test_slot_times_refuse_a_session_shorter_than_the_decision_lead() -> None:
    session = trading_session(MON)
    short = TradingSession(
        MON, session.open_ns, session.open_ns + 15 * 60 * 10**9, session.cutoff_ns, True
    )
    with pytest.raises(ValueError, match="15 minutes"):
        slot_times(short)
