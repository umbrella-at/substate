# substate

Subscription lifecycle for Python: trials, renewals, grace periods, promo codes and referrals, lifted out of your application into a core you can actually test.

[![CI](https://github.com/umbrella-at/substate/actions/workflows/ci.yml/badge.svg)](https://github.com/umbrella-at/substate/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

<!-- PyPI badges go back in with the v0.1.0 release -->

<!-- TODO before release: asciinema cast of the test suite fast-forwarding a year of subscriptions in ~200ms -->

## Why

Wrappers around payment providers are everywhere. None of them solve the part that actually breaks: **subscription state over time.**

A trial that converts. A renewal that lands three days late. A grace period that ends on a Sunday. A monthly plan billed on the 31st. A webhook delivered twice.

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
from substate import SubscriptionEngine, MemoryStorage, FrozenClock, Plan, Payment, Period

async def main():
    clock = FrozenClock("2026-01-01")   # the real clock in production;
                                        # frozen here so the output is exact
    engine = SubscriptionEngine(storage=MemoryStorage(), clock=clock)

    engine.register_plan(Plan(
        id="pro_month",
        price=5_000_000,          # 5.00 USDT, minor units
        currency="USDT",
        period=Period.days(30),
        trial_days=3,
    ))

    sub = await engine.subscribe("user_1", "pro_month")
    print(sub.state, sub.access_until)    # State.TRIAL 2026-01-04

    await engine.apply_payment(Payment(
        provider="cryptobot",
        external_id="inv_12345",          # calling this twice changes nothing
        user_id="user_1",
        amount=5_000_000,
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
- a plan change requested on the last day of a paid period
- a renewal on the 31st when the next month has 30 days
- a promo code that outlives the subscription it was applied to
- the same webhook delivered twice, four seconds apart

## State machine

```
  subscribe ──> TRIAL ─────────────────────────> EXPIRED
                  │                                 ▲
          payment │                                 │
                  ▼                                 │
                ACTIVE ─────────> GRACE ────────────┘
                  ▲                 │
                  └─────────────────┘
                        payment
```

A trial that never converts goes straight to `EXPIRED`. Grace is a courtesy to a
paying customer, not a free trial extension.

Inside grace the clock keeps running: paying on day three of a grace period
renews from the old `expires_at`, so those three days are spent. After expiry the
rule flips and the new period starts from the payment date, because a lapsed
subscription renewed from a two-month-old date would land entirely in the past.

`CANCELLED` is reachable from any state and keeps access until `expires_at`.

Transitions are driven by six calls and the clock: `subscribe()`, `apply_payment()`,
`redeem()`, `change_plan()`, `cancel()` and `tick()`. Nothing changes state behind your
back.

## Checking access

```python
if await engine.is_active("user_1"):
    ...
```

`engine.is_active()` compares the subscription's boundary against the injected clock,
so it is correct the moment a period ends rather than the next time `tick()` runs.
That gap is not theoretical: `tick()` usually runs from cron every few minutes, and
without this a cancelled subscription would keep access until the next run.

`Subscription.is_active` also exists, as a pure predicate over state with no notion
of time, and `Subscription.access_until` gives the boundary for whatever state the
subscription is in. Reach for the engine method unless you specifically want the
state check.

## Events

The core knows nothing about notifications. It emits events, your application decides what to do with them.

```
subscription.created        subscription.activated
subscription.renewed        subscription.entering_grace
subscription.expired        subscription.cancelled
subscription.plan_changed

promo.redeemed              referral.accrued

payment.recorded            payment.duplicate
payment.underpaid           payment.unmatched
```

Every event carries `user_id` and an `occurred_at` set to the moment the transition
logically happened, not the moment you called into the engine. Fast-forward a month
and the grace period still starts on the day it was due.

Pass a sink and it sees everything, including events from calls that hand back a
subscription rather than a list:

```python
journal: list[Event] = []
engine = SubscriptionEngine(storage, clock=clock, on_event=journal.append)
```

The sink runs after the new state has been persisted, so a failing notification
cannot roll back a transition. Keep it cheap: push onto a queue, do not do IO in it.

`apply_payment()` and `tick()` also return their events directly, because a payment
or a jump in the clock can set off a chain the caller cannot predict. `subscribe()`,
`cancel()` and `change_plan()` return the subscription instead: they produce at most
one event of their own, and you already know which one. The sink may additionally
see boundaries the call caught up on.

## Money

Integers in minor units, everywhere, including discount math. No floats, no `Decimal` surprises. Rounding is explicit and covered by a test.

A plan is priced in the unit its provider actually pays in — USDT through CryptoBot, XTR through Stars — and nothing is ever converted. `Plan.currency` is a label the core does not interpret, the scale behind it belongs to the adapter, and a second provider means a second plan rather than an exchange rate.

## Promo codes

Three kinds. `PERCENT` and `FIXED` attach to the subscription and come off the
payments that follow; `PLUS_DAYS` is spent the moment it is claimed and moves
whichever boundary the subscription is running on.

```python
engine.register_promo_code(PromoCode(
    code="SORRY",
    kind=PromoKind.PLUS_DAYS,
    value=3,
))

# the service was down for a day: give everyone their three days back
for user_id in affected:
    await engine.redeem(user_id, "SORRY")
```

That is what `redeem()` exists for. Without it there is no way to extend a
subscription that is already running, which is the one thing you need at the
moment you least want to be writing code.

Limits (`max_redemptions`, `max_per_user`) count redemptions rather than
payments, and they are checked atomically: somebody who claims a code and never
pays has still spent their slot.

## Referrals come with programs

Most referral code hardcodes one percentage. Real programs are not one percentage:
a blogger you signed a deal with takes a cut of every renewal, someone who shared a
link with a friend gets paid once, on the first payment.

That is two parameters differing by group, so they live in a program rather than in
an `if`:

```python
engine.register_referral_program(ReferralProgram(
    id="bloggers",
    percent=30,
    accrual=Accrual.EVERY_PAYMENT,
))
await engine.assign_program("user_42", "bloggers")
```

Attribution is recorded once, at `subscribe()`, and never moves. Accrual is computed
from money actually received, in minor units. A trial that never converted pays
nobody. One level only, by design.

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

## Taking money

An adapter turns a signed webhook into a payment. It creates no invoices and
opens no sockets:

```python
from substate.adapters.cryptobot import CryptoBotWebhook

webhook = CryptoBotWebhook(token=CRYPTO_PAY_TOKEN)

parsed = webhook.parse(body, signature)      # raw request bytes, header value
events = await engine.apply_payment(parsed.payment)
```

`body` must be the bytes the socket delivered: the signature covers those, and a
framework that parses the JSON and serialises it again will break the check.
Amounts are read into minor units with `Decimal`, never `float`, and the invoice
must carry the subscriber's id in its `payload`.

Answer an `AdapterError` with 400 and do not retry it — a body that will never
verify will never verify — and keep 500 for your own failures, which are worth
retrying.

## What substate does not do

Deliberately, so the scope stays small enough to finish:

- **It is not a payment gateway.** It never moves money. It records that money moved.
- **No UI, no admin panel.** It is a library.
- **No framework coupling.** No aiogram, no FastAPI, no Django. Bring your own.
- **No background scheduler.** You call `tick()` from your own cron, worker or startup hook.
- **No proration.** A plan change takes effect at the end of the paid period, and moves no money. Prorated switching is v0.2.
- **No currency reconciliation.** A plan is denominated in one unit, the payment arrives in that
  same unit, and nothing checks one against the other because there is nothing to check. Taking a
  second provider means registering a second plan.
- **No refunds or chargebacks.**
- **No invoicing, receipts, tax or accounting.**

## License

MIT
