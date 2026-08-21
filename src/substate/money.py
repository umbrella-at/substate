"""Discount arithmetic.

Integers in minor units, floor division, and one rounding rule shared by
discounts and referral accrual so both sides of the ledger agree.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Discount:
    """What a promo takes off, and what is left to pay.

    `discount + final == price` holds for every input, which is the whole
    reason this is a type and not a pair of loose integers.
    """

    discount: int
    final: int


def percent_of(amount: int, percent: int) -> int:
    """`percent` of `amount`, floored. The single rounding rule in the package."""
    if amount < 0:
        raise ValueError(f"amount must not be negative, got {amount}")
    if not 0 <= percent <= 100:
        raise ValueError(f"percent must be between 0 and 100, got {percent}")
    return (amount * percent) // 100


def percent_discount(price: int, percent: int) -> Discount:
    """Take `percent` off `price`, rounding the discount down.

    Flooring the discount rounds the remainder towards the seller, which is a
    choice rather than an accident: the alternative loses a minor unit.
    """
    discount = percent_of(price, percent)
    return Discount(discount=discount, final=price - discount)


def fixed_discount(price: int, value: int) -> Discount:
    """Take a fixed amount off `price`, clamped so nothing goes negative.

    A promo code is written before the price it will meet is known, so a code
    worth more than the plan is ordinary, not an error. It caps at the price.
    """
    if price < 0:
        raise ValueError(f"price must not be negative, got {price}")
    if value < 0:
        raise ValueError(f"a fixed discount must not be negative, got {value}")
    effective = min(value, price)
    return Discount(discount=effective, final=price - effective)
