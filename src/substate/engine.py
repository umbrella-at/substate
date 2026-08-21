"""The state machine itself.

Nothing here happens on its own. Five calls and a clock move a subscription:
`subscribe`, `apply_payment`, `cancel`, `change_plan` and `tick`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from substate.clock import Clock, SystemClock
from substate.events import Event, SubscriptionEnteringGrace, SubscriptionExpired
from substate.models import State, Subscription
from substate.storage import Storage

# A subscription can cross at most two boundaries (ACTIVE -> GRACE -> EXPIRED).
# The cap is a guard against a boundary calculation that never advances, not a
# real limit on how far the clock may jump.
MAX_CATCH_UP_TRANSITIONS = 8


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

    def _publish(self, events: list[Event]) -> None:
        if self._on_event is None:
            return
        for event in events:
            self._on_event(event)
