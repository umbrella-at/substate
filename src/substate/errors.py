"""Every failure this package raises deliberately.

`InvalidPlan`, `InvalidPromoCode` and `InvalidPeriod` are also `ValueError`s:
they report a bad argument, and callers should not have to learn a new base
class to catch one.
"""

from __future__ import annotations


class SubstateError(Exception):
    """Base class for everything substate raises on purpose."""


class InvalidPlan(SubstateError, ValueError):
    """A plan that cannot be honoured, such as a grace longer than its period."""


class InvalidPromoCode(SubstateError, ValueError):
    """A promo code whose value makes no sense for its kind."""


class InvalidPeriod(SubstateError, ValueError):
    """A period of zero or negative length."""


class UnknownPlan(SubstateError):
    """No plan is registered under this id."""


class AlreadySubscribed(SubstateError):
    """The user already holds a live subscription."""
