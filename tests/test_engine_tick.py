"""tick(): the world catches up with the clock, and says what it crossed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from substate import (
    Event,
    FrozenClock,
    MemoryStorage,
    State,
    Subscription,
    SubscriptionEngine,
    SubscriptionEnteringGrace,
    SubscriptionExpired,
)

START = "2026-01-01"


def utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def engine(clock: FrozenClock, storage: MemoryStorage) -> SubscriptionEngine:
    return SubscriptionEngine(storage=storage, clock=clock)


def subscription(**overrides: object) -> Subscription:
    fields: dict[str, object] = {
        "user_id": "user_1",
        "plan_id": "pro",
        "state": State.ACTIVE,
        "expires_at": utc(2026, 1, 30),
    }
    fields.update(overrides)
    return Subscription(**fields)  # type: ignore[arg-type]


def names(events: list[Event]) -> list[str]:
    return [event.name for event in events]


async def test_a_quiet_world_produces_nothing() -> None:
    assert await engine(FrozenClock(START), MemoryStorage()).tick() == []


async def test_a_trial_nobody_paid_for_expires() -> None:
    clock, storage = FrozenClock(START), MemoryStorage()
    await storage.save_subscription(
        subscription(state=State.TRIAL, trial_ends_at=utc(2026, 1, 4), expires_at=None)
    )
    clock.advance(days=5)

    events = await engine(clock, storage).tick()

    assert names(events) == ["subscription.expired"]
    assert isinstance(events[0], SubscriptionExpired)
    assert events[0].reason == "trial_not_converted"


async def test_a_trial_never_passes_through_grace() -> None:
    """Grace is a courtesy to a paying customer, not a longer trial."""
    clock, storage = FrozenClock(START), MemoryStorage()
    await storage.save_subscription(
        subscription(
            state=State.TRIAL, trial_ends_at=utc(2026, 1, 4), expires_at=None, grace_days=5
        )
    )
    clock.advance(days=5)

    events = await engine(clock, storage).tick()

    assert names(events) == ["subscription.expired"]
    stored = await storage.get_subscription("user_1")
    assert stored is not None and stored.state is State.EXPIRED


async def test_an_unpaid_period_enters_grace() -> None:
    clock, storage = FrozenClock(START), MemoryStorage()
    await storage.save_subscription(subscription(grace_days=5))
    clock.advance(days=31)

    events = await engine(clock, storage).tick()

    assert names(events) == ["subscription.entering_grace"]
    assert isinstance(events[0], SubscriptionEnteringGrace)
    assert events[0].grace_ends_at == utc(2026, 2, 4)


async def test_a_plan_without_grace_expires_directly() -> None:
    clock, storage = FrozenClock(START), MemoryStorage()
    await storage.save_subscription(subscription(grace_days=0))
    clock.advance(days=31)

    events = await engine(clock, storage).tick()

    assert names(events) == ["subscription.expired"]
    assert isinstance(events[0], SubscriptionExpired)
    assert events[0].reason == "not_renewed"


async def test_grace_runs_out() -> None:
    clock, storage = FrozenClock(START), MemoryStorage()
    await storage.save_subscription(subscription(state=State.GRACE, grace_days=5))
    clock.advance(days=35)

    events = await engine(clock, storage).tick()

    assert names(events) == ["subscription.expired"]
    assert isinstance(events[0], SubscriptionExpired)
    assert events[0].reason == "grace_ended"


async def test_a_cancelled_subscription_expires_at_its_paid_boundary() -> None:
    clock, storage = FrozenClock(START), MemoryStorage()
    await storage.save_subscription(
        subscription(state=State.CANCELLED, cancelled_at=utc(2026, 1, 2), grace_days=5)
    )
    clock.advance(days=31)

    events = await engine(clock, storage).tick()

    assert names(events) == ["subscription.expired"]
    assert isinstance(events[0], SubscriptionExpired)
    assert events[0].reason == "cancelled"


async def test_a_cancelled_subscription_gets_no_grace() -> None:
    """Grace is for a period that might still be paid. This one will not be."""
    clock, storage = FrozenClock(START), MemoryStorage()
    await storage.save_subscription(subscription(state=State.CANCELLED, grace_days=5))
    clock.advance(days=31)

    await engine(clock, storage).tick()

    stored = await storage.get_subscription("user_1")
    assert stored is not None and stored.state is State.EXPIRED


async def test_an_expired_subscription_has_nothing_left_to_cross() -> None:
    clock, storage = FrozenClock(START), MemoryStorage()
    await storage.save_subscription(subscription(state=State.EXPIRED))
    clock.advance(days=400)

    assert await engine(clock, storage).tick() == []


async def test_one_tick_crosses_two_boundaries() -> None:
    """The spec's example: January to March in one call, two events, not one."""
    clock, storage = FrozenClock(START), MemoryStorage()
    await storage.save_subscription(subscription(expires_at=utc(2026, 1, 30), grace_days=5))
    clock.advance(days=59)  # 1 March

    events = await engine(clock, storage).tick()

    assert names(events) == ["subscription.entering_grace", "subscription.expired"]
    stored = await storage.get_subscription("user_1")
    assert stored is not None and stored.state is State.EXPIRED


