"""The README is the spec. These run its examples so it cannot quietly go stale."""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from substate import (
    Accrual,
    Event,
    FrozenClock,
    MemoryStorage,
    Payment,
    Period,
    Plan,
    PromoCode,
    PromoKind,
    ReferralProgram,
    State,
    Subscription,
    SubscriptionEngine,
    SubscriptionExpired,
)
from substate import events as event_types

PRO_MONTH = Plan(
    id="pro_month",
    price=5_000_000,  # 5.00 USDT, minor units
    currency="USDT",
    period=Period.days(30),
    trial_days=3,
)


async def test_the_quickstart_prints_what_it_says_it_prints() -> None:
    clock = FrozenClock("2026-01-01")  # the real clock in production
    engine = SubscriptionEngine(storage=MemoryStorage(), clock=clock)
    engine.register_plan(PRO_MONTH)

    trial = await engine.subscribe("user_1", "pro_month")

    assert (trial.state, trial.access_until) == (State.TRIAL, datetime(2026, 1, 4, tzinfo=UTC))

    await engine.apply_payment(
        Payment(
            provider="cryptobot",
            external_id="inv_12345",
            user_id="user_1",
            amount=5_000_000,
        )
    )

    sub = await engine.get_subscription("user_1")
    assert sub is not None
    assert (sub.state, sub.expires_at) == (State.ACTIVE, datetime(2026, 2, 3, tzinfo=UTC))


async def test_the_same_webhook_twice_changes_nothing_as_advertised() -> None:
    engine = SubscriptionEngine(storage=MemoryStorage(), clock=FrozenClock("2026-01-01"))
    engine.register_plan(PRO_MONTH)
    await engine.subscribe("user_1", "pro_month")
    invoice = Payment(
        provider="cryptobot", external_id="inv_12345", user_id="user_1", amount=5_000_000
    )

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
        Payment(provider="cryptobot", external_id="inv_12345", user_id="user_1", amount=5_000_000)
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
        Payment(provider="cryptobot", external_id="inv_12345", user_id="user_1", amount=5_000_000)
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


async def test_the_referral_example_from_the_readme() -> None:
    """The whole snippet: register a program, put a referrer on it, watch it pay."""
    storage = MemoryStorage()
    engine = SubscriptionEngine(storage=storage, clock=FrozenClock("2026-01-01"))
    engine.register_plan(PRO_MONTH)

    engine.register_referral_program(
        ReferralProgram(
            id="bloggers",
            percent=30,
            accrual=Accrual.EVERY_PAYMENT,
        )
    )
    await engine.assign_program("user_42", "bloggers")

    await engine.subscribe("user_1", "pro_month", referrer_id="user_42")
    events = await engine.apply_payment(
        Payment(provider="cryptobot", external_id="inv_1", user_id="user_1", amount=5_000_000)
    )

    assert [event.name for event in events] == [
        "payment.recorded",
        "subscription.activated",
        "referral.accrued",
    ]
    assert await storage.get_balance("user_42") == 1_500_000  # money received, in minor units


async def test_a_trial_that_never_converted_pays_nobody_as_the_readme_says() -> None:
    clock = FrozenClock("2026-01-01")
    storage = MemoryStorage()
    engine = SubscriptionEngine(storage=storage, clock=clock)
    engine.register_plan(PRO_MONTH)
    engine.register_referral_program(
        ReferralProgram(id="bloggers", percent=30, accrual=Accrual.EVERY_PAYMENT)
    )
    await engine.assign_program("user_42", "bloggers")
    await engine.subscribe("user_1", "pro_month", referrer_id="user_42")

    clock.advance(days=4)
    await engine.tick()

    assert await storage.get_balance("user_42") == 0


async def test_attribution_is_recorded_once_and_never_moves() -> None:
    clock = FrozenClock("2026-01-01")
    engine = SubscriptionEngine(storage=MemoryStorage(), clock=clock)
    engine.register_plan(PRO_MONTH)
    await engine.subscribe("user_1", "pro_month", referrer_id="user_42")
    clock.advance(days=4)
    await engine.tick()

    sub = await engine.subscribe("user_1", "pro_month", referrer_id="someone_else")

    assert sub.referrer_id == "user_42"


