"""Time as a dependency.

Everything in the package asks a `Clock` what time it is. That is what makes a
year of subscription history a millisecond of test runtime.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Source of the current moment, always aware and in UTC."""

    def now(self) -> datetime: ...


class SystemClock:
    """The wall clock. The single place in the package that reads real time."""

    __slots__ = ()

    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """A clock that stands still until you move it.

    Accepts any ISO 8601 string. A naive string is read as UTC, an offset-aware
    one is converted to UTC, so `now()` is always comparable with everything else.
    """

    __slots__ = ("_moment",)

    def __init__(self, moment: str) -> None:
        parsed = datetime.fromisoformat(moment)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        self._moment = parsed.astimezone(UTC)

    def now(self) -> datetime:
        return self._moment

    def advance(self, **delta: float) -> datetime:
        """Move the clock by any keyword `timedelta` accepts and return the new moment."""
        self._moment += timedelta(**delta)
        return self._moment

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._moment.isoformat()!r})"