async def test_occurred_at_is_the_boundary_not_the_call() -> None:
    """After a day of downtime the journal must still be orderable."""
    clock, storage = FrozenClock(START), MemoryStorage()
    await storage.save_subscription(subscription(expires_at=utc(2026, 1, 30), grace_days=5))
    clock.advance(days=59)

    events = await engine(clock, storage).tick()

    assert [event.occurred_at for event in events] == [utc(2026, 1, 30), utc(2026, 2, 4)]
    assert clock.now() == utc(2026, 3, 1)


async def test_a_boundary_exactly_now_counts_as_crossed() -> None:
    clock, storage = FrozenClock(START), MemoryStorage()
    await storage.save_subscription(subscription(expires_at=utc(2026, 1, 30), grace_days=0))
    clock.advance(days=29)

    assert clock.now() == utc(2026, 1, 30)
    assert names(await engine(clock, storage).tick()) == ["subscription.expired"]


async def test_a_second_tick_without_a_clock_that_moved_says_nothing() -> None:
    clock, storage = FrozenClock(START), MemoryStorage()
    await storage.save_subscription(subscription(grace_days=5))
    clock.advance(days=31)
    world = engine(clock, storage)

    first = await world.tick()
    second = await world.tick()

    assert names(first) == ["subscription.entering_grace"]
    assert second == []


async def test_events_of_several_subscriptions_are_ordered_by_time() -> None:
    clock, storage = FrozenClock(START), MemoryStorage()
    await storage.save_subscription(subscription(user_id="late", expires_at=utc(2026, 1, 20)))
    await storage.save_subscription(subscription(user_id="early", expires_at=utc(2026, 1, 10)))
    await storage.save_subscription(subscription(user_id="middle", expires_at=utc(2026, 1, 15)))
    clock.advance(days=30)

    events = await engine(clock, storage).tick()

    assert [event.user_id for event in events] == ["early", "middle", "late"]


async def test_a_paid_up_subscription_is_left_alone() -> None:
    clock, storage = FrozenClock(START), MemoryStorage()
    await storage.save_subscription(subscription(expires_at=utc(2026, 1, 30)))
    clock.advance(days=10)

    assert await engine(clock, storage).tick() == []
    stored = await storage.get_subscription("user_1")
    assert stored is not None and stored.state is State.ACTIVE


async def test_crossings_are_written_down() -> None:
    """A transition nobody saved is a transition that will happen again."""
    clock, storage = FrozenClock(START), MemoryStorage()
    await storage.save_subscription(subscription(grace_days=5))
    clock.advance(days=31)
    world = engine(clock, storage)

    await world.tick()

    stored = await storage.get_subscription("user_1")
    assert stored is not None and stored.state is State.GRACE


