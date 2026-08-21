"""The README is the spec. These run its examples so it cannot quietly go stale."""

from __future__ import annotations

from datetime import UTC, datetime

from substate import (
    FrozenClock,
    MemoryStorage,
    Payment,
    Period,
    Plan,
    State,
    SubscriptionEngine,
    SubscriptionExpired,
)

PRO_MONTH = Plan(
    id="pro_month",
    price=29900,  # minor units, always integers
    currency="RUB",
    period=Period.days(30),
    trial_days=3,
)


async def test_the_quickstart_prints_what_it_says_it_prints() -> None:
    engine = SubscriptionEngine(storage=MemoryStorage(), clock=FrozenClock("2026-01-01"))
    engine.register_plan(PRO_MONTH)

    trial = await engine.subscribe("user_1", "pro_month")

    assert (trial.state, trial.access_until) == (State.TRIAL, datetime(2026, 1, 4, tzinfo=UTC))

    await engine.apply_payment(
        Payment(
            provider="cryptobot",
            external_id="inv_12345",
            user_id="user_1",
            amount=29900,
        )
    )

    sub = await engine.get_subscription("user_1")
    assert sub is not None
    assert (sub.state, sub.expires_at) == (State.ACTIVE, datetime(2026, 2, 3, tzinfo=UTC))


async def test_the_same_webhook_twice_changes_nothing_as_advertised() -> None:
    engine = SubscriptionEngine(storage=MemoryStorage(), clock=FrozenClock("2026-01-01"))
    engine.register_plan(PRO_MONTH)
    await engine.subscribe("user_1", "pro_month")
    invoice = Payment(provider="cryptobot", external_id="inv_12345", user_id="user_1", amount=29900)

    await engine.apply_payment(invoice)
    events = await engine.apply_payment(invoice)

    assert [event.name for event in events] == ["payment.duplicate"]
    sub = await engine.get_subscription("user_1")
    assert sub is not None and sub.expires_at == datetime(2026, 2, 3, tzinfo=UTC)


async def test_the_injected_clock_example_from_the_readme() -> None:
    clock = FrozenClock("2026-01-01")
    engine = SubscriptionEngine(storage=MemoryStorage(), clock=clock)
    engine.register_plan(PRO_MONTH)

    await engine.subscribe("user_1", "pro_month")  # TRIAL, 3 days

    clock.advance(days=4)
    events = await engine.tick()

    assert len(events) == 1
    expired = events[0]
    assert isinstance(expired, SubscriptionExpired)
    assert (expired.user_id, expired.reason) == ("user_1", "trial_not_converted")
