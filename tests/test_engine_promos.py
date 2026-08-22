"""Redeeming promo codes: what a code does the moment it is claimed."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from substate import (
    DuplicatePromoCode,
    Event,
    FrozenClock,
    MemoryStorage,
    NotSubscribed,
    Payment,
    Period,
    Plan,
    PromoAlreadyBound,
    PromoCode,
    PromoKind,
    PromoLimitReached,
    PromoScope,
    State,
    SubscriptionEngine,
    UnknownPromoCode,
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


def promo(code: str = "SUMMER", **overrides: object) -> PromoCode:
    fields: dict[str, object] = {"code": code, "kind": PromoKind.PERCENT, "value": 30}
    fields.update(overrides)
    return PromoCode(**fields)  # type: ignore[arg-type]


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
    *promos: PromoCode,
    storage: MemoryStorage | None = None,
    sink: list[Event] | None = None,
) -> SubscriptionEngine:
    engine = SubscriptionEngine(
        storage=storage if storage is not None else MemoryStorage(),
        clock=clock,
        on_event=None if sink is None else sink.append,
    )
    engine.register_plan(plan())
    engine.register_plan(plan("lite", price=9900, trial_days=0, grace_days=0))
    for code in promos:
        engine.register_promo_code(code)
    return engine


def names(events: list[Event]) -> list[str]:
    return [event.name for event in events]


async def test_a_code_can_only_be_registered_once() -> None:
    engine = world(FrozenClock(START), promo())

    with pytest.raises(DuplicatePromoCode):
        engine.register_promo_code(promo(value=50))


async def test_an_unregistered_code_is_refused() -> None:
    clock = FrozenClock(START)
    engine = world(clock)

    with pytest.raises(UnknownPromoCode):
        await engine.subscribe("user_1", "pro", promo="NOPE")


async def test_a_refused_code_leaves_no_subscription_behind() -> None:
    """Either with the code or not at all: the caller must be able to ask again."""
    clock = FrozenClock(START)
    engine = world(clock)

    with pytest.raises(UnknownPromoCode):
        await engine.subscribe("user_1", "pro", promo="NOPE")

    assert await engine.get_subscription("user_1") is None


async def test_a_discount_is_bound_to_the_subscription() -> None:
    clock = FrozenClock(START)
    sink: list[Event] = []
    engine = world(clock, promo(), sink=sink)

    sub = await engine.subscribe("user_1", "pro", promo="SUMMER")

    assert sub.promo_code == "SUMMER"
    assert names(sink) == ["subscription.created", "promo.redeemed"]


async def test_first_payment_is_one_period() -> None:
    """FIRST_PAYMENT is N_PERIODS(1) wearing a friendlier name."""
    clock = FrozenClock(START)
    engine = world(clock, promo())

    sub = await engine.subscribe("user_1", "pro", promo="SUMMER")

    assert sub.promo_periods_left == 1


async def test_n_periods_is_kept_as_given() -> None:
    clock = FrozenClock(START)
    engine = world(clock, promo(applies_to=PromoScope.n_periods(3)))

    sub = await engine.subscribe("user_1", "pro", promo="SUMMER")

    assert sub.promo_periods_left == 3


async def test_forever_keeps_no_counter() -> None:
    clock = FrozenClock(START)
    engine = world(clock, promo(applies_to=PromoScope.forever()))

    sub = await engine.subscribe("user_1", "pro", promo="SUMMER")

    assert sub.promo_code == "SUMMER"
    assert sub.promo_periods_left is None


async def test_free_days_in_a_trial_move_the_trial() -> None:
    engine = world(FrozenClock(START), promo("FREEWEEK", kind=PromoKind.PLUS_DAYS, value=7))

    sub = await engine.subscribe("user_1", "pro", promo="FREEWEEK")

    assert sub.state is State.TRIAL
    assert sub.trial_ends_at == utc(2026, 1, 11)  # three trial days plus seven


async def test_free_days_leave_no_binding_behind() -> None:
    """PLUS_DAYS is spent the moment it is claimed. Nothing is owed to a payment."""
    engine = world(FrozenClock(START), promo("FREEWEEK", kind=PromoKind.PLUS_DAYS, value=7))

    sub = await engine.subscribe("user_1", "pro", promo="FREEWEEK")

    assert sub.promo_code is None
    assert sub.promo_periods_left is None


async def test_free_days_on_a_plan_without_a_trial_start_a_trial() -> None:
    """A record with no access would waste the gift, so the days become a trial."""
    clock = FrozenClock(START)
    sink: list[Event] = []
    engine = world(clock, promo("FREEWEEK", kind=PromoKind.PLUS_DAYS, value=7), sink=sink)

    sub = await engine.subscribe("user_1", "lite", promo="FREEWEEK")

    assert sub.state is State.TRIAL
    assert sub.trial_ends_at == utc(2026, 1, 8)
    assert sub.trial_started_at == utc(2026, 1, 1)
    assert await engine.is_active("user_1") is True
    assert names(sink) == ["subscription.created", "promo.redeemed"]


async def test_free_days_never_hand_out_a_grace_period() -> None:
    """Trial, not ACTIVE: grace is a courtesy to someone who has paid."""
    clock = FrozenClock(START)
    engine = world(clock, promo("FREEWEEK", kind=PromoKind.PLUS_DAYS, value=7))
    await engine.subscribe("user_1", "lite", promo="FREEWEEK")
    clock.advance(days=8)

    events = await engine.tick()

    assert names(events) == ["subscription.expired"]


async def test_free_days_on_a_restarted_cycle_start_a_trial() -> None:
    clock = FrozenClock(START)
    engine = world(clock, promo("FREEWEEK", kind=PromoKind.PLUS_DAYS, value=7))
    await engine.subscribe("user_1", "pro")
    clock.advance(days=4)
    await engine.tick()  # the trial lapsed, no second one is owed

    sub = await engine.subscribe("user_1", "pro", promo="FREEWEEK")

    assert sub.state is State.TRIAL
    assert sub.trial_ends_at == utc(2026, 1, 12)
    assert sub.trial_started_at == utc(2026, 1, 1)  # the original grant stands


async def test_free_days_extend_a_running_paid_period() -> None:
    clock = FrozenClock(START)
    engine = world(clock, promo("FREEWEEK", kind=PromoKind.PLUS_DAYS, value=7))
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())  # paid to 3 February

    sub = await engine.redeem("user_1", "FREEWEEK")

    assert sub.state is State.ACTIVE
    assert sub.expires_at == utc(2026, 2, 10)


async def test_free_days_extend_a_running_trial() -> None:
    clock = FrozenClock(START)
    engine = world(clock, promo("FREEWEEK", kind=PromoKind.PLUS_DAYS, value=7))
    await engine.subscribe("user_1", "pro")

    sub = await engine.redeem("user_1", "FREEWEEK")

    assert sub.state is State.TRIAL
    assert sub.trial_ends_at == utc(2026, 1, 11)


async def test_free_days_can_lift_a_subscription_out_of_grace() -> None:
    clock = FrozenClock(START)
    sink: list[Event] = []
    engine = world(clock, promo("FREEWEEK", kind=PromoKind.PLUS_DAYS, value=7), sink=sink)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    clock.advance(days=35)  # 5 February, two days into the grace
    await engine.tick()
    sink.clear()

    sub = await engine.redeem("user_1", "FREEWEEK")

    assert sub.state is State.ACTIVE
    assert sub.expires_at == utc(2026, 2, 10)
    assert names(sink) == ["promo.redeemed", "subscription.activated"]


async def test_free_days_too_few_to_clear_the_grace_leave_it_in_grace() -> None:
    clock = FrozenClock(START)
    engine = world(clock, promo("FREEDAY", kind=PromoKind.PLUS_DAYS, value=1))
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    clock.advance(days=35)
    await engine.tick()

    sub = await engine.redeem("user_1", "FREEDAY")

    assert sub.state is State.GRACE
    assert sub.expires_at == utc(2026, 2, 4)


async def test_free_days_on_a_cancelled_subscription_extend_the_notice() -> None:
    clock = FrozenClock(START)
    engine = world(clock, promo("FREEWEEK", kind=PromoKind.PLUS_DAYS, value=7))
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    await engine.cancel("user_1")

    sub = await engine.redeem("user_1", "FREEWEEK")

    assert sub.state is State.CANCELLED
    assert sub.expires_at == utc(2026, 2, 10)


async def test_a_second_discount_is_refused() -> None:
    """Two discounts on one payment have no defined winner."""
    clock = FrozenClock(START)
    engine = world(clock, promo(), promo("SPRING", kind=PromoKind.FIXED, value=5000))
    await engine.subscribe("user_1", "pro", promo="SUMMER")

    with pytest.raises(PromoAlreadyBound):
        await engine.redeem("user_1", "SPRING")


async def test_free_days_are_welcome_next_to_a_discount() -> None:
    """PLUS_DAYS binds nothing, so it cannot collide with the bound discount."""
    clock = FrozenClock(START)
    engine = world(clock, promo(), promo("FREEWEEK", kind=PromoKind.PLUS_DAYS, value=7))
    await engine.subscribe("user_1", "pro", promo="SUMMER")

    sub = await engine.redeem("user_1", "FREEWEEK")

    assert sub.promo_code == "SUMMER"
    assert sub.trial_ends_at == utc(2026, 1, 11)


async def test_redeeming_for_a_stranger_is_an_error() -> None:
    engine = world(FrozenClock(START), promo())

    with pytest.raises(NotSubscribed):
        await engine.redeem("nobody", "SUMMER")


async def test_redeeming_an_unregistered_code_is_an_error() -> None:
    clock = FrozenClock(START)
    engine = world(clock)
    await engine.subscribe("user_1", "pro")

    with pytest.raises(UnknownPromoCode):
        await engine.redeem("user_1", "NOPE")


async def test_redeem_catches_the_subscription_up_first() -> None:
    clock = FrozenClock(START)
    sink: list[Event] = []
    engine = world(clock, promo("FREEWEEK", kind=PromoKind.PLUS_DAYS, value=7), sink=sink)
    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(payment())
    clock.advance(days=40)  # past the expiry and past the grace
    sink.clear()

    sub = await engine.redeem("user_1", "FREEWEEK")

    assert names(sink)[:2] == ["subscription.entering_grace", "subscription.expired"]
    assert sub.state is State.TRIAL  # expired, so the days become a trial


async def test_the_per_user_limit_is_enforced() -> None:
    clock = FrozenClock(START)
    engine = world(clock, promo(max_per_user=1))
    await engine.subscribe("user_1", "pro", promo="SUMMER")
    clock.advance(days=4)
    await engine.tick()

    with pytest.raises(PromoLimitReached):
        await engine.subscribe("user_1", "pro", promo="SUMMER")


async def test_a_redemption_that_never_paid_still_spends_its_slot() -> None:
    """Otherwise subscribe, lapse, subscribe would hand out the code forever."""
    clock = FrozenClock(START)
    storage = MemoryStorage()
    engine = world(clock, promo(max_per_user=1), storage=storage)
    await engine.subscribe("user_1", "pro", promo="SUMMER")
    clock.advance(days=4)
    await engine.tick()

    assert await storage.try_redeem("SUMMER", "user_1", max_total=None, max_per_user=1) is False


async def test_the_total_limit_is_enforced_across_users() -> None:
    clock = FrozenClock(START)
    engine = world(clock, promo(max_redemptions=2))
    await engine.subscribe("user_1", "pro", promo="SUMMER")
    await engine.subscribe("user_2", "pro", promo="SUMMER")

    with pytest.raises(PromoLimitReached):
        await engine.subscribe("user_3", "pro", promo="SUMMER")


async def test_a_limit_refusal_leaves_no_subscription_behind() -> None:
    clock = FrozenClock(START)
    engine = world(clock, promo(max_redemptions=1))
    await engine.subscribe("user_1", "pro", promo="SUMMER")

    with pytest.raises(PromoLimitReached):
        await engine.subscribe("user_2", "pro", promo="SUMMER")

    assert await engine.get_subscription("user_2") is None


async def test_a_refused_redemption_does_not_burn_a_slot() -> None:
    clock = FrozenClock(START)
    engine = world(clock, promo(max_redemptions=2), promo("SPRING", kind=PromoKind.FIXED, value=1))
    await engine.subscribe("user_1", "pro", promo="SUMMER")

    with pytest.raises(PromoAlreadyBound):
        await engine.redeem("user_1", "SPRING")

    sub = await engine.subscribe("user_2", "pro", promo="SPRING")
    assert sub.promo_code == "SPRING"


async def test_a_promo_does_not_survive_the_cycle_it_was_claimed_in() -> None:
    """Even FOREVER: the binding dies with the cycle, and a new one starts clean."""
    clock = FrozenClock(START)
    engine = world(clock, promo(applies_to=PromoScope.forever(), max_per_user=5))
    await engine.subscribe("user_1", "pro", promo="SUMMER")
    clock.advance(days=4)
    await engine.tick()

    sub = await engine.subscribe("user_1", "pro")

    assert sub.promo_code is None
    assert sub.promo_periods_left is None
