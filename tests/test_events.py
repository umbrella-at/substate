"""Events: what the core says happened, for an application to act on."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from substate import (
    Event,
    PaymentDuplicate,
    PaymentRecorded,
    PaymentUnderpaid,
    PaymentUnmatched,
    State,
    SubscriptionActivated,
    SubscriptionCancelled,
    SubscriptionCreated,
    SubscriptionEnteringGrace,
    SubscriptionExpired,
    SubscriptionPlanChanged,
    SubscriptionRenewed,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def every_event() -> list[Event]:
    return [
        SubscriptionCreated("user_1", NOW, plan_id="pro", state=State.TRIAL),
        SubscriptionActivated("user_1", NOW, plan_id="pro", expires_at=NOW),
        SubscriptionRenewed("user_1", NOW, plan_id="pro", expires_at=NOW),
        SubscriptionEnteringGrace("user_1", NOW, grace_ends_at=NOW),
        SubscriptionExpired("user_1", NOW, reason="grace_ended"),
        SubscriptionCancelled("user_1", NOW, access_until=NOW),
        SubscriptionPlanChanged("user_1", NOW, plan_id="pro", pending_plan_id="lite"),
        PaymentRecorded("user_1", NOW, provider="cryptobot", external_id="inv_1", amount=29900),
        PaymentDuplicate("user_1", NOW, provider="cryptobot", external_id="inv_1"),
        PaymentUnderpaid(
            "user_1", NOW, provider="cryptobot", external_id="inv_1", amount=100, expected=29900
        ),
        PaymentUnmatched("user_1", NOW, provider="cryptobot", external_id="inv_1", amount=29900),
    ]


@pytest.mark.parametrize("event", every_event(), ids=lambda event: event.name)
def test_every_event_carries_who_and_when(event: Event) -> None:
    assert event.user_id == "user_1"
    assert event.occurred_at == NOW


@pytest.mark.parametrize("event", every_event(), ids=lambda event: event.name)
def test_every_event_is_a_substate_event(event: Event) -> None:
    assert isinstance(event, Event)


@pytest.mark.parametrize("event", every_event(), ids=lambda event: event.name)
def test_every_event_is_frozen(event: Event) -> None:
    with pytest.raises(AttributeError):
        event.user_id = "someone_else"  # type: ignore[misc]


def test_the_names_are_the_ones_from_the_spec() -> None:
    assert [event.name for event in every_event()] == [
        "subscription.created",
        "subscription.activated",
        "subscription.renewed",
        "subscription.entering_grace",
        "subscription.expired",
        "subscription.cancelled",
        "subscription.plan_changed",
        "payment.recorded",
        "payment.duplicate",
        "payment.underpaid",
        "payment.unmatched",
    ]


def test_the_name_is_a_class_attribute_not_a_field() -> None:
    """Two events of the same kind compare equal without carrying their label."""
    first = PaymentDuplicate("user_1", NOW, provider="cryptobot", external_id="inv_1")
    second = PaymentDuplicate("user_1", NOW, provider="cryptobot", external_id="inv_1")

    assert first == second
    assert PaymentDuplicate.name == "payment.duplicate"


def test_expiry_says_why_it_expired() -> None:
    """The reason is what tells a trial that fizzled from a subscription that lapsed."""
    assert SubscriptionExpired("user_1", NOW, reason="trial_not_converted").reason == (
        "trial_not_converted"
    )


def test_an_underpayment_carries_both_numbers() -> None:
    event = PaymentUnderpaid(
        "user_1", NOW, provider="cryptobot", external_id="inv_1", amount=100, expected=29900
    )

    assert (event.amount, event.expected) == (100, 29900)
