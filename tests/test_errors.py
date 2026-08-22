"""The exception hierarchy, which callers rely on more than they admit."""

from __future__ import annotations

import pytest

from substate import (
    AlreadySubscribed,
    DuplicatePlan,
    DuplicatePromoCode,
    DuplicateReferralProgram,
    InvalidPeriod,
    InvalidPlan,
    InvalidPromoCode,
    InvalidReferralProgram,
    NotSubscribed,
    PromoAlreadyBound,
    PromoLimitReached,
    SubstateError,
    UnknownPlan,
    UnknownPromoCode,
    UnknownReferralProgram,
)

EVERY_ERROR = [
    InvalidPlan,
    InvalidPromoCode,
    InvalidPeriod,
    InvalidReferralProgram,
    UnknownPlan,
    AlreadySubscribed,
    NotSubscribed,
    DuplicatePlan,
    DuplicatePromoCode,
    UnknownPromoCode,
    PromoLimitReached,
    PromoAlreadyBound,
    DuplicateReferralProgram,
    UnknownReferralProgram,
]

VALIDATION_ERRORS = [InvalidPlan, InvalidPromoCode, InvalidPeriod, InvalidReferralProgram]


@pytest.mark.parametrize("error", EVERY_ERROR)
def test_every_error_is_a_substate_error(error: type[SubstateError]) -> None:
    with pytest.raises(SubstateError):
        raise error("boom")


@pytest.mark.parametrize("error", VALIDATION_ERRORS)
def test_validation_errors_are_also_value_errors(error: type[SubstateError]) -> None:
    """`except ValueError` is what people write without thinking. It must work."""
    with pytest.raises(ValueError):
        raise error("boom")


@pytest.mark.parametrize("error", VALIDATION_ERRORS)
def test_validation_errors_are_still_catchable_as_substate_errors(
    error: type[SubstateError],
) -> None:
    with pytest.raises(SubstateError):
        raise error("boom")


def test_the_base_error_is_not_a_value_error() -> None:
    """Not everything that goes wrong is a bad argument."""
    assert not issubclass(SubstateError, ValueError)
    assert not issubclass(UnknownPlan, ValueError)
    assert not issubclass(AlreadySubscribed, ValueError)
    assert not issubclass(NotSubscribed, ValueError)
    assert not issubclass(DuplicatePlan, ValueError)
    assert not issubclass(DuplicatePromoCode, ValueError)
    assert not issubclass(UnknownPromoCode, ValueError)
    assert not issubclass(PromoLimitReached, ValueError)
    assert not issubclass(PromoAlreadyBound, ValueError)
    assert not issubclass(DuplicateReferralProgram, ValueError)
    assert not issubclass(UnknownReferralProgram, ValueError)


def test_errors_carry_their_message() -> None:
    assert str(InvalidPlan("grace_days must be shorter than the period")) == (
        "grace_days must be shorter than the period"
    )
