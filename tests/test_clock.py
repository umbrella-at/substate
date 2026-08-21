"""The clock is injected, so time is an argument rather than a global."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import substate
from substate import Clock, FrozenClock, SystemClock


def test_system_clock_returns_aware_utc() -> None:
    moment = SystemClock().now()

    assert moment.tzinfo is not None
    assert moment.utcoffset() == timedelta(0)


def test_system_clock_moves_forward() -> None:
    clock = SystemClock()

    first = clock.now()
    second = clock.now()

    assert second >= first


def test_frozen_clock_parses_a_date_only_string_as_midnight_utc() -> None:
    clock = FrozenClock("2026-01-01")

    assert clock.now() == datetime(2026, 1, 1, tzinfo=UTC)


def test_frozen_clock_parses_a_full_timestamp() -> None:
    clock = FrozenClock("2026-01-01T15:30:45")

    assert clock.now() == datetime(2026, 1, 1, 15, 30, 45, tzinfo=UTC)


def test_frozen_clock_converts_other_offsets_to_utc() -> None:
    clock = FrozenClock("2026-01-01T03:00:00+03:00")

    assert clock.now() == datetime(2026, 1, 1, tzinfo=UTC)
    assert clock.now().tzinfo is UTC


def test_frozen_clock_does_not_move_on_its_own() -> None:
    clock = FrozenClock("2026-01-01")

    assert clock.now() == clock.now()


def test_advance_moves_time_forward() -> None:
    clock = FrozenClock("2026-01-01")

    clock.advance(days=4)

    assert clock.now() == datetime(2026, 1, 5, tzinfo=UTC)


def test_advance_accepts_every_timedelta_keyword() -> None:
    clock = FrozenClock("2026-01-01")

    clock.advance(weeks=1, days=1, hours=2, minutes=3, seconds=4, milliseconds=5, microseconds=6)

    assert clock.now() == datetime(2026, 1, 9, 2, 3, 4, 5006, tzinfo=UTC)


def test_advance_accumulates_across_calls() -> None:
    clock = FrozenClock("2026-01-01")

    clock.advance(days=1)
    clock.advance(days=1)

    assert clock.now() == datetime(2026, 1, 3, tzinfo=UTC)


def test_advance_returns_the_new_moment() -> None:
    clock = FrozenClock("2026-01-01")

    assert clock.advance(hours=6) == datetime(2026, 1, 1, 6, tzinfo=UTC)


def test_advance_backwards_is_allowed() -> None:
    clock = FrozenClock("2026-01-10")

    clock.advance(days=-3)

    assert clock.now() == datetime(2026, 1, 7, tzinfo=UTC)


def test_frozen_clock_rejects_a_non_iso_string() -> None:
    with pytest.raises(ValueError):
        FrozenClock("the first of January")


def test_both_clocks_satisfy_the_protocol() -> None:
    clocks: list[Clock] = [SystemClock(), FrozenClock("2026-01-01")]

    for clock in clocks:
        assert isinstance(clock.now(), datetime)


def test_wall_clock_is_confined_to_the_system_clock() -> None:
    """The whole point of the package: no module reads the wall clock behind your back."""
    package = Path(substate.__file__).parent
    offenders = [
        path.name
        for path in sorted(package.rglob("*.py"))
        if path.name != "clock.py" and "datetime.now(" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_the_system_clock_is_the_one_place_that_reads_the_wall_clock() -> None:
    source = (Path(substate.__file__).parent / "clock.py").read_text(encoding="utf-8")

    assert source.count("datetime.now(") == 1


def test_a_frozen_clock_says_what_time_it_is_stuck_at() -> None:
    """A failing time test should not report an object address."""
    assert repr(FrozenClock("2026-01-01")) == "FrozenClock('2026-01-01T00:00:00+00:00')"
