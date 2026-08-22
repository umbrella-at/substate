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


class InvalidReferralProgram(SubstateError, ValueError):
    """A referral program whose percentage is off the scale."""


class UnknownPlan(SubstateError):
    """No plan is registered under this id."""


class DuplicatePlan(SubstateError):
    """A plan is already registered under this id.

    Plans are immutable for the life of an engine. Changing one means building
    a new engine, which is a restart, not a silent price change under a
    subscription that is already running.
    """


class AlreadySubscribed(SubstateError):
    """The user already holds a live subscription."""


class NotSubscribed(SubstateError):
    """The user has no subscription at all, so there is nothing to act on."""


class DuplicatePromoCode(SubstateError):
    """A promo code is already registered under this code."""


class UnknownPromoCode(SubstateError):
    """No promo code is registered under this code."""


class PromoLimitReached(SubstateError):
    """The code is real, but its redemptions are used up."""


class PromoAlreadyBound(SubstateError):
    """A discount is already attached to this subscription.

    Two discounts on one payment have no defined winner, so the second one is
    refused rather than silently overwriting the first.
    """


class DuplicateReferralProgram(SubstateError):
    """A referral program is already registered under this id."""


class UnknownReferralProgram(SubstateError):
    """No referral program is registered under this id."""
