"""The storage contract, exercised through the in-memory implementation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from substate import MemoryStorage, Payment, State, Storage, Subscription

NOW = datetime(2026, 2, 1, tzinfo=UTC)
YESTERDAY = NOW - timedelta(days=1)
TOMORROW = NOW + timedelta(days=1)


def subscription(**overrides: object) -> Subscription:
    fields: dict[str, object] = {
        "user_id": "user_1",
        "plan_id": "pro_month",
        "state": State.ACTIVE,
        "expires_at": YESTERDAY,
    }
    fields.update(overrides)
    return Subscription(**fields)  # type: ignore[arg-type]


def payment(**overrides: object) -> Payment:
    fields: dict[str, object] = {
        "provider": "cryptobot",
        "external_id": "inv_1",
        "user_id": "user_1",
        "amount": 29900,
    }
    fields.update(overrides)
    return Payment(**fields)  # type: ignore[arg-type]


async def due_users(storage: Storage, now: datetime = NOW) -> list[str]:
    return [sub.user_id async for sub in storage.iter_due(now)]


def test_memory_storage_satisfies_the_protocol() -> None:
    storage: Storage = MemoryStorage()

    assert storage is not None


async def test_a_saved_subscription_comes_back() -> None:
    storage = MemoryStorage()

    await storage.save_subscription(subscription())

    stored = await storage.get_subscription("user_1")
    assert stored is not None
    assert stored.plan_id == "pro_month"


async def test_an_unknown_user_has_no_subscription() -> None:
    assert await MemoryStorage().get_subscription("nobody") is None


async def test_saving_twice_keeps_the_later_version() -> None:
    storage = MemoryStorage()

    await storage.save_subscription(subscription())
    await storage.save_subscription(subscription(state=State.GRACE))

    stored = await storage.get_subscription("user_1")
    assert stored is not None
    assert stored.state is State.GRACE


async def test_storage_keeps_its_own_copy() -> None:
    """Mutating a subscription without saving it changes nothing, as in a real store."""
    storage = MemoryStorage()
    sub = subscription()
    await storage.save_subscription(sub)

    sub.state = State.EXPIRED

    stored = await storage.get_subscription("user_1")
    assert stored is not None
    assert stored.state is State.ACTIVE


async def test_reading_hands_out_a_copy_too() -> None:
    storage = MemoryStorage()
    await storage.save_subscription(subscription())

    first = await storage.get_subscription("user_1")
    assert first is not None
    first.state = State.EXPIRED

    second = await storage.get_subscription("user_1")
    assert second is not None
    assert second.state is State.ACTIVE


async def test_a_saved_payment_comes_back_by_its_pair() -> None:
    storage = MemoryStorage()

    await storage.save_payment(payment())

    stored = await storage.get_payment("cryptobot", "inv_1")
    assert stored is not None
    assert stored.amount == 29900


async def test_an_unseen_payment_is_absent() -> None:
    assert await MemoryStorage().get_payment("cryptobot", "inv_404") is None


async def test_the_same_external_id_from_another_provider_is_another_payment() -> None:
    """Idempotency is per pair. Two providers numbering invoices from 1 must not collide."""
    storage = MemoryStorage()
    await storage.save_payment(payment(provider="cryptobot", amount=29900))
    await storage.save_payment(payment(provider="stars", amount=100))

    from_bot = await storage.get_payment("cryptobot", "inv_1")
    from_stars = await storage.get_payment("stars", "inv_1")

    assert from_bot is not None and from_bot.amount == 29900
    assert from_stars is not None and from_stars.amount == 100


async def test_a_trial_is_due_once_its_trial_has_ended() -> None:
    storage = MemoryStorage()
    await storage.save_subscription(
        subscription(state=State.TRIAL, trial_ends_at=YESTERDAY, expires_at=None)
    )

    assert await due_users(storage) == ["user_1"]


async def test_a_running_trial_is_not_due() -> None:
    storage = MemoryStorage()
    await storage.save_subscription(
        subscription(state=State.TRIAL, trial_ends_at=TOMORROW, expires_at=None)
    )

    assert await due_users(storage) == []


async def test_an_active_subscription_is_due_once_it_expires() -> None:
    storage = MemoryStorage()
    await storage.save_subscription(subscription(state=State.ACTIVE, expires_at=YESTERDAY))

    assert await due_users(storage) == ["user_1"]


async def test_a_paid_up_subscription_is_not_due() -> None:
    storage = MemoryStorage()
    await storage.save_subscription(subscription(state=State.ACTIVE, expires_at=TOMORROW))

    assert await due_users(storage) == []


async def test_grace_is_due_by_its_own_boundary_not_by_the_expiry() -> None:
    storage = MemoryStorage()
    await storage.save_subscription(
        subscription(state=State.GRACE, expires_at=YESTERDAY, grace_days=3)
    )

    assert await due_users(storage) == []
    assert await due_users(storage, NOW + timedelta(days=3)) == ["user_1"]


async def test_a_cancelled_subscription_is_due_when_its_paid_period_runs_out() -> None:
    storage = MemoryStorage()
    await storage.save_subscription(
        subscription(state=State.CANCELLED, expires_at=YESTERDAY, cancelled_at=YESTERDAY)
    )

    assert await due_users(storage) == ["user_1"]


async def test_an_expired_subscription_is_never_due_again() -> None:
    storage = MemoryStorage()
    await storage.save_subscription(subscription(state=State.EXPIRED, expires_at=YESTERDAY))

    assert await due_users(storage) == []


async def test_a_boundary_exactly_now_has_passed() -> None:
    """The paid period is half open: at expires_at the period is over."""
    storage = MemoryStorage()
    await storage.save_subscription(subscription(state=State.ACTIVE, expires_at=NOW))

    assert await due_users(storage) == ["user_1"]


async def test_iter_due_lists_only_what_is_due() -> None:
    storage = MemoryStorage()
    await storage.save_subscription(subscription(user_id="late", expires_at=YESTERDAY))
    await storage.save_subscription(subscription(user_id="paid", expires_at=TOMORROW))

    assert await due_users(storage) == ["late"]


async def test_saving_while_iterating_is_safe() -> None:
    """tick() walks the due list and writes to it. That must not explode."""
    storage = MemoryStorage()
    await storage.save_subscription(subscription(user_id="late", expires_at=YESTERDAY))

    seen = []
    async for sub in storage.iter_due(NOW):
        seen.append(sub.user_id)
        sub.state = State.GRACE
        await storage.save_subscription(sub)
        await storage.save_subscription(subscription(user_id="newcomer", expires_at=YESTERDAY))

    assert seen == ["late"]


async def test_a_first_redemption_is_allowed() -> None:
    storage = MemoryStorage()

    assert await storage.try_redeem("WELCOME", "user_1", max_total=None, max_per_user=1) is True


async def test_the_per_user_limit_is_enforced() -> None:
    storage = MemoryStorage()

    first = await storage.try_redeem("WELCOME", "user_1", max_total=None, max_per_user=1)
    second = await storage.try_redeem("WELCOME", "user_1", max_total=None, max_per_user=1)

    assert (first, second) == (True, False)


async def test_the_per_user_limit_can_be_more_than_one() -> None:
    storage = MemoryStorage()

    results = [
        await storage.try_redeem("WELCOME", "user_1", max_total=None, max_per_user=2)
        for _ in range(3)
    ]

    assert results == [True, True, False]


async def test_one_user_hitting_their_limit_does_not_block_another() -> None:
    storage = MemoryStorage()
    await storage.try_redeem("WELCOME", "user_1", max_total=None, max_per_user=1)

    assert await storage.try_redeem("WELCOME", "user_2", max_total=None, max_per_user=1) is True


async def test_the_total_limit_is_enforced_across_users() -> None:
    storage = MemoryStorage()

    first = await storage.try_redeem("LIMITED", "user_1", max_total=2, max_per_user=1)
    second = await storage.try_redeem("LIMITED", "user_2", max_total=2, max_per_user=1)
    third = await storage.try_redeem("LIMITED", "user_3", max_total=2, max_per_user=1)

    assert (first, second, third) == (True, True, False)


async def test_a_rejected_redemption_is_not_recorded() -> None:
    """A refusal must not burn a slot, or the limit drifts down on every retry."""
    storage = MemoryStorage()
    await storage.try_redeem("LIMITED", "user_1", max_total=2, max_per_user=1)
    await storage.try_redeem("LIMITED", "user_1", max_total=2, max_per_user=1)

    assert await storage.try_redeem("LIMITED", "user_2", max_total=2, max_per_user=1) is True


async def test_limits_are_counted_per_code() -> None:
    storage = MemoryStorage()
    await storage.try_redeem("WELCOME", "user_1", max_total=1, max_per_user=1)

    assert await storage.try_redeem("SPRING", "user_1", max_total=1, max_per_user=1) is True


async def test_an_exhausted_code_stays_exhausted() -> None:
    storage = MemoryStorage()
    await storage.try_redeem("ONCE", "user_1", max_total=1, max_per_user=1)

    assert await storage.try_redeem("ONCE", "user_2", max_total=1, max_per_user=1) is False
    assert await storage.try_redeem("ONCE", "user_3", max_total=1, max_per_user=1) is False


async def test_a_referrer_has_no_program_until_one_is_assigned() -> None:
    assert await MemoryStorage().get_program_id("user_1") is None


async def test_an_assigned_program_comes_back() -> None:
    storage = MemoryStorage()

    await storage.assign_program("user_1", "bloggers")

    assert await storage.get_program_id("user_1") == "bloggers"


async def test_a_program_can_be_reassigned() -> None:
    storage = MemoryStorage()
    await storage.assign_program("user_1", "bloggers")

    await storage.assign_program("user_1", "friends")

    assert await storage.get_program_id("user_1") == "friends"


async def test_a_balance_starts_at_zero() -> None:
    assert await MemoryStorage().get_balance("user_1") == 0


async def test_accruals_accumulate() -> None:
    storage = MemoryStorage()

    await storage.add_accrual("user_1", 8970)
    await storage.add_accrual("user_1", 30)

    assert await storage.get_balance("user_1") == 9000


async def test_balances_are_kept_per_user() -> None:
    storage = MemoryStorage()

    await storage.add_accrual("user_1", 8970)

    assert await storage.get_balance("user_2") == 0


@pytest.mark.parametrize("amount", [1, 8970])
async def test_a_balance_is_an_integer_number_of_minor_units(amount: int) -> None:
    storage = MemoryStorage()

    await storage.add_accrual("user_1", amount)

    assert type(await storage.get_balance("user_1")) is int
