"""CryptoBot (Crypto Pay) webhooks, turned into payments.

This is not a client. It creates no invoices and opens no sockets: it takes the
bytes of a webhook request and the signature that came with them, and hands
back a payment the core understands.

**Scale.** Amounts arrive as decimal USDT — `"5.5"` means five and a half USDT.
This adapter multiplies by `USDT_MINOR_UNITS`, which is 1_000_000: one USDT is
a million minor units, six decimal places. A plan priced for CryptoBot is
priced in those units, so 5.00 USDT is `price=5_000_000`.

**Only USDT.** Every asset has its own precision, providers change the list,
and a quietly wrong amount in a ledger is worse than a loud refusal. Another
asset raises `UnsupportedAsset`; adding one is a deliberate edit with a test,
not a guess. That covers fiat invoices, which carry no asset at all, and
invoices settled in a second asset, whose conversion this adapter cannot read.

**The amount is the invoice's own.** Crypto Pay's fee is not deducted here and
neither is anything else: the payment is what the invoice was raised for.

**No float, at any step.** `float("2.01") * 1_000_000` is 2009999.9999999998,
and a minor unit disappears. The JSON is parsed with `Decimal` in place of
`float` and the arithmetic stays exact from the wire to the integer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal, InvalidOperation
from typing import Any

from substate.errors import InvalidSignature, InvalidWebhook, UnsupportedAsset
from substate.models import Payment
from substate.payments import ParsedPayment

#: Minor units in one USDT: six decimal places.
USDT_MINOR_UNITS = 1_000_000

#: The only asset this adapter knows the scale of.
SUPPORTED_ASSET = "USDT"

#: The header Crypto Pay puts the signature in.
SIGNATURE_HEADER = "crypto-pay-api-signature"

#: The update this adapter reads. Anything else is not a payment.
PAID_UPDATE = "invoice_paid"

PROVIDER = "cryptobot"


class CryptoBotWebhook:
    """Reads CryptoBot webhooks with a Crypto Pay app token.

    The token is the one from `@CryptoBot` → `/pay` → Create App, the same
    value the API expects in `Crypto-Pay-API-Token`. A Telegram bot token is a
    different thing and will not verify anything.

    The application must put the subscriber's id in the invoice `payload` when
    it creates the invoice: it is the only thing tying a payment to a user, and
    an invoice without one is refused. The idempotency key is the invoice id,
    not the payload, so two invoices for one user are two payments, and not the
    update id either, which Crypto Pay documents as non-unique.

    Answer a refusal from here with 400: Crypto Pay retries a delivery that
    failed, and after enough failures it turns the webhook off until someone
    switches it back on by hand. A body it will never sign correctly is not
    worth that.
    """

    __slots__ = ("_secret",)

    def __init__(self, token: str) -> None:
        self._secret = hashlib.sha256(token.encode()).digest()

    def parse(self, body: bytes, signature: str) -> ParsedPayment:
        """Verify the signature over `body` and read the payment out of it.

        `body` must be the raw request bytes. The signature is computed over
        exactly those, so a body that was decoded, re-parsed or re-serialised
        on the way here will not match, however equal the JSON looks.
        """
        self._verify(body, signature)
        invoice = _invoice_of(body)
        asset = _asset_of(invoice)

        payment = Payment(
            provider=PROVIDER,
            external_id=_invoice_id(invoice),
            user_id=_user_id(invoice),
            amount=_minor_units(invoice.get("amount")),
        )
        return ParsedPayment(payment=payment, currency=asset)

    def _verify(self, body: bytes, signature: str) -> None:
        # A request that arrived without the header reaches here as None: that
        # is a refusal like any other, not an AttributeError two frames down.
        if not isinstance(signature, str):
            raise InvalidSignature(f"the signature is not a string: {type(signature).__name__}")
        # Hex case carries no information and the header's is not guaranteed.
        expected = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature.strip().lower()):
            raise InvalidSignature("the signature does not match the body")


def _invoice_of(body: bytes) -> dict[str, Any]:
    """The invoice object inside a paid-invoice update."""
    try:
        # parse_float keeps decimals off the float path even when unquoted.
        update = json.loads(body, parse_float=Decimal)
    except ValueError as broken:
        raise InvalidWebhook(f"the body is not JSON: {broken}") from broken

    if not isinstance(update, dict):
        raise InvalidWebhook("the body is not an update object")
    if update.get("update_type") != PAID_UPDATE:
        raise InvalidWebhook(f"not a payment: update_type is {update.get('update_type')!r}")

    invoice = update.get("payload")
    if not isinstance(invoice, dict):
        raise InvalidWebhook("the update carries no invoice")
    if invoice.get("status") != "paid":
        raise InvalidWebhook(f"the invoice is {invoice.get('status')!r}, not paid")
    return invoice


def _asset_of(invoice: dict[str, Any]) -> str:
    """The unit this invoice is priced in, refusing every one but USDT.

    A fiat invoice prices in fiat and settles in crypto, so its `amount` and
    its `paid_amount` are different numbers in different units: reading the
    first as USDT would book a payment that never happened. Both it and an
    invoice settled in some other asset are refused by name.
    """
    fiat = invoice.get("fiat")
    if isinstance(fiat, str) and fiat:
        raise UnsupportedAsset(f"this adapter reads {SUPPORTED_ASSET} invoices, got fiat {fiat!r}")

    asset = invoice.get("asset")
    if not isinstance(asset, str) or not asset:
        raise InvalidWebhook("the invoice names no asset")

    settled = invoice.get("paid_asset")
    if isinstance(settled, str) and settled and settled != asset:
        raise UnsupportedAsset(
            f"this adapter reads {SUPPORTED_ASSET} invoices, got {asset!r} settled in {settled!r}"
        )
    if asset != SUPPORTED_ASSET:
        raise UnsupportedAsset(f"this adapter reads {SUPPORTED_ASSET} invoices, got {asset!r}")
    return asset


def _invoice_id(invoice: dict[str, Any]) -> str:
    invoice_id = invoice.get("invoice_id")
    if not isinstance(invoice_id, int | str) or isinstance(invoice_id, bool):
        raise InvalidWebhook("the invoice has no usable invoice_id")
    return str(invoice_id)


def _user_id(invoice: dict[str, Any]) -> str:
    payload = invoice.get("payload")
    if not isinstance(payload, str) or not payload:
        raise InvalidWebhook(
            "the invoice payload is empty: put the subscriber's id there when creating it"
        )
    return payload


def _minor_units(amount: Any) -> int:
    """Decimal USDT to whole minor units, exactly or not at all."""
    if isinstance(amount, bool) or not isinstance(amount, str | int | Decimal):
        raise InvalidWebhook(f"the amount is not a number: {amount!r}")
    try:
        scaled = Decimal(amount) * USDT_MINOR_UNITS
    except InvalidOperation as broken:
        raise InvalidWebhook(f"the amount is not a number: {amount!r}") from broken

    # Decimal reads "NaN" and "Infinity" without complaint; int() then raises
    # something no caller of an adapter would think to catch.
    if not scaled.is_finite():
        raise InvalidWebhook(f"the amount is not a number: {amount!r}")
    if scaled != scaled.to_integral_value():
        raise InvalidWebhook(f"the amount is finer than a minor unit: {amount!r}")
    if scaled < 0:
        raise InvalidWebhook(f"the amount is negative: {amount!r}")
    return int(scaled)
