"""The CryptoBot webhook adapter, on fixtures. Nothing here touches a network."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest

from substate import (
    InvalidSignature,
    InvalidWebhook,
    ParsedPayment,
    Payment,
    PaymentWebhook,
    UnsupportedAsset,
)
from substate.adapters.cryptobot import USDT_MINOR_UNITS, CryptoBotWebhook

TOKEN = "12345:AAbbCCddEEff"
_MISSING = object()


def sign(body: bytes, token: str = TOKEN) -> str:
    secret = hashlib.sha256(token.encode()).digest()
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def body(update_type: str = "invoice_paid", **invoice: Any) -> bytes:
    """A webhook shaped like the ones Crypto Pay sends."""
    paid: dict[str, Any] = {
        "invoice_id": 528890,
        "status": "paid",
        "hash": "IVjfmoNBAvbT",
        "asset": "USDT",
        "amount": "5.5",
        "payload": "user_1",
        "paid_at": "2026-01-01T10:00:00.000Z",
    }
    paid.update(invoice)
    update = {
        "update_id": 1,
        "update_type": update_type,
        "request_date": "2026-01-01T10:00:00.000Z",
        "payload": paid,
    }
    return json.dumps(update).encode()


def parse(raw: bytes, signature: Any = _MISSING) -> ParsedPayment:
    return CryptoBotWebhook(TOKEN).parse(raw, sign(raw) if signature is _MISSING else signature)


def test_the_adapter_satisfies_the_protocol() -> None:
    webhook: PaymentWebhook = CryptoBotWebhook(TOKEN)

    assert isinstance(webhook.parse(body(), sign(body())), ParsedPayment)


def test_a_paid_invoice_becomes_a_payment() -> None:
    parsed = parse(body())

    assert parsed.payment.provider == "cryptobot"
    assert parsed.payment.external_id == "528890"
    assert parsed.payment.user_id == "user_1"
    assert parsed.payment.amount == 5_500_000
    assert parsed.currency == "USDT"


def test_the_scale_is_declared_and_is_six_decimal_places() -> None:
    assert USDT_MINOR_UNITS == 1_000_000


@pytest.mark.parametrize(
    ("amount", "minor_units"),
    [
        ("5.5", 5_500_000),
        ("5", 5_000_000),
        ("0.000001", 1),
        ("0", 0),
        ("12.345678", 12_345_678),
        ("2.01", 2_010_000),  # float(2.01) * 1e6 truncates to 2009999
        ("1.001", 1_001_000),  # float(1.001) * 1e6 truncates to 1000999
        ("1234.567891", 1_234_567_891),
    ],
)
def test_amounts_are_read_exactly(amount: str, minor_units: int) -> None:
    """The float route loses a minor unit on ordinary prices. This one does not."""
    assert parse(body(amount=amount)).payment.amount == minor_units


@pytest.mark.parametrize("amount", ["2.01", "1.001", "4.02", "2.03"])
def test_the_float_route_would_have_been_wrong(amount: str) -> None:
    """A guard on the guard: these inputs really do break under float."""
    assert int(float(amount) * USDT_MINOR_UNITS) != parse(body(amount=amount)).payment.amount


def test_an_amount_sent_as_a_json_number_is_still_exact() -> None:
    raw = json.dumps(
        {
            "update_id": 1,
            "update_type": "invoice_paid",
            "request_date": "2026-01-01T10:00:00.000Z",
            "payload": {
                "invoice_id": 7,
                "status": "paid",
                "asset": "USDT",
                "amount": 2.01,
                "payload": "user_1",
            },
        }
    ).encode()

    assert parse(raw).payment.amount == 2_010_000


def test_an_amount_finer_than_the_scale_is_refused() -> None:
    """Six decimals is the unit. Anything past it would have to be rounded away."""
    with pytest.raises(InvalidWebhook):
        parse(body(amount="0.0000005"))


def test_a_negative_amount_is_refused() -> None:
    with pytest.raises(InvalidWebhook):
        parse(body(amount="-1"))


@pytest.mark.parametrize("amount", ["", "abc", "5,5", None, [], {}, True])
def test_an_unreadable_amount_is_refused(amount: Any) -> None:
    with pytest.raises(InvalidWebhook):
        parse(body(amount=amount))


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-Infinity", "sNaN"])
def test_an_amount_that_is_not_a_number_is_refused(amount: str) -> None:
    """Decimal reads these happily. An adapter that then calls int() on one crashes."""
    with pytest.raises(InvalidWebhook):
        parse(body(amount=amount))


def test_exponent_notation_is_read_as_the_number_it_is() -> None:
    assert parse(body(amount="1E3")).payment.amount == 1_000_000_000


def test_a_wrong_signature_is_refused() -> None:
    with pytest.raises(InvalidSignature):
        parse(body(), "0" * 64)


@pytest.mark.parametrize("signature", [None, b"abc", 12345, ""])
def test_a_signature_that_is_not_a_hex_string_is_refused(signature: Any) -> None:
    """A missing header reaches here as None, and that is a refusal, not a crash."""
    with pytest.raises(InvalidSignature):
        parse(body(), signature)


def test_a_signature_from_another_token_is_refused() -> None:
    raw = body()

    with pytest.raises(InvalidSignature):
        parse(raw, sign(raw, token="99999:someone-else"))


def test_a_tampered_body_is_refused() -> None:
    """The signature covers the bytes, so changing the amount invalidates it."""
    honest = body(amount="5.5")
    tampered = body(amount="9999")

    with pytest.raises(InvalidSignature):
        parse(tampered, sign(honest))


def test_a_re_encoded_body_no_longer_matches() -> None:
    """Documented trap: pass the bytes the socket gave you, not a re-serialised dict."""
    raw = body()
    re_encoded = json.dumps(json.loads(raw), indent=2).encode()

    with pytest.raises(InvalidSignature):
        parse(re_encoded, sign(raw))


def test_a_malformed_body_is_refused() -> None:
    with pytest.raises(InvalidWebhook):
        parse(b"{not json")


def test_a_body_that_is_not_an_update_is_refused() -> None:
    with pytest.raises(InvalidWebhook):
        parse(b"[]")


def test_an_update_that_is_not_a_payment_is_refused() -> None:
    with pytest.raises(InvalidWebhook):
        parse(body(update_type="invoice_expired"))


def test_an_invoice_that_was_not_paid_is_refused() -> None:
    with pytest.raises(InvalidWebhook):
        parse(body(status="active"))


def test_an_invoice_without_an_id_is_refused() -> None:
    raw = json.dumps(
        {
            "update_id": 1,
            "update_type": "invoice_paid",
            "payload": {"status": "paid", "asset": "USDT", "amount": "5.5", "payload": "user_1"},
        }
    ).encode()

    with pytest.raises(InvalidWebhook):
        parse(raw)


@pytest.mark.parametrize("payload", ["", None])
def test_an_invoice_without_a_user_is_refused(payload: Any) -> None:
    """The application puts the user id in the invoice payload. Nothing else can."""
    with pytest.raises(InvalidWebhook):
        parse(body(payload=payload))


def test_two_invoices_for_one_user_are_two_payments() -> None:
    """Idempotency keys off the invoice id, never off the payload."""
    first = parse(body(invoice_id=1)).payment
    second = parse(body(invoice_id=2)).payment

    assert first.user_id == second.user_id
    assert first.external_id != second.external_id


def test_an_asset_that_is_not_usdt_is_refused() -> None:
    with pytest.raises(UnsupportedAsset) as refusal:
        parse(body(asset="TON"))

    assert "TON" in str(refusal.value)


def test_the_currency_is_handed_over_not_checked() -> None:
    """The engine never sees it: comparing it with a plan is the application's business."""
    parsed = parse(body())

    assert parsed.currency == "USDT"
    assert not hasattr(parsed.payment, "currency")


