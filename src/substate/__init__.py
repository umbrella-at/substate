"""Subscription lifecycle for Python.

The public API is intentionally small. See README.md for the shape it will take.
"""

from substate.clock import Clock, FrozenClock, SystemClock
from substate.errors import (
    AlreadySubscribed,
    InvalidPeriod,
    InvalidPlan,
    InvalidPromoCode,
    SubstateError,
    UnknownPlan,
)
from substate.models import (
    Accrual,
    Payment,
    Plan,
    PromoCode,
    PromoKind,
    PromoScope,
    ReferralProgram,
    ScopeKind,
    State,
    Subscription,
)
from substate.money import Discount, fixed_discount, percent_discount, percent_of
from substate.periods import Period, PeriodUnit

__all__ = [
    "Accrual",
    "AlreadySubscribed",
    "Clock",
    "Discount",
    "FrozenClock",
    "InvalidPeriod",
    "InvalidPlan",
    "InvalidPromoCode",
    "Payment",
    "Period",
    "PeriodUnit",
    "Plan",
    "PromoCode",
    "PromoKind",
    "PromoScope",
    "ReferralProgram",
    "ScopeKind",
    "State",
    "Subscription",
    "SubstateError",
    "SystemClock",
    "UnknownPlan",
    "__version__",
    "fixed_discount",
    "percent_discount",
    "percent_of",
]

__version__ = "0.1.0.dev0"
