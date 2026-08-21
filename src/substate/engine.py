"""The state machine itself.

Nothing here happens on its own. Five calls and a clock move a subscription:
`subscribe`, `apply_payment`, `cancel`, `change_plan` and `tick`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from substate.clock import Clock, SystemClock
from substate.errors import AlreadySubscribed, DuplicatePlan, UnknownPlan
from substate.events import (
    Event,
    PaymentDuplicate,
    PaymentRecorded,
    PaymentUnderpaid,
    PaymentUnmatched,
    SubscriptionActivated,
    SubscriptionCreated,
    SubscriptionEnteringGrace,
    SubscriptionExpired,
    SubscriptionRenewed,
)
from substate.models import Payment, Plan, State, Subscription
from substate.storage import Storage

# A subscription can cross at most two boundaries (ACTIVE -> GRACE -> EXPIRED).
# The cap is a guard against a boundary calculation that never advances, not a
# real limit on how far the clock may jump.
MAX_CATCH_UP_TRANSITIONS = 8

# States a new cycle may be started on top of. Everything else is still live.
_RESTARTABLE = (State.EXPIRED, State.CANCELLED)


class SubscriptionEngine:
    """Subscription lifecycle over an injected clock and storage.

    The return types are deliberately uneven. `subscribe`, `cancel` and
    `change_plan` produce exactly one predictable event, and the caller already
    knows which, so they hand back the subscription. `apply_payment` and
    `tick` produce a chain nobody can predict, because both catch the world up
    with the clock first, so they hand back the events.

    `on_event` is optional and receives **every** event, including the ones the
    other calls return. That means events from `apply_payment` and `tick` reach
    two consumers: the caller, and the sink. Handle them in one place or you
    will act on each of them twice. The sink is called after the new state is
    saved, so a notification that fails cannot undo a transition; keep it cheap
    and put the real work on a queue.
    """

    def __init__(
        self,
        storage: Storage,
        clock: Clock | None = None,
        on_event: Callable[[Event], None] | None = None,
    ) -> None:
        self._storage = storage
        self._clock = clock if clock is not None else SystemClock()
        self._on_event = on_event
        self._plans: dict[str, Plan] = {}

    def register_plan(self, plan: Plan) -> None:
        """Add a plan. Ids are claimed once and for all.

        A plan cannot be replaced under a running engine: subscriptions read
        their price from it, and rewriting one under them would change terms
        that were already agreed. Changing a plan means a new engine.
        """
        if plan.id in self._plans:
            raise DuplicatePlan(f"a plan is already registered as {plan.id!r}")
        self._plans[plan.id] = plan

    async def subscribe(self, user_id: str, plan_id: str, promo: str | None = None) -> Subscription:
        """Start a cycle for this user and return the subscription.

        A plan with a trial starts in `TRIAL`. Without one the subscription
        starts expired with its boundary at now: no free access, waiting for
        the first payment.

        A user whose subscription is still live gets `AlreadySubscribed`. An
        expired or cancelled record starts a new cycle in place, keeping the
        referrer and the memory that a trial was already granted, and dropping
        everything that belonged to the previous cycle.

        `promo` is accepted and ignored until promo codes land, so that adding
        them does not change this signature.
        """
        plan = self._plan(plan_id)
        now = self._clock.now()
        subscription, events = await self._load_and_advance(user_id)

        if subscription is not None and subscription.state not in _RESTARTABLE:
            self._publish(events)
            raise AlreadySubscribed(f"{user_id!r} already has a live subscription")

        if subscription is None:
            subscription = Subscription(user_id=user_id, plan_id=plan_id, state=State.EXPIRED)
        self._begin_cycle(subscription, plan, now)

        await self._storage.save_subscription(subscription)
        events.append(SubscriptionCreated(user_id, now, plan_id=plan.id, state=subscription.state))
        self._publish(events)
        return subscription

    def _begin_cycle(self, subscription: Subscription, plan: Plan, now: datetime) -> None:
        """Reset a record onto a fresh cycle of `plan`. The referrer survives."""
        subscription.plan_id = plan.id
        subscription.grace_days = plan.grace_days
        subscription.billing_anchor_day = None
        subscription.pending_plan_id = None
        subscription.cancelled_at = None
        subscription.promo_code = None
        subscription.promo_periods_left = None

        if plan.trial_days > 0 and subscription.trial_started_at is None:
            subscription.state = State.TRIAL
            subscription.trial_started_at = now
            subscription.trial_ends_at = now + timedelta(days=plan.trial_days)
            subscription.expires_at = None
        else:
            subscription.state = State.EXPIRED
            subscription.trial_ends_at = None
            subscription.expires_at = now

    async def apply_payment(self, payment: Payment) -> list[Event]:
        """Record a payment and let it move the subscription. Returns what happened.

        Idempotent by `(provider, external_id)`: a pair already on file returns
        a single `payment.duplicate` and touches nothing, because a retried
        webhook is not a reason to advance the world. `tick()` is.

        Otherwise the payment is written down first, then the subscription is
        caught up with the clock, and only then does the money apply. Catching
        up first is what keeps a payment on a long-stale trial from renewing
        into the past.

        The amount is compared with the price of the plan that will govern the
        new period, which is the pending one if a plan change is waiting.
        """
        now = self._clock.now()
        if await self._storage.get_payment(payment.provider, payment.external_id) is not None:
            return self._returned(
                [
                    PaymentDuplicate(
                        payment.user_id,
                        now,
                        provider=payment.provider,
                        external_id=payment.external_id,
                    )
                ]
            )

        await self._storage.save_payment(payment)
        subscription, events = await self._load_and_advance(payment.user_id)
        events.append(
            PaymentRecorded(
                payment.user_id,
                now,
                provider=payment.provider,
                external_id=payment.external_id,
                amount=payment.amount,
            )
        )

        if subscription is None or subscription.state is State.CANCELLED:
            events.append(
                PaymentUnmatched(
                    payment.user_id,
                    now,
                    provider=payment.provider,
                    external_id=payment.external_id,
                    amount=payment.amount,
                )
            )
            return self._returned(events)

        plan = self._plan(subscription.pending_plan_id or subscription.plan_id)
        if payment.amount < plan.price:
            events.append(
                PaymentUnderpaid(
                    payment.user_id,
                    now,
                    provider=payment.provider,
                    external_id=payment.external_id,
                    amount=payment.amount,
                    expected=plan.price,
                )
            )
            return self._returned(events)

        events.append(self._start_period(subscription, plan, now))
        await self._storage.save_subscription(subscription)
        return self._returned(events)

    def _start_period(self, subscription: Subscription, plan: Plan, now: datetime) -> Event:
        """Move a paid-for subscription into its next period. Does not save.

        The billing anchor pins to the date the period is counted from, and
        only when a cycle starts. A renewal continues the cycle and leaves the
        anchor alone; an expired subscription paying again restarts it, so the
        anchor moves to the payment date. Without that, an anchor of the 15th
        and a payment on the 7th would buy eight days instead of a month.
        """
        previous = subscription.state
        anchor = subscription.billing_anchor_day
        # A subscription always carries the boundary its state is measured by;
        # the fallbacks keep a damaged record from turning a payment into a crash.
        if previous is State.TRIAL:
            base = subscription.trial_ends_at or now
            anchor = base.day
        elif previous is State.EXPIRED:
            base = now
            anchor = now.day
        else:
            base = subscription.expires_at or now

        subscription.plan_id = plan.id
        subscription.pending_plan_id = None
        subscription.grace_days = plan.grace_days
        subscription.billing_anchor_day = anchor
        subscription.cancelled_at = None
        subscription.state = State.ACTIVE
        subscription.expires_at = plan.period.next_boundary(base, anchor)

        if previous is State.ACTIVE:
            return SubscriptionRenewed(
                subscription.user_id, now, plan_id=plan.id, expires_at=subscription.expires_at
            )
        return SubscriptionActivated(
            subscription.user_id, now, plan_id=plan.id, expires_at=subscription.expires_at
        )

    async def is_active(self, user_id: str) -> bool:
        """Whether this user has access right now, by the engine's clock.

        `Subscription.is_active` answers by state alone and cannot know that a
        boundary passed four minutes ago. This one compares with the clock, so
        access ends on time rather than at the next `tick()`.
        """
        subscription = await self._storage.get_subscription(user_id)
        if subscription is None or not subscription.is_active:
            return False
        access_until = subscription.access_until
        return access_until is not None and self._clock.now() < access_until

    def _plan(self, plan_id: str) -> Plan:
        try:
            return self._plans[plan_id]
        except KeyError:
            raise UnknownPlan(f"no plan is registered as {plan_id!r}") from None

    async def _load_and_advance(self, user_id: str) -> tuple[Subscription | None, list[Event]]:
        """Read a subscription and bring it up to the clock before acting on it."""
        subscription = await self._storage.get_subscription(user_id)
        if subscription is None:
            return None, []
        events = self._advance(subscription, self._clock.now())
        if events:
            await self._storage.save_subscription(subscription)
        return subscription, events

    async def get_subscription(self, user_id: str) -> Subscription | None:
        """The stored subscription, exactly as written. This advances nothing."""
        return await self._storage.get_subscription(user_id)

    async def tick(self) -> list[Event]:
        """Advance every overdue subscription to the clock and report the crossings.

        One call may cross several boundaries per subscription: a period that
        ended in January and a grace that ran out in February are two events,
        not one. Events come back sorted by `occurred_at`, which is the moment
        of the boundary rather than the moment of this call.
        """
        now = self._clock.now()
        events: list[Event] = []
        async for subscription in self._storage.iter_due(now):
            events.extend(self._advance(subscription, now))
            await self._storage.save_subscription(subscription)
        events.sort(key=lambda event: event.occurred_at)
        self._publish(events)
        return events

    def _advance(self, subscription: Subscription, now: datetime) -> list[Event]:
        """Cross every boundary this subscription has already passed. Does not save."""
        events: list[Event] = []
        for _ in range(MAX_CATCH_UP_TRANSITIONS):
            boundary = subscription.due_at
            if boundary is None or boundary > now:
                break
            events.append(self._cross(subscription, boundary))
        return events

    def _cross(self, subscription: Subscription, boundary: datetime) -> Event:
        """Apply the one transition that `boundary` triggers, dated at the boundary."""
        user_id = subscription.user_id
        if subscription.state is State.TRIAL:
            subscription.state = State.EXPIRED
            return SubscriptionExpired(user_id, boundary, reason="trial_not_converted")
        if subscription.state is State.ACTIVE and subscription.grace_days > 0:
            subscription.state = State.GRACE
            grace_ends_at = boundary + timedelta(days=subscription.grace_days)
            return SubscriptionEnteringGrace(user_id, boundary, grace_ends_at=grace_ends_at)
        if subscription.state is State.ACTIVE:
            subscription.state = State.EXPIRED
            return SubscriptionExpired(user_id, boundary, reason="not_renewed")
        if subscription.state is State.GRACE:
            subscription.state = State.EXPIRED
            return SubscriptionExpired(user_id, boundary, reason="grace_ended")
        subscription.state = State.EXPIRED
        return SubscriptionExpired(user_id, boundary, reason="cancelled")

    def _returned(self, events: list[Event]) -> list[Event]:
        """Order a call's events by when they happened, publish them, hand them back."""
        events.sort(key=lambda event: event.occurred_at)
        self._publish(events)
        return events

    def _publish(self, events: list[Event]) -> None:
        if self._on_event is None:
            return
        for event in events:
            self._on_event(event)
