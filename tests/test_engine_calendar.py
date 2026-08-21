"""Calendar months through the engine: the anchor, and a year in milliseconds."""

from __future__ import annotations

from datetime import UTC, datetime

from substate import (
    Event,
    FrozenClock,
    MemoryStorage,
    Payment,
    Period,
    Plan,
    State,
    SubscriptionEngine,
)


def utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def monthly(plan_id: str = "monthly", **overrides: object) -> Plan:
    fields: dict[str, object] = {
        "id": plan_id,
        "price": 29900,
        "currency": "RUB",
        "period": Period.months(1),
        "trial_days": 0,
        "grace_days": 0,
    }
    fields.update(overrides)
    return Plan(**fields)  # type: ignore[arg-type]


def world(clock: FrozenClock, *plans: Plan) -> SubscriptionEngine:
    engine = SubscriptionEngine(storage=MemoryStorage(), clock=clock)
    for plan in plans or (monthly(),):
        engine.register_plan(plan)
    return engine


def payment(number: int, amount: int = 29900) -> Payment:
    return Payment(
        provider="cryptobot", external_id=f"inv_{number}", user_id="user_1", amount=amount
    )


async def pay(engine: SubscriptionEngine, number: int, amount: int = 29900) -> datetime:
    await engine.apply_payment(payment(number, amount))
    sub = await engine.get_subscription("user_1")
    assert sub is not None and sub.expires_at is not None
    return sub.expires_at


async def test_the_billing_day_does_not_drift_after_february() -> None:
    """The chain from the spec, driven through payments rather than by hand."""
    clock = FrozenClock("2026-01-31")
    engine = world(clock)
    await engine.subscribe("user_1", "monthly")

    boundaries = []
    for number in range(1, 6):
        boundaries.append(await pay(engine, number))
        clock.advance(days=1)

    assert boundaries == [
        utc(2026, 2, 28),
        utc(2026, 3, 31),
        utc(2026, 4, 30),
        utc(2026, 5, 31),
        utc(2026, 6, 30),
    ]


async def test_the_anchor_is_kept_on_the_subscription() -> None:
    clock = FrozenClock("2026-01-31")
    engine = world(clock)
    await engine.subscribe("user_1", "monthly")

    await pay(engine, 1)

    sub = await engine.get_subscription("user_1")
    assert sub is not None and sub.billing_anchor_day == 31


async def test_an_anchor_of_29_finds_the_leap_day() -> None:
    clock = FrozenClock("2028-01-29")
    engine = world(clock)
    await engine.subscribe("user_1", "monthly")

    assert await pay(engine, 1) == utc(2028, 2, 29)


async def test_an_anchor_of_29_clamps_in_an_ordinary_year() -> None:
    clock = FrozenClock("2027-01-29")
    engine = world(clock)
    await engine.subscribe("user_1", "monthly")

    assert await pay(engine, 1) == utc(2027, 2, 28)
    clock.advance(days=1)
    assert await pay(engine, 2) == utc(2027, 3, 29)


async def test_a_trial_pins_the_anchor_to_the_day_the_trial_ends() -> None:
    """Three days of trial must not cost three days of the first paid month."""
    clock = FrozenClock("2026-01-01")
    engine = world(clock, monthly(trial_days=3))
    await engine.subscribe("user_1", "monthly")

    assert await pay(engine, 1) == utc(2026, 2, 4)

    sub = await engine.get_subscription("user_1")
    assert sub is not None and sub.billing_anchor_day == 4


async def test_paying_after_a_lapse_moves_the_anchor_to_the_payment() -> None:
    """An anchor of the 15th and a payment on the 7th must still buy a month."""
    clock = FrozenClock("2026-01-15")
    engine = world(clock)
    await engine.subscribe("user_1", "monthly")
    await pay(engine, 1)
    clock.advance(days=82)  # 7 April, long past the 15 February boundary
    await engine.tick()

    assert await pay(engine, 2) == utc(2026, 5, 7)

    sub = await engine.get_subscription("user_1")
    assert sub is not None and sub.billing_anchor_day == 7


async def test_paying_inside_grace_leaves_the_anchor_alone() -> None:
    """Grace continues the cycle, so the billing day it was bought on stays."""
    clock = FrozenClock("2026-01-31")
    engine = world(clock, monthly(grace_days=5))
    await engine.subscribe("user_1", "monthly")
    await pay(engine, 1)  # 28 February
    clock.advance(days=30)  # 2 March, inside the grace
    await engine.tick()
    grace = await engine.get_subscription("user_1")
    assert grace is not None and grace.state is State.GRACE

    assert await pay(engine, 2) == utc(2026, 3, 31)

    sub = await engine.get_subscription("user_1")
    assert sub is not None and sub.billing_anchor_day == 31


async def test_a_year_of_renewals_never_drifts() -> None:
    clock = FrozenClock("2026-01-31")
    engine = world(clock)
    await engine.subscribe("user_1", "monthly")

    boundaries = []
    for number in range(1, 13):
        boundaries.append(await pay(engine, number))
        clock.advance(days=1)

    assert boundaries[-1] == utc(2027, 1, 31)
    assert [boundary.day for boundary in boundaries] == [
        28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
        31,
    ]


async def test_a_year_of_one_subscription_replays_as_a_chain() -> None:
    """The README promise, end to end: pay for a while, stop, watch it lapse."""
    clock = FrozenClock("2026-01-01")
    sink: list[Event] = []
    engine = SubscriptionEngine(
        storage=MemoryStorage(),
        clock=clock,
        on_event=sink.append,
    )
    engine.register_plan(monthly(trial_days=3, grace_days=5))
    await engine.subscribe("user_1", "monthly")

    for number in range(1, 4):
        await engine.apply_payment(payment(number))
        clock.advance(days=32)
        await engine.tick()

    clock.advance(days=365)
    await engine.tick()

    assert [event.name for event in sink] == [
        "subscription.created",  # 1 January, three days of trial
        "payment.recorded",
        "subscription.activated",  # converted, paid to 4 February
        "payment.recorded",
        "subscription.renewed",  # paid to 4 March
        "subscription.entering_grace",  # 4 March came and went
        "payment.recorded",
        "subscription.activated",  # paid inside the grace, back to 4 April
        "subscription.entering_grace",  # 4 April, and this time nobody pays
        "subscription.expired",  # 9 April, the courtesy ran out
    ]
    assert sink[-2].occurred_at == utc(2026, 4, 4)
    assert sink[-1].occurred_at == utc(2026, 4, 9)
    assert await engine.is_active("user_1") is False
