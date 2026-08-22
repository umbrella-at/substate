"""What a payment provider has to hand the core.

One protocol, one result type. Everything provider-shaped — signatures, JSON
layouts, asset names, decimal places — stays on the adapter's side of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from substate.models import Payment


@dataclass(frozen=True)
class ParsedPayment:
    """A payment read out of a webhook, and the unit it arrived in.

    The currency travels beside the payment rather than inside it. `Payment`
    has no currency field and is not getting one: a plan is priced in whatever
    unit its provider pays in, the core never converts, and a field it cannot
    interpret would only invite it to try.
    """

    payment: Payment
    currency: str


class PaymentWebhook(Protocol):
    """Turns one provider's signed webhook into a payment the core understands.

    `body` is the raw bytes of the request exactly as they arrived. The
    signature is computed over those bytes, so anything that re-encodes them on
    the way — decoding to `str` and back, a framework that parses the JSON and
    dumps it again — breaks the check. Hand over what the socket gave you.

    Synchronous on purpose: parsing and hashing touch no network, and making
    this a coroutine would only promise otherwise.
    """

    def parse(self, body: bytes, signature: str) -> ParsedPayment: ...
