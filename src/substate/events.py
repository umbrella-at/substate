"""What the core reports. It never notifies anyone; that is the application's job.

Every event says who it is about and when it happened. `occurred_at` is the
moment the change logically happened — the boundary that was crossed — not the
moment `tick()` got around to noticing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Literal

from substate.models import PromoKind, State

ExpiryReason = Literal[
    "trial_not_converted",  # a trial nobody paid for
    "not_renewed",  # a paid period ended on a plan without grace
    "grace_ended",  # the courtesy ran out
    "cancelled",  # a cancelled subscription reached its paid boundary
]


@dataclass(frozen=True)
class Event:
    """Base of every event. `name` is the dotted label, shared by a whole kind."""

    name: ClassVar[str] = "event"

    user_id: str
    occurred_at: datetime


@dataclass(frozen=True)
class SubscriptionCreated(Event):
    """A cycle began: a fresh subscription, or a new one on an old record."""

    name: ClassVar[str] = "subscription.created"

    plan_id: str
    state: State


@dataclass(frozen=True)
class SubscriptionActivated(Event):
    """A paid period started after a trial, a grace period or an expiry."""

    name: ClassVar[str] = "subscription.activated"

    plan_id: str
    expires_at: datetime


@dataclass(frozen=True)
class SubscriptionRenewed(Event):
    """A paid period was extended by another one."""

    name: ClassVar[str] = "subscription.renewed"

    plan_id: str
    expires_at: datetime


@dataclass(frozen=True)
class SubscriptionEnteringGrace(Event):
    """The paid period ended unpaid, and access continues out of courtesy."""

    name: ClassVar[str] = "subscription.entering_grace"

    grace_ends_at: datetime


@dataclass(frozen=True)
class SubscriptionExpired(Event):
    """Access ended. The reason separates a trial that fizzled from a lapse."""

    name: ClassVar[str] = "subscription.expired"

    reason: ExpiryReason


@dataclass(frozen=True)
class SubscriptionCancelled(Event):
    """The user cancelled. Access runs to `access_until`, then it is over."""

    name: ClassVar[str] = "subscription.cancelled"

    access_until: datetime | None


@dataclass(frozen=True)
class SubscriptionPlanChanged(Event):
    """A plan change was recorded. `pending_plan_id` is None when one was undone."""

    name: ClassVar[str] = "subscription.plan_changed"

    plan_id: str
    pending_plan_id: str | None


@dataclass(frozen=True)
class PaymentRecorded(Event):
    """Money arrived and was written down, whatever it turned out to mean."""

    name: ClassVar[str] = "payment.recorded"

    provider: str
    external_id: str
    amount: int


@dataclass(frozen=True)
class PaymentDuplicate(Event):
    """This `(provider, external_id)` was already recorded. Nothing happened."""

    name: ClassVar[str] = "payment.duplicate"

    provider: str
    external_id: str


@dataclass(frozen=True)
class PaymentUnderpaid(Event):
    """Less than the price. The core records it and refuses to guess."""

    name: ClassVar[str] = "payment.underpaid"

    provider: str
    external_id: str
    amount: int
    expected: int


@dataclass(frozen=True)
class PaymentUnmatched(Event):
    """Money with no live subscription to apply it to."""

    name: ClassVar[str] = "payment.unmatched"

    provider: str
    external_id: str
    amount: int


@dataclass(frozen=True)
class PromoRedeemed(Event):
    """A promo code was claimed by this user and has been applied."""

    name: ClassVar[str] = "promo.redeemed"

    code: str
    kind: PromoKind


@dataclass(frozen=True)
class ReferralAccrued(Event):
    """A referrer earned their cut of a payment.

    `user_id` is the referrer, the one whose balance grew; the person who paid
    is `referred_user_id`.
    """

    name: ClassVar[str] = "referral.accrued"

    referred_user_id: str
    program_id: str
    amount: int
