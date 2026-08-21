"""change_plan(): a new tariff, deferred to the end of the paid period."""

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
    SubscriptionEngine,
    SubscriptionPlanChanged,
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


def payment(**overrides: object) -> Payment:
    fields: dict[str, object] = {
        "provider": "cryptobot",
        "external_id": "inv_1",
        "user_id": "user_1",
        "amount": 29900,
    }
    fields.update(overrides)
    return Payment(**fields)  # type: ignore[arg-type]


def world(
    clock: FrozenClock, storage: MemoryStorage | None = None, sink: list[Event] | None = None
) -> SubscriptionEngine:
    engine = SubscriptionEngine(
        storage=storage if storage is not None else MemoryStorage(),
        clock=clock,
        on_event=None if sink is None else sink.append,
    )
    engine.register_plan(plan())
    engine.register_plan(
        plan("annual", price=99900, period=Period.days(365), trial_days=0, grace_days=0)
    )
    return engine


def names(events: list[Event]) -> list[str]:
    return [event.name for event in events]


async def test_a_plan_change_moves_no_date_and_no_money() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    clock.advance(days=10)

    sub = await engine.change_plan("user_1", "annual")

    assert sub.plan_id == "pro"
    assert sub.pending_plan_id == "annual"
    assert sub.expires_at == utc(2026, 2, 3)


async def test_the_change_is_announced_immediately() -> None:
    clock = FrozenClock(START)
    sink: list[Event] = []
    engine = world(clock, sink=sink)
    await engine.subscribe("user_1", "pro")
    sink.clear()

    await engine.change_plan("user_1", "annual")

    assert names(sink) == ["subscription.plan_changed"]
    changed = sink[0]
    assert isinstance(changed, SubscriptionPlanChanged)
    assert (changed.plan_id, changed.pending_plan_id) == ("pro", "annual")


async def test_the_new_plan_takes_effect_at_the_next_renewal() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    await engine.change_plan("user_1", "annual")

    await engine.apply_payment(payment(external_id="inv_2", amount=99900))

    sub = await engine.get_subscription("user_1")
    assert sub is not None
    assert sub.plan_id == "annual"
    assert sub.pending_plan_id is None
    assert sub.expires_at == utc(2027, 2, 3)


async def test_the_new_plan_brings_its_own_grace() -> None:
    """Policy is snapshotted per period, so the new period carries the new terms."""
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    await engine.change_plan("user_1", "annual")

    await engine.apply_payment(payment(external_id="inv_2", amount=99900))

    sub = await engine.get_subscription("user_1")
    assert sub is not None and sub.grace_days == 0


async def test_the_price_to_beat_is_the_new_plan() -> None:
    """Paying the old price for the new plan is an underpayment, not a renewal."""
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    await engine.change_plan("user_1", "annual")

    events = await engine.apply_payment(payment(external_id="inv_2", amount=29900))

    assert names(events) == ["payment.recorded", "payment.underpaid"]
    sub = await engine.get_subscription("user_1")
    assert sub is not None and sub.plan_id == "pro"


async def test_changing_on_the_last_day_of_the_paid_period() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    clock.advance(days=32)  # 2 February, expires_at is the 3rd

    sub = await engine.change_plan("user_1", "annual")

    assert sub.expires_at == utc(2026, 2, 3)
    assert sub.state is State.ACTIVE

    await engine.apply_payment(payment(external_id="inv_2", amount=99900))

    renewed = await engine.get_subscription("user_1")
    assert renewed is not None
    assert renewed.plan_id == "annual"
    assert renewed.expires_at == utc(2027, 2, 3)


async def test_changing_back_undoes_a_pending_change() -> None:
    """A deferred change to the plan you are already on is not a change, it is a cancel."""
    clock = FrozenClock(START)
    sink: list[Event] = []
    engine = world(clock, sink=sink)
    await engine.subscribe("user_1", "pro")
    await engine.change_plan("user_1", "annual")
    sink.clear()

    sub = await engine.change_plan("user_1", "pro")

    assert sub.pending_plan_id is None
    assert names(sink) == ["subscription.plan_changed"]
    changed = sink[0]
    assert isinstance(changed, SubscriptionPlanChanged)
    assert changed.pending_plan_id is None


async def test_changing_to_the_current_plan_with_nothing_pending_does_nothing() -> None:
    clock = FrozenClock(START)
    sink: list[Event] = []
    engine = world(clock, sink=sink)
    await engine.subscribe("user_1", "pro")
    sink.clear()

    sub = await engine.change_plan("user_1", "pro")

    assert sub.pending_plan_id is None
    assert sink == []


async def test_a_pending_change_can_be_replaced() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    engine.register_plan(plan("lite", price=9900, trial_days=0, grace_days=0))
    await engine.subscribe("user_1", "pro")
    await engine.change_plan("user_1", "annual")

    sub = await engine.change_plan("user_1", "lite")

    assert sub.pending_plan_id == "lite"


async def test_an_unregistered_plan_is_refused() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")

    with pytest.raises(UnknownPlan):
        await engine.change_plan("user_1", "enterprise")


async def test_changing_the_plan_of_a_stranger_is_an_error() -> None:
    with pytest.raises(NotSubscribed):
        await world(FrozenClock(START)).change_plan("nobody", "pro")


async def test_change_plan_catches_the_subscription_up_first() -> None:
    clock = FrozenClock(START)
    sink: list[Event] = []
    engine = world(clock, sink=sink)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    clock.advance(days=40)  # past the expiry and past the five day grace
    sink.clear()

    sub = await engine.change_plan("user_1", "annual")

    assert names(sink) == [
        "subscription.entering_grace",
        "subscription.expired",
        "subscription.plan_changed",
    ]
    assert sub.state is State.EXPIRED
    assert sub.pending_plan_id == "annual"


async def test_an_expired_subscription_can_be_switched_before_paying_again() -> None:
    """The change costs nothing now and applies to whatever period is bought next."""
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    clock.advance(days=4)
    await engine.tick()

    await engine.change_plan("user_1", "annual")
    await engine.apply_payment(payment(amount=99900))

    sub = await engine.get_subscription("user_1")
    assert sub is not None
    assert sub.plan_id == "annual"
    assert sub.expires_at == utc(2027, 1, 5)


async def test_a_cancelled_subscription_can_be_switched_too() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    await engine.cancel("user_1")

    sub = await engine.change_plan("user_1", "annual")

    assert sub.state is State.CANCELLED
    assert sub.pending_plan_id == "annual"
