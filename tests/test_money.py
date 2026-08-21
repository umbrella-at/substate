"""Money is integers in minor units. These tests exist to keep it that way."""

from __future__ import annotations

import pytest

from substate import Discount, fixed_discount, percent_discount, percent_of

PRICES = [0, 1, 7, 99, 100, 101, 999, 1234, 29999, 100_000, 123_457]
PERCENTS = [0, 1, 3, 7, 33, 50, 66, 99, 100]


def test_the_remainder_case_from_the_spec() -> None:
    result = percent_discount(29999, 33)

    assert result.discount == 9899
    assert result.final == 20100


@pytest.mark.parametrize("price", PRICES)
@pytest.mark.parametrize("percent", PERCENTS)
def test_a_percent_discount_never_loses_a_minor_unit(price: int, percent: int) -> None:
    result = percent_discount(price, percent)

    assert result.discount + result.final == price
    assert 0 <= result.discount <= price
    assert result.final >= 0


@pytest.mark.parametrize("price", PRICES)
@pytest.mark.parametrize("percent", PERCENTS)
def test_a_percent_discount_stays_integral(price: int, percent: int) -> None:
    """No float ever touches the money, so nothing can arrive as 20099.999999."""
    result = percent_discount(price, percent)

    assert type(result.discount) is int
    assert type(result.final) is int


def test_rounding_favours_the_seller() -> None:
    """Half a kopeck of discount is not a discount."""
    assert percent_discount(101, 50) == Discount(discount=50, final=51)
    assert percent_discount(1, 99) == Discount(discount=0, final=1)


def test_a_zero_percent_code_is_a_no_op() -> None:
    assert percent_discount(29900, 0) == Discount(discount=0, final=29900)


def test_a_hundred_percent_code_makes_it_free() -> None:
    assert percent_discount(29900, 100) == Discount(discount=29900, final=0)


def test_a_free_plan_stays_free() -> None:
    assert percent_discount(0, 50) == Discount(discount=0, final=0)


def test_a_fixed_discount_subtracts_its_value() -> None:
    assert fixed_discount(29900, 5000) == Discount(discount=5000, final=24900)


def test_a_fixed_discount_larger_than_the_price_is_clamped() -> None:
    """The promo was written before anyone knew the price. It caps, it never owes."""
    result = fixed_discount(29900, 50000)

    assert result.discount == 29900
    assert result.final == 0


@pytest.mark.parametrize("price", PRICES)
@pytest.mark.parametrize("value", [0, 1, 99, 29999, 50_000, 1_000_000])
def test_a_clamped_fixed_discount_keeps_the_invariant(price: int, value: int) -> None:
    result = fixed_discount(price, value)

    assert result.discount + result.final == price
    assert result.final >= 0
    assert type(result.discount) is int
    assert type(result.final) is int


def test_percent_of_is_the_one_rounding_rule() -> None:
    """Referral accrual reuses it, so both sides of the ledger round the same way."""
    assert percent_of(29999, 33) == 9899
    assert percent_of(29900, 30) == 8970
    assert percent_of(1, 50) == 0


@pytest.mark.parametrize("percent", [-1, 101, 1000])
def test_a_percent_outside_the_scale_is_rejected(percent: int) -> None:
    with pytest.raises(ValueError):
        percent_discount(29900, percent)

    with pytest.raises(ValueError):
        percent_of(29900, percent)


def test_a_negative_price_is_rejected() -> None:
    with pytest.raises(ValueError):
        percent_discount(-1, 50)

    with pytest.raises(ValueError):
        fixed_discount(-1, 50)


def test_a_negative_fixed_value_is_rejected() -> None:
    with pytest.raises(ValueError):
        fixed_discount(29900, -1)


def test_a_discount_is_frozen() -> None:
    result = percent_discount(29900, 10)

    with pytest.raises(AttributeError):
        result.final = 0  # type: ignore[misc]
