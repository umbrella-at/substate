"""Persistence, behind one protocol, plus the in-memory implementation.

Storage stores. It holds no rules about what a subscription may do next; that
belongs to the engine. The two methods worth reading are `iter_due`, which
keeps `tick()` from loading the whole table, and `try_redeem`, which keeps
promo limits honest by checking and recording in one call.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from substate.models import Payment, Subscription


class Storage(Protocol):
    """What the engine needs from a store. Roughly forty lines to implement."""

    async def get_subscription(self, user_id: str) -> Subscription | None:
        """The user's subscription, or None if they never had one."""
        ...

    async def save_subscription(self, subscription: Subscription) -> None:
        """Write the subscription, replacing any previous version for that user."""
        ...

    def iter_due(self, now: datetime) -> AsyncIterator[Subscription]:
        """Subscriptions whose current boundary has passed at `now`.

        `TRIAL` is measured by `trial_ends_at`, `ACTIVE` and `CANCELLED` by
        `expires_at`, `GRACE` by `grace_ends_at`; `EXPIRED` is never due. The
        signature is a stream so a SQL adapter can answer it with an index
        instead of loading the table.
        """
        ...

    async def get_payment(self, provider: str, external_id: str) -> Payment | None:
        """The payment already recorded under this pair, if any."""
        ...

    async def save_payment(self, payment: Payment) -> None:
        """Record a payment under its `(provider, external_id)` pair."""
        ...

    async def try_redeem(
        self, code: str, user_id: str, max_total: int | None, max_per_user: int
    ) -> bool:
        """Claim one redemption of `code` for `user_id`, or refuse it.

        Returns False when either limit is used up, and records nothing in that
        case. Checking and recording are one call on purpose: as two, the
        limits stop being true the day a real database is involved.
        `max_total` of None means no overall limit.
        """
        ...

    async def get_program_id(self, user_id: str) -> str | None:
        """The referral program assigned to this referrer, if any."""
        ...

    async def assign_program(self, user_id: str, program_id: str) -> None:
        """Put this referrer on a program from now on."""
        ...

    async def add_accrual(self, user_id: str, amount: int) -> None:
        """Add minor units to a referrer's balance."""
        ...

    async def get_balance(self, user_id: str) -> int:
        """A referrer's balance in minor units. Zero for anyone unheard of."""
        ...


class MemoryStorage:
    """Dictionaries. Ships with the core so the library runs with no setup.

    Subscriptions are copied on the way in and on the way out, which is what a
    real store does: a change you did not save did not happen.
    """

    def __init__(self) -> None:
        self._subscriptions: dict[str, Subscription] = {}
        self._payments: dict[tuple[str, str], Payment] = {}
        self._redemptions: dict[str, list[str]] = {}
        self._programs: dict[str, str] = {}
        self._balances: dict[str, int] = {}

    async def get_subscription(self, user_id: str) -> Subscription | None:
        stored = self._subscriptions.get(user_id)
        return None if stored is None else replace(stored)

    async def save_subscription(self, subscription: Subscription) -> None:
        self._subscriptions[subscription.user_id] = replace(subscription)

    async def iter_due(self, now: datetime) -> AsyncIterator[Subscription]:
        # Snapshot first: the caller is expected to write while walking this.
        for stored in list(self._subscriptions.values()):
            due_at = stored.due_at
            if due_at is not None and due_at <= now:
                yield replace(stored)

    async def get_payment(self, provider: str, external_id: str) -> Payment | None:
        return self._payments.get((provider, external_id))

    async def save_payment(self, payment: Payment) -> None:
        self._payments[(payment.provider, payment.external_id)] = payment

    async def try_redeem(
        self, code: str, user_id: str, max_total: int | None, max_per_user: int
    ) -> bool:
        # No await between the check and the write, so nothing interleaves here.
        redeemed = self._redemptions.setdefault(code, [])
        if max_total is not None and len(redeemed) >= max_total:
            return False
        if redeemed.count(user_id) >= max_per_user:
            return False
        redeemed.append(user_id)
        return True

    async def get_program_id(self, user_id: str) -> str | None:
        return self._programs.get(user_id)

    async def assign_program(self, user_id: str, program_id: str) -> None:
        self._programs[user_id] = program_id

    async def add_accrual(self, user_id: str, amount: int) -> None:
        self._balances[user_id] = self._balances.get(user_id, 0) + amount

    async def get_balance(self, user_id: str) -> int:
        return self._balances.get(user_id, 0)
