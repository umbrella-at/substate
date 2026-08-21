"""apply_payment(): money meets the state machine."""

from __future__ import annotations

from datetime import UTC, datetime

from substate import (
    Event,
    FrozenClock,
    MemoryStorage,
    Payment,
    PaymentUnderpaid,
    Period,
    Plan,
    State,
    SubscriptionActivated,
    SubscriptionEngine,
    SubscriptionRenewed,
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
    engine.register_plan(plan("lite", price=9900, trial_days=0, grace_days=0))
    engine.register_plan(plan("annual", price=99900, period=Period.days(365), trial_days=0))
    return engine


def names(events: list[Event]) -> list[str]:
    return [event.name for event in events]


async def test_paying_in_a_trial_activates_from_the_end_of_the_trial() -> None:
    """Three days were promised, so three days are served whenever they pay."""
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")

    events = await engine.apply_payment(payment())

    assert names(events) == ["payment.recorded", "subscription.activated"]
    sub = await engine.get_subscription("user_1")
    assert sub is not None
    assert sub.state is State.ACTIVE
    assert sub.expires_at == utc(2026, 2, 3)


async def test_the_activation_event_carries_the_new_boundary() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")

    events = await engine.apply_payment(payment())

    activated = events[1]
    assert isinstance(activated, SubscriptionActivated)
    assert activated.expires_at == utc(2026, 2, 3)
    assert activated.plan_id == "pro"


async def test_paying_again_renews_from_the_current_boundary() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())

    events = await engine.apply_payment(payment(external_id="inv_2"))

    assert names(events) == ["payment.recorded", "subscription.renewed"]
    assert isinstance(events[1], SubscriptionRenewed)
    sub = await engine.get_subscription("user_1")
    assert sub is not None and sub.expires_at == utc(2026, 3, 5)


async def test_paying_inside_grace_eats_the_grace_days() -> None:
    """Grace is a debt, not a postponement: the period kept running."""
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    clock.advance(days=35)  # 5 February, two days into a five day grace
    await engine.tick()
    grace = await engine.get_subscription("user_1")
    assert grace is not None and grace.state is State.GRACE

    events = await engine.apply_payment(payment(external_id="inv_2"))

    assert names(events) == ["payment.recorded", "subscription.activated"]
    sub = await engine.get_subscription("user_1")
    assert sub is not None
    assert sub.state is State.ACTIVE
    assert sub.expires_at == utc(2026, 3, 5)


async def test_paying_after_expiry_counts_from_the_payment() -> None:
    """A lapsed subscription renewed from a two-month-old date would land in the past."""
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "lite")
    clock.advance(days=60)  # 2 March
    await engine.tick()

    events = await engine.apply_payment(payment(amount=9900))

    assert names(events) == ["payment.recorded", "subscription.activated"]
    sub = await engine.get_subscription("user_1")
    assert sub is not None
    assert sub.expires_at == utc(2026, 4, 1)


async def test_a_payment_from_a_stranger_is_unmatched() -> None:
    clock = FrozenClock(START)
    engine = world(clock)

    events = await engine.apply_payment(payment(user_id="nobody"))

    assert names(events) == ["payment.recorded", "payment.unmatched"]
    assert await engine.get_subscription("nobody") is None


async def test_an_unmatched_payment_is_still_written_down() -> None:
    """Money arrived. A second delivery of the same webhook is a duplicate."""
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, storage)

    await engine.apply_payment(payment(user_id="nobody"))
    events = await engine.apply_payment(payment(user_id="nobody"))

    assert names(events) == ["payment.duplicate"]
    assert await storage.get_payment("cryptobot", "inv_1") is not None


async def test_too_little_money_moves_nothing() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")

    events = await engine.apply_payment(payment(amount=100))

    assert names(events) == ["payment.recorded", "payment.underpaid"]
    sub = await engine.get_subscription("user_1")
    assert sub is not None and sub.state is State.TRIAL


async def test_an_underpayment_says_what_was_expected() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")

    events = await engine.apply_payment(payment(amount=100))

    underpaid = events[1]
    assert isinstance(underpaid, PaymentUnderpaid)
    assert (underpaid.amount, underpaid.expected) == (100, 29900)


async def test_an_underpayment_is_written_down() -> None:
    """The core does not guess whether they will top it up. The application decides."""
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, storage)
    await engine.subscribe("user_1", "pro")

    await engine.apply_payment(payment(amount=100))

    assert await storage.get_payment("cryptobot", "inv_1") is not None


async def test_paying_exactly_the_price_is_enough() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "lite")

    events = await engine.apply_payment(payment(amount=9900))

    assert names(events) == ["payment.recorded", "subscription.activated"]


async def test_overpaying_buys_exactly_one_period() -> None:
    """The excess is not stored and not carried over. It is written in the README."""
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "lite")

    await engine.apply_payment(payment(amount=99900))

    sub = await engine.get_subscription("user_1")
    assert sub is not None and sub.expires_at == utc(2026, 1, 31)


async def test_the_same_pair_twice_changes_nothing() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())

    events = await engine.apply_payment(payment())

    assert names(events) == ["payment.duplicate"]
    sub = await engine.get_subscription("user_1")
    assert sub is not None and sub.expires_at == utc(2026, 2, 3)


async def test_the_same_webhook_four_seconds_later() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    clock.advance(seconds=4)

    events = await engine.apply_payment(payment())

    assert names(events) == ["payment.duplicate"]
    sub = await engine.get_subscription("user_1")
    assert sub is not None and sub.state is State.ACTIVE


async def test_a_duplicate_arriving_a_month_late_still_only_duplicates() -> None:
    """A retried webhook is not a reason to advance the world. tick() is."""
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    clock.advance(days=45)

    events = await engine.apply_payment(payment())

    assert names(events) == ["payment.duplicate"]
    sub = await engine.get_subscription("user_1")
    assert sub is not None
    assert sub.state is State.ACTIVE
    assert sub.expires_at == utc(2026, 2, 3)


async def test_the_same_external_id_from_another_provider_is_another_payment() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())

    events = await engine.apply_payment(payment(provider="stars"))

    assert names(events) == ["payment.recorded", "subscription.renewed"]


async def test_a_payment_catches_the_subscription_up_before_applying_itself() -> None:
    """The spec's example: a payment on a trial that ended forty days ago."""
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    clock.advance(days=40)

    events = await engine.apply_payment(payment())

    assert names(events) == [
        "subscription.expired",
        "payment.recorded",
        "subscription.activated",
    ]
    sub = await engine.get_subscription("user_1")
    assert sub is not None
    assert sub.expires_at == utc(2026, 3, 12)


async def test_the_catch_up_events_come_first_because_they_happened_first() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")
    clock.advance(days=40)

    events = await engine.apply_payment(payment())

    assert [event.occurred_at for event in events] == [
        utc(2026, 1, 4),
        utc(2026, 2, 10),
        utc(2026, 2, 10),
    ]


async def test_the_sink_sees_the_payment_events_too() -> None:
    clock = FrozenClock(START)
    sink: list[Event] = []
    engine = world(clock, sink=sink)
    await engine.subscribe("user_1", "pro")
    sink.clear()

    returned = await engine.apply_payment(payment())

    assert sink == returned


async def test_paying_restores_access_immediately() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "lite")
    assert await engine.is_active("user_1") is False

    await engine.apply_payment(payment(amount=9900))

    assert await engine.is_active("user_1") is True