def test_an_uppercase_signature_is_accepted() -> None:
    """Hex case carries no information, and the header's case is not guaranteed."""
    raw = body()

    assert parse(raw, sign(raw).upper()).payment.amount == 5_500_000


def test_a_fiat_invoice_is_refused_by_asset_not_by_shape() -> None:
    """Fiat invoices carry `fiat` instead of `asset`. That is an unsupported unit."""
    raw = json.dumps(
        {
            "update_id": 1,
            "update_type": "invoice_paid",
            "payload": {
                "invoice_id": 9,
                "status": "paid",
                "currency_type": "fiat",
                "fiat": "EUR",
                "amount": "5.5",
                "payload": "user_1",
            },
        }
    ).encode()

    with pytest.raises(UnsupportedAsset) as refusal:
        parse(raw)

    assert "EUR" in str(refusal.value)


def test_an_invoice_settled_in_another_asset_is_refused() -> None:
    """An invoice priced in USDT but settled in TON is a conversion this pass cannot read."""
    with pytest.raises(UnsupportedAsset) as refusal:
        parse(body(paid_asset="TON", paid_amount="1.7"))

    assert "TON" in str(refusal.value)


def test_an_invoice_settled_in_usdt_reads_normally() -> None:
    assert parse(body(paid_asset="USDT", paid_amount="5.5")).payment.amount == 5_500_000