README = Path(__file__).resolve().parent.parent / "README.md"


def documented_events() -> set[str]:
    """The dotted names inside the README's event block."""
    block = re.search(r"## Events\n.*?```\n(.*?)```", README.read_text(), re.S)
    assert block is not None, "the README lost its event block"
    return set(re.findall(r"[a-z]+\.[a-z_]+", block.group(1)))


def emitted_events() -> set[str]:
    """The dotted names of every event type the core can emit."""
    return {
        member.name
        for _, member in inspect.getmembers(event_types, inspect.isclass)
        if issubclass(member, Event) and member is not Event
    }


def test_the_readme_lists_every_event_the_core_emits() -> None:
    assert emitted_events() == documented_events()


def test_the_list_is_the_thirteen_from_the_spec() -> None:
    assert len(emitted_events()) == 13


async def test_the_promo_example_from_the_readme() -> None:
    """The outage story: register PLUS_DAYS, hand it out, everyone gets three days."""
    clock = FrozenClock("2026-01-01")
    engine = SubscriptionEngine(storage=MemoryStorage(), clock=clock)
    engine.register_plan(PRO_MONTH)
    for user_id in ("user_1", "user_2"):
        await engine.subscribe(user_id, "pro_month")
        await engine.apply_payment(
            Payment(
                provider="cryptobot",
                external_id=f"inv_{user_id}",
                user_id=user_id,
                amount=5_000_000,
            )
        )

    engine.register_promo_code(
        PromoCode(
            code="SORRY",
            kind=PromoKind.PLUS_DAYS,
            value=3,
        )
    )

    affected = ["user_1", "user_2"]
    for user_id in affected:
        await engine.redeem(user_id, "SORRY")

    for user_id in affected:
        sub = await engine.get_subscription(user_id)
        assert sub is not None
        assert sub.expires_at == datetime(2026, 2, 6, tzinfo=UTC)  # 3 February plus three
        assert sub.promo_code is None  # PLUS_DAYS binds nothing


async def test_the_webhook_example_from_the_readme() -> None:
    """The adapter snippet, from signed bytes to an activated subscription."""
    from substate.adapters.cryptobot import CryptoBotWebhook

    CRYPTO_PAY_TOKEN = "12345:AAbbCCddEEff"
    body = json.dumps(
        {
            "update_id": 1,
            "update_type": "invoice_paid",
            "payload": {
                "invoice_id": 528890,
                "status": "paid",
                "asset": "USDT",
                "amount": "5",
                "payload": "user_1",
            },
        }
    ).encode()
    signature = hmac.new(
        hashlib.sha256(CRYPTO_PAY_TOKEN.encode()).digest(), body, hashlib.sha256
    ).hexdigest()

    engine = SubscriptionEngine(storage=MemoryStorage(), clock=FrozenClock("2026-01-01"))
    engine.register_plan(PRO_MONTH)
    await engine.subscribe("user_1", "pro_month")

    webhook = CryptoBotWebhook(token=CRYPTO_PAY_TOKEN)

    parsed = webhook.parse(body, signature)
    events = await engine.apply_payment(parsed.payment)

    assert parsed.currency == "USDT"
    assert parsed.payment.amount == 5_000_000  # 5.00 USDT in minor units
    assert [event.name for event in events] == ["payment.recorded", "subscription.activated"]


def test_the_picture_and_the_suite_agree_on_the_numbers() -> None:
    """The README's screenshot states a test count. A screenshot that lies is worse
    than none, so the number in the image, in its alt text and in the suite match."""
    picture = (README.parent / "docs" / "test-run.svg").read_text()
    claimed = re.search(r"(\d+) passed", picture)
    advertised = re.search(r"!\[The test suite: (\d+) tests", README.read_text())

    assert claimed is not None and advertised is not None
    assert claimed.group(1) == advertised.group(1)
