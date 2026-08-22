"""cancel(): access until the paid boundary, then nothing."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from substate import (
    Event,
    FrozenClock,
    MemoryStorage,
    NotSubscribed,
    Payment,
    Period,
    Plan,
    State,
    SubscriptionCancelled,
    SubscriptionEngine,
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


def names(events: list[Event]) -> list[str]:
    return [event.name for event in events]


def payment(**overrides: object) -> Payment:
    fields: dict[str, object] = {
        "provider": "cryptobot",
        "external_id": "inv_1",
        "user_id": "user_1",
        "amount": 29900,
    }
    fields.update(overrides)
    return Payment(**fields)  # type: ignore[arg-type]


async def test_cancelling_a_paid_period_keeps_it_to_the_end() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())

    sub = await engine.cancel("user_1")

    assert sub.state is State.CANCELLED
    assert sub.cancelled_at == utc(2026, 1, 1)
    assert sub.expires_at == utc(2026, 2, 3)
    assert await engine.is_active("user_1") is True


async def test_cancelling_announces_when_access_runs_out() -> None:
    clock = FrozenClock(START)
    sink: list[Event] = []
    engine = world(clock, sink=sink)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    sink.clear()

    await engine.cancel("user_1")

    assert names(sink) == ["subscription.cancelled"]
    cancelled = sink[0]
    assert isinstance(cancelled, SubscriptionCancelled)
    assert cancelled.access_until == utc(2026, 2, 3)


async def test_a_cancelled_period_expires_on_time() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    await engine.cancel("user_1")
    clock.advance(days=33)

    events = await engine.tick()

    assert names(events) == ["subscription.expired"]
    assert events[0].occurred_at == utc(2026, 2, 3)


async def test_a_cancelled_subscription_gets_no_grace() -> None:
    """Grace is for a period that might still be paid for. This one will not be."""
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    await engine.cancel("user_1")
    clock.advance(days=33)
    await engine.tick()

    sub = await engine.get_subscription("user_1")
    assert sub is not None and sub.state is State.EXPIRED


async def test_cancelling_a_trial_serves_out_the_promised_days() -> None:
    """A trial has no expires_at, so cancelling writes the trial boundary into it."""
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")

    sub = await engine.cancel("user_1")

    assert sub.state is State.CANCELLED
    assert sub.expires_at == utc(2026, 1, 4)
    assert await engine.is_active("user_1") is True


async def test_a_cancelled_trial_still_expires_by_itself() -> None:
    """Without a boundary the record would sit in CANCELLED forever, unseen by tick."""
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.cancel("user_1")
    clock.advance(days=4)

    events = await engine.tick()

    assert names(events) == ["subscription.expired"]
    sub = await engine.get_subscription("user_1")
    assert sub is not None and sub.state is State.EXPIRED


async def test_cancelling_a_trial_keeps_it_on_record() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")

    sub = await engine.cancel("user_1")

    assert sub.trial_started_at == utc(2026, 1, 1)


async def test_cancelling_inside_grace_ends_the_courtesy() -> None:
    """Grace lasts while a payment might still arrive. Cancelling says it will not."""
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    clock.advance(days=35)
    await engine.tick()

    sub = await engine.cancel("user_1")

    assert sub.state is State.CANCELLED
    assert await engine.is_active("user_1") is False


async def test_an_expired_subscription_can_still_be_cancelled() -> None:
    """The table allows it: it stops the record ever renewing again by itself."""
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "lite")
    clock.advance(days=1)
    await engine.tick()

    sub = await engine.cancel("user_1")

    assert sub.state is State.CANCELLED


async def test_cancelling_twice_changes_nothing() -> None:
    """A double click on cancel, or a retried webhook, is not an error."""
    clock = FrozenClock(START)
    sink: list[Event] = []
    engine = world(clock, sink=sink)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    await engine.cancel("user_1")
    sink.clear()
    clock.advance(hours=1)

    sub = await engine.cancel("user_1")

    assert sink == []
    assert sub.cancelled_at == utc(2026, 1, 1)


async def test_cancelling_what_was_never_subscribed_is_an_error() -> None:
    with pytest.raises(NotSubscribed):
        await world(FrozenClock(START)).cancel("nobody")


async def test_cancel_catches_the_subscription_up_first() -> None:
    clock = FrozenClock(START)
    sink: list[Event] = []
    engine = world(clock, sink=sink)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    clock.advance(days=35)
    sink.clear()

    sub = await engine.cancel("user_1")

    assert names(sink) == ["subscription.entering_grace", "subscription.cancelled"]
    assert sub.state is State.CANCELLED


async def test_a_payment_for_a_cancelled_subscription_is_unmatched() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    await engine.cancel("user_1")

    events = await engine.apply_payment(payment(external_id="inv_2"))

    assert names(events) == ["payment.recorded", "payment.unmatched"]
    sub = await engine.get_subscription("user_1")
    assert sub is not None and sub.state is State.CANCELLED


async def test_a_cancelled_record_can_start_a_new_cycle() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    await engine.cancel("user_1")

    sub = await engine.subscribe("user_1", "lite")

    assert sub.state is State.EXPIRED
    assert sub.plan_id == "lite"
    assert sub.cancelled_at is None


async def test_cancelling_moves_no_money_and_no_boundary() -> None:
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, storage)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    before = await storage.get_subscription("user_1")
    assert before is not None

    after = await engine.cancel("user_1")

    assert after.expires_at == before.expires_at
    assert after.plan_id == before.plan_id
