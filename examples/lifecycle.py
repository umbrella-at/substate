"""A subscription from trial to expiry, without waiting a month to watch it.

    python examples/lifecycle.py

No services, no database, no network: the clock is a parameter, and that is the
whole point of the library.
"""

import asyncio

from substate import Event, FrozenClock, MemoryStorage, Payment, Period, Plan, SubscriptionEngine


def show(event: Event) -> None:
    print(f"{event.occurred_at:%Y-%m-%d}  {event.name}")


async def main() -> None:
    clock = FrozenClock("2026-01-01")
    engine = SubscriptionEngine(storage=MemoryStorage(), clock=clock, on_event=show)
    engine.register_plan(
        Plan(
            id="pro",
            price=5_000_000,  # 5.00 USDT, minor units
            currency="USDT",
            period=Period.days(30),
            trial_days=3,
            grace_days=5,
        )
    )

    await engine.subscribe("user_1", "pro")
    await engine.apply_payment(
        Payment(provider="cryptobot", external_id="inv_1", user_id="user_1", amount=5_000_000)
    )

    clock.advance(days=33)  # the paid period ended three days ago
    await engine.tick()
    print(f"inside grace, access: {await engine.is_active('user_1')}")

    clock.advance(days=5)  # the courtesy runs out
    await engine.tick()
    print(f"after grace, access:  {await engine.is_active('user_1')}")


if __name__ == "__main__":
    asyncio.run(main())
