# substate

Subscription lifecycle for Python: trials, renewals, grace periods, promo codes and referrals, lifted out of your application into a core you can actually test.

[![CI](https://github.com/umbrella-at/substate/actions/workflows/ci.yml/badge.svg)](https://github.com/umbrella-at/substate/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/substate)](https://pypi.org/project/substate/)
[![Python](https://img.shields.io/pypi/pyversions/substate)](https://pypi.org/project/substate/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

<!-- TODO before release: asciinema cast of the test suite fast-forwarding a year of subscriptions in ~200ms -->

## Why

Wrappers around payment providers are everywhere. None of them solve the part that actually breaks: **subscription state over time.**

A trial that converts. A renewal that lands three days late. A grace period that ends on a Sunday. A plan change halfway through a paid month. A refund. A webhook delivered twice.

Most codebases handle this with `if user.expires_at < datetime.now()` sprinkled across a dozen handlers, and nobody tests it, because you cannot fast-forward a month in a test.

substate is that state machine, isolated, provider-agnostic and covered by tests.

## Install

```bash
pip install substate
```

Zero required dependencies. Payment and storage adapters are optional extras.

## Quickstart

```python
import asyncio
from substate import SubscriptionEngine, MemoryStorage, Plan, Payment, Period

async def main():
    engine = SubscriptionEngine(storage=MemoryStorage())

    engine.register_plan(Plan(
        id="pro_month",
        price=29900,              # minor units, always integers
        currency="RUB",
        period=Period.days(30),
        trial_days=3,
    ))

    sub = await engine.subscribe("user_1", "pro_month")
    print(sub.state, sub.expires_at)      # State.TRIAL 2026-01-04

    await engine.apply_payment(Payment(
        provider="cryptobot",
        external_id="inv_12345",          # calling this twice changes nothing
        user_id="user_1",
        amount=29900,
    ))

    sub = await engine.get_subscription("user_1")
    print(sub.state, sub.expires_at)      # State.ACTIVE 2026-02-03

    for event in await engine.tick():     # advance the world, collect what happened
        print(event)

asyncio.run(main())
```

## The clock is injectable, and that is the whole point

`datetime.now()` does not appear anywhere in the core. Every date comes from a clock you pass in.

```python
from substate import SubscriptionEngine, MemoryStorage, FrozenClock

clock = FrozenClock("2026-01-01")
engine = SubscriptionEngine(storage=MemoryStorage(), clock=clock)

await engine.subscribe("user_1", "pro_month")   # TRIAL, 3 days

clock.advance(days=4)
events = await engine.tick()
# [SubscriptionExpired(user_id='user_1', reason='trial_not_converted')]
```

In production you pass the real clock and never think about it again. In tests you fast-forward a year in milliseconds, which means the awkward cases are covered by actual tests rather than a comment saying it should probably work:

- payment arriving in the middle of a grace period
- plan change on the last day of a paid period
- a renewal on the 31st when the next month has 30 days
- a promo code that outlives the subscription it was applied to
- the same webhook delivered twice, four seconds apart

## State machine

```
              ┌──────────────────────────────────┐
              ▼                                  │
  subscribe ──> TRIAL ──> ACTIVE ──> GRACE ──> EXPIRED
                            ▲          │
                            └──────────┘
                             payment

  CANCELLED is reachable from any state.
```

Transitions are driven by two things only: `apply_payment()` and `tick()`. Nothing changes state behind your back.

## Events

The core knows nothing about notifications. It emits events, your application decides what to do with them.

```
subscription.activated      subscription.renewed
subscription.entering_grace subscription.expired
promo.redeemed              referral.accrued
```

## Money

Integers in minor units (kopecks, cents), everywhere, including discount math. No floats, no `Decimal` surprises. Rounding is explicit and covered by a test.

## Adapters

Payments, behind one protocol:

| Adapter | Status |
|---|---|
| CryptoBot | v0.1 |
| Telegram Stars | planned |
| YooKassa | planned |

Storage, behind one protocol:

| Adapter | Status |
|---|---|
| In-memory | v0.1, ships with the core |
| SQLAlchemy (async) | planned |

Both are plain protocols. Writing your own is roughly forty lines.

## What substate does not do

Deliberately, so the scope stays small enough to finish:

- **It is not a payment gateway.** It never moves money. It records that money moved.
- **No UI, no admin panel.** It is a library.
- **No framework coupling.** No aiogram, no FastAPI, no Django. Bring your own.
- **No background scheduler.** You call `tick()` from your own cron, worker or startup hook.
- **No invoicing, receipts, tax or accounting.**

## License

MIT
