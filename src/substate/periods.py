"""Billing periods: fixed spans of days, or calendar months with an anchor."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from substate.errors import InvalidPeriod

DAYS_IN_THE_SHORTEST_MONTH = 28


class PeriodUnit(Enum):
    """What a period counts."""

    DAYS = "days"
    MONTHS = "months"


@dataclass(frozen=True)
class Period:
    """A billing period.

    Build one through `Period.days` or `Period.months` rather than by hand.
    """

    unit: PeriodUnit
    count: int

    def __post_init__(self) -> None:
        if self.count <= 0:
            raise InvalidPeriod(f"a period must be at least 1 {self.unit.value}, got {self.count}")

    @classmethod
    def days(cls, count: int) -> Period:
        """Exactly `count` days. No calendar involved."""
        return cls(PeriodUnit.DAYS, count)

    @classmethod
    def months(cls, count: int) -> Period:
        """Calendar months, landing on the anchor day when that day exists."""
        return cls(PeriodUnit.MONTHS, count)

    @property
    def min_days(self) -> int:
        """The shortest this period can ever be, in whole days.

        Grace is validated against this rather than against a nominal month:
        a plan that only breaks in February is a plan that breaks.
        """
        if self.unit is PeriodUnit.DAYS:
            return self.count
        return DAYS_IN_THE_SHORTEST_MONTH * self.count

    def next_boundary(self, start: datetime, anchor: int | None = None) -> datetime:
        """The end of one period that begins at `start`.

        `anchor` is the day of the month the subscription bills on. It is kept
        by the caller, never re-derived from the previous boundary, so a month
        too short to hold it clamps once instead of moving the billing day
        forever. Ignored by day periods, which have no calendar day to hold.
        """
        if start.tzinfo is None:
            raise ValueError("next_boundary needs an aware datetime, got a naive one")
        if anchor is not None and not 1 <= anchor <= 31:
            raise ValueError(f"anchor must be a day of the month, got {anchor}")

        if self.unit is PeriodUnit.DAYS:
            return start + timedelta(days=self.count)

        months = start.year * 12 + (start.month - 1) + self.count
        year, month = divmod(months, 12)
        month += 1
        day = min(anchor if anchor is not None else start.day, monthrange(year, month)[1])
        return start.replace(year=year, month=month, day=day)
