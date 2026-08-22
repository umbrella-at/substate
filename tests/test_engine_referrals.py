"""Referrals: who brought whom, and what that is worth at payment time."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from substate import (
    Accrual,
    DuplicateReferralProgram,
    Event,
    FrozenClock,
    MemoryStorage,
    Payment,
    Period,
    Plan,
    PromoCode,
    PromoKind,
    ReferralAccrued,
    ReferralProgram,
    State,
    SubscriptionEngine,
    UnknownReferralProgram,
)

START = "2026-01-01"

BLOGGERS = ReferralProgram(id="bloggers", percent=30, accrual=Accrual.EVERY_PAYMENT)
FRIENDS = ReferralProgram(id="friends", percent=10, accrual=Accrual.FIRST_PAYMENT_ONLY)


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
    clock: FrozenClock,
    *programs: ReferralProgram,
    default: ReferralProgram | None = None,
    storage: MemoryStorage | None = None,
    sink: list[Event] | None = None,
) -> SubscriptionEngine:
    engine = SubscriptionEngine(
        storage=storage if storage is not None else MemoryStorage(),
        clock=clock,
        on_event=None if sink is None else sink.append,
        default_program=default,
    )
    engine.register_plan(plan())
    engine.register_plan(plan("lite", price=9900, trial_days=0, grace_days=0))
    for program in programs:
        engine.register_referral_program(program)
    return engine


def names(events: list[Event]) -> list[str]:
    return [event.name for event in events]


async def test_a_program_id_can_only_be_claimed_once() -> None:
    engine = world(FrozenClock(START), BLOGGERS)

    with pytest.raises(DuplicateReferralProgram):
        engine.register_referral_program(
            ReferralProgram(id="bloggers", percent=5, accrual=Accrual.EVERY_PAYMENT)
        )


async def test_assigning_an_unregistered_program_is_an_error() -> None:
    engine = world(FrozenClock(START))

    with pytest.raises(UnknownReferralProgram):
        await engine.assign_program("blogger_1", "bloggers")


async def test_a_payment_pays_the_referrer() -> None:
    clock = FrozenClock(START)
    storage = MemoryStorage()
    sink: list[Event] = []
    engine = world(clock, BLOGGERS, storage=storage, sink=sink)
    await engine.assign_program("blogger_1", "bloggers")
    await engine.subscribe("user_1", "pro", referrer_id="blogger_1")
    sink.clear()

    events = await engine.apply_payment(payment())

    assert names(events) == ["payment.recorded", "subscription.activated", "referral.accrued"]
    assert await storage.get_balance("blogger_1") == 8970  # thirty percent of 29900


async def test_the_accrual_event_says_who_earned_it_for_whom() -> None:
    clock = FrozenClock(START)
    engine = world(clock, BLOGGERS)
    await engine.assign_program("blogger_1", "bloggers")
    await engine.subscribe("user_1", "pro", referrer_id="blogger_1")

    events = await engine.apply_payment(payment())

    accrued = events[2]
    assert isinstance(accrued, ReferralAccrued)
    assert accrued.user_id == "blogger_1"
    assert accrued.referred_user_id == "user_1"
    assert (accrued.program_id, accrued.amount) == ("bloggers", 8970)


async def test_a_trial_that_never_converted_pays_nobody() -> None:
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, BLOGGERS, storage=storage)
    await engine.assign_program("blogger_1", "bloggers")
    await engine.subscribe("user_1", "pro", referrer_id="blogger_1")
    clock.advance(days=4)

    events = await engine.tick()

    assert names(events) == ["subscription.expired"]
    assert await storage.get_balance("blogger_1") == 0


async def test_an_underpayment_pays_nobody() -> None:
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, BLOGGERS, storage=storage)
    await engine.assign_program("blogger_1", "bloggers")
    await engine.subscribe("user_1", "pro", referrer_id="blogger_1")

    events = await engine.apply_payment(payment(amount=100))

    assert names(events) == ["payment.recorded", "payment.underpaid"]
    assert await storage.get_balance("blogger_1") == 0


async def test_every_payment_pays_a_blogger_again() -> None:
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, BLOGGERS, storage=storage)
    await engine.assign_program("blogger_1", "bloggers")
    await engine.subscribe("user_1", "pro", referrer_id="blogger_1")

    await engine.apply_payment(payment())
    await engine.apply_payment(payment(external_id="inv_2"))

    assert await storage.get_balance("blogger_1") == 17940


async def test_first_payment_only_pays_once() -> None:
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, FRIENDS, storage=storage)
    await engine.assign_program("friend_1", "friends")
    await engine.subscribe("user_1", "pro", referrer_id="friend_1")

    first = await engine.apply_payment(payment())
    second = await engine.apply_payment(payment(external_id="inv_2"))

    assert names(first) == ["payment.recorded", "subscription.activated", "referral.accrued"]
    assert names(second) == ["payment.recorded", "subscription.renewed"]
    assert await storage.get_balance("friend_1") == 2990


async def test_first_payment_only_does_not_reset_with_a_new_cycle() -> None:
    """One payment per person brought in, not per cycle. Churn is not a new referral."""
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, FRIENDS, storage=storage)
    await engine.assign_program("friend_1", "friends")
    await engine.subscribe("user_1", "pro", referrer_id="friend_1")
    await engine.apply_payment(payment())
    clock.advance(days=40)
    await engine.tick()
    await engine.subscribe("user_1", "pro")

    await engine.apply_payment(payment(external_id="inv_2"))

    assert await storage.get_balance("friend_1") == 2990


async def test_the_referrer_survives_a_new_cycle() -> None:
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, BLOGGERS, storage=storage)
    await engine.assign_program("blogger_1", "bloggers")
    await engine.subscribe("user_1", "pro", referrer_id="blogger_1")
    clock.advance(days=4)
    await engine.tick()

    sub = await engine.subscribe("user_1", "pro", referrer_id="someone_else")

    assert sub.referrer_id == "blogger_1"
    await engine.apply_payment(payment())
    assert await storage.get_balance("blogger_1") == 8970
    assert await storage.get_balance("someone_else") == 0


async def test_two_referrers_on_different_programs_earn_differently() -> None:
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, BLOGGERS, FRIENDS, storage=storage)
    await engine.assign_program("blogger_1", "bloggers")
    await engine.assign_program("friend_1", "friends")
    await engine.subscribe("user_1", "pro", referrer_id="blogger_1")
    await engine.subscribe("user_2", "pro", referrer_id="friend_1")

    await engine.apply_payment(payment(user_id="user_1", external_id="inv_1"))
    await engine.apply_payment(payment(user_id="user_2", external_id="inv_2"))

    assert await storage.get_balance("blogger_1") == 8970
    assert await storage.get_balance("friend_1") == 2990


async def test_a_referrer_with_no_program_falls_into_the_default() -> None:
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, BLOGGERS, default=FRIENDS, storage=storage)
    await engine.subscribe("user_1", "pro", referrer_id="stranger")

    await engine.apply_payment(payment())

    assert await storage.get_balance("stranger") == 2990


async def test_the_built_in_default_pays_nothing_and_says_nothing() -> None:
    """Attribution without a configured program is recorded, not rewarded."""
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, storage=storage)
    sub = await engine.subscribe("user_1", "pro", referrer_id="stranger")

    events = await engine.apply_payment(payment())

    assert sub.referrer_id == "stranger"
    assert names(events) == ["payment.recorded", "subscription.activated"]
    assert await storage.get_balance("stranger") == 0

    unstamped = await engine.get_subscription("user_1")
    assert unstamped is not None and unstamped.referral_accrued_at is None


async def test_a_program_change_leaves_what_was_already_earned_alone() -> None:
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, BLOGGERS, FRIENDS, storage=storage)
    await engine.assign_program("blogger_1", "bloggers")
    await engine.subscribe("user_1", "pro", referrer_id="blogger_1")
    await engine.apply_payment(payment())

    await engine.assign_program("blogger_1", "friends")

    assert await storage.get_balance("blogger_1") == 8970


async def test_a_program_change_applies_to_what_comes_next() -> None:
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, BLOGGERS, storage=storage)
    engine.register_referral_program(
        ReferralProgram(id="partners", percent=50, accrual=Accrual.EVERY_PAYMENT)
    )
    await engine.assign_program("blogger_1", "bloggers")
    await engine.subscribe("user_1", "pro", referrer_id="blogger_1")
    await engine.apply_payment(payment())

    await engine.assign_program("blogger_1", "partners")
    await engine.apply_payment(payment(external_id="inv_2"))

    assert await storage.get_balance("blogger_1") == 8970 + 14950


async def test_the_cut_is_taken_from_the_money_that_arrived() -> None:
    """Not from the price: an overpayment pays the referrer more, as it should."""
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, BLOGGERS, storage=storage)
    await engine.assign_program("blogger_1", "bloggers")
    await engine.subscribe("user_1", "pro", referrer_id="blogger_1")

    await engine.apply_payment(payment(amount=40000))

    assert await storage.get_balance("blogger_1") == 12000


async def test_a_discounted_payment_pays_the_referrer_from_the_discounted_sum() -> None:
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, BLOGGERS, storage=storage)
    engine.register_promo_code(PromoCode(code="SUMMER", kind=PromoKind.PERCENT, value=30))
    await engine.assign_program("blogger_1", "bloggers")
    await engine.subscribe("user_1", "pro", promo="SUMMER", referrer_id="blogger_1")

    await engine.apply_payment(payment(amount=20930))

    assert await storage.get_balance("blogger_1") == 6279


async def test_free_days_pay_nobody() -> None:
    """No money moved, so there is nothing to take a cut of."""
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, BLOGGERS, storage=storage)
    engine.register_promo_code(PromoCode(code="FREEWEEK", kind=PromoKind.PLUS_DAYS, value=7))
    await engine.assign_program("blogger_1", "bloggers")

    sub = await engine.subscribe("user_1", "lite", promo="FREEWEEK", referrer_id="blogger_1")

    assert sub.state is State.TRIAL
    assert await storage.get_balance("blogger_1") == 0


async def test_a_subscription_without_a_referrer_pays_nobody() -> None:
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, BLOGGERS, storage=storage)
    await engine.subscribe("user_1", "pro")

    events = await engine.apply_payment(payment())

    assert names(events) == ["payment.recorded", "subscription.activated"]


async def test_the_default_program_can_be_named_when_assigning() -> None:
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, default=FRIENDS, storage=storage)

    await engine.assign_program("friend_1", "friends")
    await engine.subscribe("user_1", "pro", referrer_id="friend_1")
    await engine.apply_payment(payment())

    assert await storage.get_balance("friend_1") == 2990


async def test_a_referrer_earns_nothing_from_a_payment_that_bought_nothing() -> None:
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, BLOGGERS, storage=storage)
    await engine.assign_program("blogger_1", "bloggers")
    await engine.subscribe("user_1", "pro", referrer_id="blogger_1")
    await engine.apply_payment(payment())

    await engine.apply_payment(payment())  # the same webhook again

    assert await storage.get_balance("blogger_1") == 8970


async def test_a_program_that_is_no_longer_registered_falls_back_to_the_default() -> None:
    """A referrer's bookkeeping must not be able to block somebody else's payment."""
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, BLOGGERS, default=FRIENDS, storage=storage)
    await engine.assign_program("blogger_1", "bloggers")
    await engine.subscribe("user_1", "pro", referrer_id="blogger_1")

    restarted = world(clock, default=FRIENDS, storage=storage)  # bloggers is gone
    events = await restarted.apply_payment(payment())

    assert names(events) == ["payment.recorded", "subscription.activated", "referral.accrued"]
    assert await storage.get_balance("blogger_1") == 2990  # the default's ten percent


async def test_a_payment_worth_nothing_to_the_referrer_does_not_retire_them() -> None:
    """A zero accrual stamps nothing, or the first free payment would end the referral.

    FIRST_PAYMENT_ONLY reads the stamp as "already paid". Setting it for an
    accrual of zero would mean a referrer whose program was configured a day
    late never earns anything at all.
    """
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, FRIENDS, storage=storage)  # friend_1 is on no program yet
    await engine.subscribe("user_1", "pro", referrer_id="friend_1")

    events = await engine.apply_payment(payment())

    assert names(events) == ["payment.recorded", "subscription.activated"]
    unstamped = await engine.get_subscription("user_1")
    assert unstamped is not None and unstamped.referral_accrued_at is None

    await engine.assign_program("friend_1", "friends")
    later = await engine.apply_payment(payment(external_id="inv_2"))

    assert names(later) == ["payment.recorded", "subscription.renewed", "referral.accrued"]
    assert await storage.get_balance("friend_1") == 2990
    stamped = await engine.get_subscription("user_1")
    assert stamped is not None and stamped.referral_accrued_at == utc(2026, 1, 1)
