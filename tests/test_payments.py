"""The contract a payment provider fills in."""

from __future__ import annotations

import pytest

from substate import ParsedPayment, Payment, PaymentWebhook

INVOICE = Payment(provider="fake", external_id="inv_1", user_id="user_1", amount=5_000_000)


class FakeWebhook:
    """The forty lines the README promises, minus the signature check."""

    def parse(self, body: bytes, signature: str) -> ParsedPayment:
        if signature != "ok":
            raise AssertionError("the real ones raise InvalidSignature here")
        return ParsedPayment(payment=INVOICE, currency=body.decode())


def test_an_implementation_satisfies_the_protocol() -> None:
    webhook: PaymentWebhook = FakeWebhook()

    parsed = webhook.parse(b"USDT", "ok")

    assert parsed.payment == INVOICE
    assert parsed.currency == "USDT"


def test_the_currency_travels_beside_the_payment_not_inside_it() -> None:
    """Payment has no currency field, and the core is never given one."""
    parsed = ParsedPayment(payment=INVOICE, currency="USDT")

    assert not hasattr(parsed.payment, "currency")
    assert parsed.currency == "USDT"


def test_a_parsed_payment_is_frozen() -> None:
    parsed = ParsedPayment(payment=INVOICE, currency="USDT")

    with pytest.raises(AttributeError):
        parsed.currency = "TON"  # type: ignore[misc]


def test_parsed_payments_compare_by_value() -> None:
    assert ParsedPayment(payment=INVOICE, currency="USDT") == ParsedPayment(
        payment=INVOICE, currency="USDT"
    )
    assert ParsedPayment(payment=INVOICE, currency="USDT") != ParsedPayment(
        payment=INVOICE, currency="TON"
    )