async def test_the_sink_sees_what_tick_returns() -> None:
    clock, storage = FrozenClock(START), MemoryStorage()
    seen: list[Event] = []
    await storage.save_subscription(subscription(grace_days=5))
    clock.advance(days=59)
    world = SubscriptionEngine(storage=storage, clock=clock, on_event=seen.append)

    returned = await world.tick()

    assert seen == returned


async def test_the_sink_is_optional() -> None:
    clock, storage = FrozenClock(START), MemoryStorage()
    await storage.save_subscription(subscription(grace_days=5))
    clock.advance(days=31)

    assert names(await engine(clock, storage).tick()) == ["subscription.entering_grace"]


class RecordingStorage(MemoryStorage):
    """A storage that remembers when it was written to."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def save_subscription(self, subscription: Subscription) -> None:
        self.calls.append("save")
        await super().save_subscription(subscription)


async def test_the_sink_runs_after_the_state_is_saved() -> None:
    """A notification that throws must not be able to undo a transition."""
    clock, storage = FrozenClock(START), RecordingStorage()
    await storage.save_subscription(subscription(grace_days=5))
    storage.calls.clear()
    clock.advance(days=31)
    world = SubscriptionEngine(
        storage=storage, clock=clock, on_event=lambda event: storage.calls.append(event.name)
    )

    await world.tick()

    assert storage.calls == ["save", "subscription.entering_grace"]


async def test_get_subscription_reads_without_advancing_anything() -> None:
    """Nothing happens by itself, including inside a getter."""
    clock, storage = FrozenClock(START), MemoryStorage()
    await storage.save_subscription(subscription(grace_days=5))
    clock.advance(days=59)

    stored = await engine(clock, storage).get_subscription("user_1")

    assert stored is not None and stored.state is State.ACTIVE


async def test_an_unknown_user_has_no_subscription() -> None:
    assert await engine(FrozenClock(START), MemoryStorage()).get_subscription("nobody") is None


async def test_the_default_clock_is_the_system_one() -> None:
    world = SubscriptionEngine(storage=MemoryStorage())

    assert await world.tick() == []


async def test_a_year_of_grace_and_expiry_replays_in_order() -> None:
    """The README promise: fast-forward a year, get a chain rather than a blur."""
    clock, storage = FrozenClock(START), MemoryStorage()
    for month in range(1, 13):
        await storage.save_subscription(
            subscription(user_id=f"user_{month:02d}", expires_at=utc(2026, month, 15), grace_days=3)
        )
    clock.advance(days=400)

    events = await engine(clock, storage).tick()

    assert len(events) == 24
    moments = [event.occurred_at for event in events]
    assert moments == sorted(moments)
    assert names(events)[:4] == [
        "subscription.entering_grace",
        "subscription.expired",
        "subscription.entering_grace",
        "subscription.expired",
    ]
    assert events[0].occurred_at == utc(2026, 1, 15)
    assert events[1].occurred_at == utc(2026, 1, 18)


async def test_a_subscription_moved_by_tick_keeps_its_expiry_in_the_past() -> None:
    """Grace is a debt: the period kept running, the boundary did not move."""
    clock, storage = FrozenClock(START), MemoryStorage()
    await storage.save_subscription(subscription(expires_at=utc(2026, 1, 30), grace_days=5))
    clock.advance(days=31)

    await engine(clock, storage).tick()

    stored = await storage.get_subscription("user_1")
    assert stored is not None
    assert stored.expires_at == utc(2026, 1, 30)
    assert stored.grace_ends_at == utc(2026, 2, 4)


async def test_a_late_tick_still_dates_the_events_correctly() -> None:
    clock, storage = FrozenClock(START), MemoryStorage()
    await storage.save_subscription(
        subscription(state=State.TRIAL, trial_ends_at=utc(2026, 1, 4), expires_at=None)
    )
    clock.advance(days=365)

    events = await engine(clock, storage).tick()

    assert events[0].occurred_at == utc(2026, 1, 4)
    assert clock.now() - events[0].occurred_at == timedelta(days=362)