def test_a_fiat_invoice_settled_in_usdt_is_still_refused() -> None:
    """The trap: the fiat amount is not the USDT amount, and 5.50 EUR is not 5.50 USDT.

    A fiat invoice prices in fiat and settles in crypto, so its `amount` and
    its `paid_amount` are different numbers in different units. Reading the
    first one as USDT would book a payment that never happened.
    """
    raw = json.dumps(
        {
            "update_id": 1,
            "update_type": "invoice_paid",
            "payload": {
                "invoice_id": 77,
                "status": "paid",
                "currency_type": "fiat",
                "fiat": "EUR",
                "amount": "5.5",
                "paid_asset": "USDT",
                "paid_amount": "6.02",
                "payload": "user_1",
            },
        }
    ).encode()

    with pytest.raises(UnsupportedAsset) as refusal:
        parse(raw)

    assert "EUR" in str(refusal.value)


def test_a_realistic_invoice_from_the_wire() -> None:
    """The full shape a paid invoice arrives in, extra fields and all."""
    raw = json.dumps(
        {
            "update_id": 12345,
            "update_type": "invoice_paid",
            "request_date": "2026-08-22T10:15:32.345Z",
            "payload": {
                "invoice_id": 528890,
                "hash": "IVjfWDx1LOMx",
                "currency_type": "crypto",
                "asset": "USDT",
                "amount": "10",
                "paid_asset": "USDT",
                "paid_amount": "10",
                "fee_asset": "USDT",
                "fee_amount": 0.03,
                "description": "Pro subscription, 1 month",
                "status": "paid",
                "created_at": "2026-08-22T10:14:03.123Z",
                "paid_at": "2026-08-22T10:15:31.987Z",
                "payload": "user_42",
            },
        }
    ).encode()

    parsed = parse(raw)

    assert parsed.payment == Payment(
        provider="cryptobot", external_id="528890", user_id="user_42", amount=10_000_000
    )
    assert parsed.currency == "USDT"


def test_an_update_whose_invoice_is_not_an_object_is_refused() -> None:
    raw = json.dumps({"update_id": 1, "update_type": "invoice_paid", "payload": "nope"}).encode()

    with pytest.raises(InvalidWebhook):
        parse(raw)


def test_an_invoice_naming_no_unit_at_all_is_refused() -> None:
    raw = json.dumps(
        {
            "update_id": 1,
            "update_type": "invoice_paid",
            "payload": {"invoice_id": 3, "status": "paid", "amount": "5.5", "payload": "user_1"},
        }
    ).encode()

    with pytest.raises(InvalidWebhook):
        parse(raw)
