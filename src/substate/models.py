"""The domain: what a subscription is made of.

Everything that describes policy is frozen. `Subscription` is the one record
the engine moves through the state machine, so it is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from substate.errors import InvalidPlan, InvalidPromoCode
from substate.periods import Period

LAST_POSSIBLE_DAY_OF_MONTH = 31


class State(Enum):
    """Where a subscription stands. Access is `Subscription.is_active`, not a list."""

    TRIAL = "trial"
    ACTIVE = "active"
    GRACE = "grace"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class PromoKind(Enum):
    """What a promo code does to the next payment, or to the calendar."""

    PERCENT = "percent"
    FIXED = "fixed"
    PLUS_DAYS = "plus_days"


class ScopeKind(Enum):
    """How long a promo code keeps working."""

    FIRST_PAYMENT = "first_payment"
    N_PERIODS = "n_periods"
    FOREVER = "forever"


class Accrual(Enum):
    """Whether a referrer is paid once or on every renewal."""

    FIRST_PAYMENT_ONLY = "first_payment_only"
    EVERY_PAYMENT = "every_payment"


def _check_day_of_month(day: int) -> None:
    if not 1 <= day <= LAST_POSSIBLE_DAY_OF_MONTH:
        raise ValueError(f"a billing anchor must be a day of the month, got {day}")


@dataclass(frozen=True)
class Plan:
    """A tariff. Prices are integers in minor units, durations are whole days."""

    id: str
    price: int
    currency: str
    period: Period
    trial_days: int = 0
    grace_days: int = 0

    def __post_init__(self) -> None:
        for name, value in (
            ("price", self.price),
            ("trial_days", self.trial_days),
            ("grace_days", self.grace_days),
        ):
            if value < 0:
                raise InvalidPlan(f"{name} must not be negative, got {value}")
        if self.grace_days >= self.period.min_days:
            raise InvalidPlan(
                f"grace_days must be shorter than the period, got {self.grace_days} "
                f"against a period of at least {self.period.min_days} days"
            )


@dataclass(frozen=True)
class Payment:
    """Money that arrived. Identified by the pair the provider can repeat."""

    provider: str
    external_id: str
    user_id: str
    amount: int

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise ValueError(f"a payment must not be negative, got {self.amount}")


@dataclass(frozen=True)
class PromoScope:
    """How long a promo code applies. Build one through the factories."""

    kind: ScopeKind
    periods: int | None = None

    def __post_init__(self) -> None:
        if self.kind is ScopeKind.N_PERIODS:
            if self.periods is None or self.periods < 1:
                raise InvalidPromoCode(f"N_PERIODS needs a positive count, got {self.periods}")
        elif self.periods is not None:
            raise InvalidPromoCode(f"{self.kind.name} carries no period count")

    @classmethod
    def first_payment(cls) -> PromoScope:
        """Applies once, to the payment that follows redemption."""
        return cls(ScopeKind.FIRST_PAYMENT)

    @classmethod
    def n_periods(cls, count: int) -> PromoScope:
        """Applies to the next `count` payments."""
        return cls(ScopeKind.N_PERIODS, count)

    @classmethod
    def forever(cls) -> PromoScope:
        """Applies for as long as this subscription cycle lasts."""
        return cls(ScopeKind.FOREVER)


@dataclass(frozen=True)
class PromoCode:
    """A discount, read according to its kind.

    `value` is a percentage for `PERCENT`, minor units for `FIXED` and whole
    days for `PLUS_DAYS`. A `FIXED` code larger than the price is fine; it is
    clamped when it meets one.
    """

    code: str
    kind: PromoKind
    value: int
    applies_to: PromoScope = field(default_factory=PromoScope.first_payment)
    max_redemptions: int | None = None
    max_per_user: int = 1

    def __post_init__(self) -> None:
        if self.value < 0:
            raise InvalidPromoCode(f"a promo value must not be negative, got {self.value}")
        if self.kind is PromoKind.PERCENT and self.value > 100:
            raise InvalidPromoCode(f"a percent promo must be between 0 and 100, got {self.value}")


@dataclass(frozen=True)
class ReferralProgram:
    """The terms a referrer is paid on: one percentage, one repeat rule."""

    id: str
    percent: int
    accrual: Accrual

    def __post_init__(self) -> None:
        if not 0 <= self.percent <= 100:
            raise ValueError(f"percent must be between 0 and 100, got {self.percent}")


@dataclass
class Subscription:
    """One user's subscription, as the engine sees it.

    Policy is stored as a snapshot (`grace_days`, `billing_anchor_day`) rather
    than read back from the plan: editing a plan tomorrow must not rewrite what
    was promised today. Store the policy, compute the moment.
    """

    user_id: str
    plan_id: str
    state: State
    trial_ends_at: datetime | None = None
    expires_at: datetime | None = None
    grace_days: int = 0
    billing_anchor_day: int | None = None
    referrer_id: str | None = None
    pending_plan_id: str | None = None
    promo_code: str | None = None
    promo_periods_left: int | None = None
    cancelled_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.billing_anchor_day is not None:
            _check_day_of_month(self.billing_anchor_day)

    @property
    def grace_ends_at(self) -> datetime | None:
        """When the courtesy runs out. Computed, never stored."""
        if self.expires_at is None:
            return None
        return self.expires_at + timedelta(days=self.grace_days)

    @property
    def is_active(self) -> bool:
        """Whether the user has access, as one predicate.

        Cancelled keeps access until its paid period runs out; `tick()` is what
        turns that into `EXPIRED`.
        """
        return self.state is not State.EXPIRED

    @property
    def due_at(self) -> datetime | None:
        """The boundary the current state is waiting on, or None if there is none."""
        if self.state is State.TRIAL:
            return self.trial_ends_at
        if self.state is State.GRACE:
            return self.grace_ends_at
        if self.state in (State.ACTIVE, State.CANCELLED):
            return self.expires_at
        return None
