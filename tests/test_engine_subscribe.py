"""subscribe(), plan registration and the access predicate that knows the clock."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from substate import (
    AlreadySubscribed,
    DuplicatePlan,
    Event,
    FrozenClock,
    InvalidPlan,
    MemoryStorage,
    Payment,
    Period,
    Plan,
    State,
    SubscriptionEngine,
    UnknownPlan,
)

START = "2026-01-01"


def utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def plan(plan_id: str = "pro", **overrides: object) -> Plan:
    fields: dict[str, object] = {
        "id": plan_id,
        "price": 29900,
        "currency": "RUB",
        "period": Period.days(30),
        "trial_days": 3,
        "grace_days": 5,
    }
    fields.update(overrides)
    return Plan(**fields)  # type: ignore[arg-type]


def world(
    clock: FrozenClock, storage: MemoryStorage | None = None, sink: list[Event] | None = None
) -> SubscriptionEngine:
    engine = SubscriptionEngine(
        storage=storage if storage is not None else MemoryStorage(),
        clock=clock,
        on_event=None if sink is None else sink.append,
    )
    engine.register_plan(plan())
    engine.register_plan(plan("lite", price=9900, trial_days=0, grace_days=0))
    return engine


async def test_a_plan_with_a_trial_starts_in_trial() -> None:
    clock = FrozenClock(START)
    engine = world(clock)

    sub = await engine.subscribe("user_1", "pro")

    assert sub.state is State.TRIAL
    assert sub.trial_ends_at == utc(2026, 1, 4)
    assert sub.expires_at is None
    assert sub.plan_id == "pro"


async def test_the_trial_is_stamped_when_it_is_granted() -> None:
    clock = FrozenClock(START)

    sub = await world(clock).subscribe("user_1", "pro")

    assert sub.trial_started_at == utc(2026, 1, 1)


async def test_the_plan_policy_is_snapshotted_onto_the_subscription() -> None:
    sub = await world(FrozenClock(START)).subscribe("user_1", "pro")

    assert sub.grace_days == 5


async def test_a_plan_without_a_trial_waits_for_the_first_payment() -> None:
    """No trial means no free access: expired from the start, owing a payment."""
    clock = FrozenClock(START)
    engine = world(clock)

    sub = await engine.subscribe("user_1", "lite")

    assert sub.state is State.EXPIRED
    assert sub.expires_at == utc(2026, 1, 1)
    assert await engine.is_active("user_1") is False


async def test_subscribing_announces_the_new_cycle() -> None:
    sink: list[Event] = []
    engine = world(FrozenClock(START), sink=sink)

    await engine.subscribe("user_1", "pro")

    assert [event.name for event in sink] == ["subscription.created"]
    assert sink[0].occurred_at == utc(2026, 1, 1)


async def test_the_subscription_is_saved() -> None:
    storage = MemoryStorage()
    engine = world(FrozenClock(START), storage)

    await engine.subscribe("user_1", "pro")

    stored = await storage.get_subscription("user_1")
    assert stored is not None and stored.state is State.TRIAL


async def test_an_unregistered_plan_is_refused() -> None:
    with pytest.raises(UnknownPlan):
        await world(FrozenClock(START)).subscribe("user_1", "enterprise")


async def test_a_plan_id_can_only_be_claimed_once() -> None:
    engine = world(FrozenClock(START))

    with pytest.raises(DuplicatePlan):
        engine.register_plan(plan("pro", price=34900))


async def test_a_grace_longer_than_the_period_never_reaches_the_engine() -> None:
    engine = world(FrozenClock(START))

    with pytest.raises(InvalidPlan):
        engine.register_plan(plan("broken", period=Period.days(30), grace_days=30))


@pytest.mark.parametrize("state", [State.TRIAL, State.ACTIVE, State.GRACE])
async def test_a_live_subscription_cannot_be_subscribed_again(state: State) -> None:
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, storage)
    await engine.subscribe("user_1", "pro")
    stored = await storage.get_subscription("user_1")
    assert stored is not None
    stored.state = state
    stored.expires_at = utc(2026, 2, 1)
    await storage.save_subscription(stored)

    with pytest.raises(AlreadySubscribed):
        await engine.subscribe("user_1", "pro")


async def test_an_expired_record_starts_a_new_cycle() -> None:
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, storage)
    await engine.subscribe("user_1", "pro")
    clock.advance(days=4)
    await engine.tick()

    sub = await engine.subscribe("user_1", "lite")

    assert sub.plan_id == "lite"
    assert sub.state is State.EXPIRED


async def test_a_record_that_never_had_a_trial_still_gets_one() -> None:
    """The other half of the restart rule: unused trials are not forfeited by a cycle."""
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "lite")  # no trial on this plan
    clock.advance(days=1)

    sub = await engine.subscribe("user_1", "pro")

    assert sub.state is State.TRIAL
    assert sub.trial_ends_at == utc(2026, 1, 5)
    assert sub.trial_started_at == utc(2026, 1, 2)


async def test_a_second_cycle_does_not_hand_out_a_second_trial() -> None:
    """Otherwise subscribe, wait, subscribe is free service forever."""
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    clock.advance(days=4)
    await engine.tick()

    sub = await engine.subscribe("user_1", "pro")

    assert sub.state is State.EXPIRED
    assert sub.expires_at == utc(2026, 1, 5)
    assert sub.trial_started_at == utc(2026, 1, 1)


async def test_a_new_cycle_clears_what_belonged_to_the_old_one() -> None:
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, storage)
    await engine.subscribe("user_1", "pro")
    stored = await storage.get_subscription("user_1")
    assert stored is not None
    stored.state = State.EXPIRED
    stored.promo_code = "WELCOME"
    stored.promo_periods_left = 3
    stored.pending_plan_id = "lite"
    stored.cancelled_at = utc(2026, 1, 2)
    stored.referrer_id = "user_42"
    await storage.save_subscription(stored)

    sub = await engine.subscribe("user_1", "pro")

    assert sub.promo_code is None
    assert sub.promo_periods_left is None
    assert sub.pending_plan_id is None
    assert sub.cancelled_at is None


async def test_a_new_cycle_keeps_the_referrer() -> None:
    """First to bring them in, first to be paid. Cycles do not reset that."""
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, storage)
    await engine.subscribe("user_1", "pro")
    stored = await storage.get_subscription("user_1")
    assert stored is not None
    stored.state = State.EXPIRED
    stored.referrer_id = "user_42"
    await storage.save_subscription(stored)

    sub = await engine.subscribe("user_1", "pro")

    assert sub.referrer_id == "user_42"


async def test_subscribe_catches_the_subscription_up_first() -> None:
    """A trial that ended forty days ago is expired, whether tick ran or not."""
    clock = FrozenClock(START)
    sink: list[Event] = []
    engine = world(clock, sink=sink)
    await engine.subscribe("user_1", "pro")
    clock.advance(days=40)
    sink.clear()

    sub = await engine.subscribe("user_1", "pro")

    assert sub.state is State.EXPIRED
    assert [event.name for event in sink] == ["subscription.expired", "subscription.created"]


async def test_the_catch_up_events_of_subscribe_are_dated_at_the_boundary() -> None:
    clock = FrozenClock(START)
    sink: list[Event] = []
    engine = world(clock, sink=sink)
    await engine.subscribe("user_1", "pro")
    clock.advance(days=40)
    sink.clear()

    await engine.subscribe("user_1", "pro")

    assert sink[0].occurred_at == utc(2026, 1, 4)
    assert sink[1].occurred_at == utc(2026, 2, 10)


async def test_access_during_a_trial() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")

    assert await engine.is_active("user_1") is True


async def test_access_ends_the_moment_the_trial_does_even_without_a_tick() -> None:
    """Otherwise a cancelled or lapsed subscription keeps access until the next cron run."""
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    clock.advance(days=3)

    assert clock.now() == utc(2026, 1, 4)
    assert await engine.is_active("user_1") is False


async def test_access_a_moment_before_the_boundary() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    clock.advance(days=3, microseconds=-1)

    assert await engine.is_active("user_1") is True


async def test_access_continues_through_a_grace_that_has_not_run_out() -> None:
    """Grace is access. The answer must not wait for the cron job to notice."""
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(
        Payment(provider="cryptobot", external_id="inv_1", user_id="user_1", amount=29900)
    )
    clock.advance(days=35)  # 5 February: the period ended on the 3rd, grace runs to the 8th

    assert await engine.is_active("user_1") is True


async def test_the_answer_does_not_change_when_tick_finally_runs() -> None:
    """Same clock, same answer, whether or not the world has been advanced yet."""
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(
        Payment(provider="cryptobot", external_id="inv_1", user_id="user_1", amount=29900)
    )
    clock.advance(days=35)

    before = await engine.is_active("user_1")
    await engine.tick()
    after = await engine.is_active("user_1")

    assert (before, after) == (True, True)


async def test_access_ends_with_the_grace_even_without_a_tick() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(
        Payment(provider="cryptobot", external_id="inv_1", user_id="user_1", amount=29900)
    )
    clock.advance(days=39)  # 9 February, one day past the grace

    assert await engine.is_active("user_1") is False


async def test_asking_about_access_emits_nothing() -> None:
    """It runs on every request. If it published, one stale trial would flood the journal."""
    clock = FrozenClock(START)
    storage = MemoryStorage()
    sink: list[Event] = []
    engine = world(clock, storage, sink)
    await engine.subscribe("user_1", "pro")
    clock.advance(days=40)
    sink.clear()

    for _ in range(5):
        assert await engine.is_active("user_1") is False

    assert sink == []
    stored = await storage.get_subscription("user_1")
    assert stored is not None and stored.state is State.TRIAL


async def test_asking_about_access_changes_nothing() -> None:
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, storage)
    await engine.subscribe("user_1", "pro")
    clock.advance(days=40)

    assert await engine.is_active("user_1") is False

    stored = await storage.get_subscription("user_1")
    assert stored is not None and stored.state is State.TRIAL


async def test_a_stranger_has_no_access() -> None:
    assert await world(FrozenClock(START)).is_active("nobody") is False
