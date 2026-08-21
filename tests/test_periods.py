"""Period arithmetic, where calendars stop being obvious."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from substate import InvalidPeriod, Period, PeriodUnit


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def test_days_is_exactly_a_timedelta() -> None:
    start = utc(2026, 1, 1, 12, 30)

    assert Period.days(30).next_boundary(start) == start + timedelta(days=30)


def test_days_crosses_a_month_boundary_without_thinking_about_it() -> None:
    assert Period.days(7).next_boundary(utc(2026, 1, 28)) == utc(2026, 2, 4)


def test_days_crosses_a_leap_day() -> None:
    assert Period.days(1).next_boundary(utc(2028, 2, 28)) == utc(2028, 2, 29)


def test_days_crosses_a_year_boundary() -> None:
    assert Period.days(1).next_boundary(utc(2026, 12, 31)) == utc(2027, 1, 1)


def test_days_ignores_the_anchor() -> None:
    """The anchor is a calendar notion. A 30 day period does not have one."""
    assert Period.days(30).next_boundary(utc(2026, 1, 31), anchor=31) == utc(2026, 3, 2)


def test_days_keeps_the_time_of_day() -> None:
    assert Period.days(30).next_boundary(utc(2026, 1, 1, 23, 59)) == utc(2026, 1, 31, 23, 59)


def test_the_anchor_does_not_drift_after_a_short_month() -> None:
    """The chain from the spec: a naive implementation moves to the 28th forever."""
    period = Period.months(1)
    anchor = 31

    first = period.next_boundary(utc(2026, 1, 31), anchor)
    second = period.next_boundary(first, anchor)
    third = period.next_boundary(second, anchor)
    fourth = period.next_boundary(third, anchor)

    assert [first, second, third, fourth] == [
        utc(2026, 2, 28),
        utc(2026, 3, 31),
        utc(2026, 4, 30),
        utc(2026, 5, 31),
    ]


def test_anchor_29_lands_on_the_leap_day_in_2028() -> None:
    assert Period.months(1).next_boundary(utc(2028, 1, 29), anchor=29) == utc(2028, 2, 29)


def test_anchor_29_is_clamped_to_28_in_2027() -> None:
    assert Period.months(1).next_boundary(utc(2027, 1, 29), anchor=29) == utc(2027, 2, 28)


def test_anchor_29_returns_to_29_after_a_non_leap_february() -> None:
    period = Period.months(1)

    february = period.next_boundary(utc(2027, 1, 29), anchor=29)
    march = period.next_boundary(february, anchor=29)

    assert february == utc(2027, 2, 28)
    assert march == utc(2027, 3, 29)


def test_months_defaults_the_anchor_to_the_day_of_the_start() -> None:
    assert Period.months(1).next_boundary(utc(2026, 3, 15)) == utc(2026, 4, 15)


def test_months_rolls_over_the_year() -> None:
    assert Period.months(1).next_boundary(utc(2026, 12, 31), anchor=31) == utc(2027, 1, 31)


def test_several_months_at_once() -> None:
    assert Period.months(3).next_boundary(utc(2026, 11, 30), anchor=30) == utc(2027, 2, 28)


def test_twelve_months_land_on_the_same_day_next_year() -> None:
    assert Period.months(12).next_boundary(utc(2026, 1, 31), anchor=31) == utc(2027, 1, 31)


def test_months_keep_the_time_of_day() -> None:
    assert Period.months(1).next_boundary(utc(2026, 1, 31, 9, 15), anchor=31) == utc(
        2026, 2, 28, 9, 15
    )


def test_months_keep_the_timezone_of_the_start() -> None:
    tehran = timezone(timedelta(hours=3, minutes=30))
    start = datetime(2026, 1, 31, tzinfo=tehran)

    assert Period.months(1).next_boundary(start, anchor=31).tzinfo is tehran


def test_min_days_of_a_day_period_is_its_length() -> None:
    assert Period.days(30).min_days == 30


def test_min_days_of_a_month_is_the_shortest_february() -> None:
    """Guarding by the worst case is the point: February is what breaks grace."""
    assert Period.months(1).min_days == 28
    assert Period.months(3).min_days == 84


def test_the_factories_produce_the_units_they_promise() -> None:
    assert Period.days(30) == Period(PeriodUnit.DAYS, 30)
    assert Period.months(2) == Period(PeriodUnit.MONTHS, 2)


def test_periods_are_frozen() -> None:
    period = Period.days(30)

    with pytest.raises(AttributeError):
        period.count = 60  # type: ignore[misc]


def test_periods_are_comparable_and_hashable() -> None:
    assert Period.days(30) == Period.days(30)
    assert Period.days(30) != Period.months(1)
    assert len({Period.days(30), Period.days(30), Period.months(1)}) == 2


@pytest.mark.parametrize("count", [0, -1, -30])
def test_a_period_must_have_a_positive_length(count: int) -> None:
    with pytest.raises(InvalidPeriod):
        Period.days(count)

    with pytest.raises(InvalidPeriod):
        Period.months(count)


def test_an_invalid_period_is_also_a_value_error() -> None:
    with pytest.raises(ValueError):
        Period.months(0)


@pytest.mark.parametrize("anchor", [0, 32, -1])
def test_the_anchor_must_be_a_day_of_the_month(anchor: int) -> None:
    with pytest.raises(ValueError):
        Period.months(1).next_boundary(utc(2026, 1, 15), anchor=anchor)


def test_next_boundary_rejects_a_naive_datetime() -> None:
    with pytest.raises(ValueError):
        Period.days(30).next_boundary(datetime(2026, 1, 1))
