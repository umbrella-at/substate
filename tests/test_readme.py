"""The README is the spec. These run its examples so it cannot quietly go stale."""

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
    Subscription,
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


async def test_the_access_check_from_the_readme() -> None:
    """`engine.is_active()` is right the moment a period ends, not at the next tick."""
    clock = FrozenClock("2026-01-01")
    engine = SubscriptionEngine(storage=MemoryStorage(), clock=clock)
    engine.register_plan(PRO_MONTH)
    await engine.subscribe("user_1", "pro_month")

    assert await engine.is_active("user_1") is True

    clock.advance(days=3)  # the trial ends on 2026-01-04, and no tick has run

    assert await engine.is_active("user_1") is False


async def test_the_two_access_answers_the_readme_distinguishes() -> None:
    """The record still says TRIAL; only the engine knows what time it is."""
    clock = FrozenClock("2026-01-01")
    engine = SubscriptionEngine(storage=MemoryStorage(), clock=clock)
    engine.register_plan(PRO_MONTH)
    await engine.subscribe("user_1", "pro_month")
    clock.advance(days=3)

    sub = await engine.get_subscription("user_1")
    assert sub is not None
    assert sub.is_active is True  # a predicate over state, with no notion of time
    assert sub.access_until == datetime(2026, 1, 4, tzinfo=UTC)
    assert await engine.is_active("user_1") is False


async def test_the_journal_sink_from_the_readme() -> None:
    """A sink sees everything, including the calls that hand back a subscription."""
    journal: list[Event] = []
    clock = FrozenClock("2026-01-01")
    storage = MemoryStorage()
    engine = SubscriptionEngine(storage, clock=clock, on_event=journal.append)
    engine.register_plan(PRO_MONTH)

    await engine.subscribe("user_1", "pro_month")
    returned = await engine.apply_payment(
        Payment(provider="cryptobot", external_id="inv_12345", user_id="user_1", amount=29900)
    )
    await engine.cancel("user_1")

    assert [event.name for event in journal] == [
        "subscription.created",  # from subscribe, which returned a Subscription
        "payment.recorded",
        "subscription.activated",
        "subscription.cancelled",  # from cancel, which returned a Subscription
    ]
    assert returned == journal[1:3]  # payment events reach both consumers


class RecordingStorage(MemoryStorage):
    """A storage that remembers the order it was written in."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def save_subscription(self, subscription: Subscription) -> None:
        self.calls.append("save")
        await super().save_subscription(subscription)


async def test_the_sink_never_runs_before_the_state_is_saved() -> None:
    """The README promises a failing notification cannot roll back a transition."""
    storage = RecordingStorage()
    engine = SubscriptionEngine(
        storage,
        clock=FrozenClock("2026-01-01"),
        on_event=lambda event: storage.calls.append(event.name),
    )
    engine.register_plan(PRO_MONTH)

    await engine.subscribe("user_1", "pro_month")
    await engine.apply_payment(
        Payment(provider="cryptobot", external_id="inv_12345", user_id="user_1", amount=29900)
    )
    await engine.cancel("user_1")

    assert storage.calls == [
        "save",
        "subscription.created",
        "save",
        "payment.recorded",
        "subscription.activated",
        "save",
        "subscription.cancelled",
    ]


async def test_the_engine_check_reads_the_state_as_well_as_the_clock() -> None:
    """A boundary in the future does not resurrect a subscription that is over."""
    storage = MemoryStorage()
    engine = SubscriptionEngine(storage, clock=FrozenClock("2026-01-01"))
    engine.register_plan(PRO_MONTH)
    await storage.save_subscription(
        Subscription(
            user_id="user_1",
            plan_id="pro_month",
            state=State.EXPIRED,
            expires_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
    )

    stored = await engine.get_subscription("user_1")
    assert stored is not None and stored.is_active is False
    assert await engine.is_active("user_1") is False
