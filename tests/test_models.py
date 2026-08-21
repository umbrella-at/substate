"""Domain entities and the invariants they refuse to be built without."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from substate import (
    Accrual,
    InvalidPlan,
    InvalidPromoCode,
    Payment,
    Period,
    Plan,
    PromoCode,
    PromoKind,
    PromoScope,
    ReferralProgram,
    ScopeKind,
    State,
    Subscription,
)

JANUARY = datetime(2026, 1, 31, tzinfo=UTC)


def plan(**overrides: object) -> Plan:
    fields: dict[str, object] = {
        "id": "pro_month",
        "price": 29900,
        "currency": "RUB",
        "period": Period.days(30),
        "trial_days": 3,
        "grace_days": 3,
    }
    fields.update(overrides)
    return Plan(**fields)  # type: ignore[arg-type]


def subscription(**overrides: object) -> Subscription:
    fields: dict[str, object] = {
        "user_id": "user_1",
        "plan_id": "pro_month",
        "state": State.ACTIVE,
        "expires_at": JANUARY,
        "grace_days": 3,
    }
    fields.update(overrides)
    return Subscription(**fields)  # type: ignore[arg-type]


def test_the_five_states_and_nothing_else() -> None:
    assert [member.name for member in State] == [
        "TRIAL",
        "ACTIVE",
        "GRACE",
        "EXPIRED",
        "CANCELLED",
    ]


def test_a_plan_keeps_what_it_was_given() -> None:
    pro = plan()

    assert pro.id == "pro_month"
    assert pro.price == 29900
    assert pro.currency == "RUB"
    assert pro.period == Period.days(30)
    assert pro.trial_days == 3
    assert pro.grace_days == 3


def test_a_plan_needs_neither_a_trial_nor_a_grace() -> None:
    bare = Plan(id="bare", price=29900, currency="RUB", period=Period.days(30))

    assert bare.trial_days == 0
    assert bare.grace_days == 0


def test_a_free_plan_is_legal() -> None:
    assert plan(price=0).price == 0


def test_zero_is_legal_for_every_count() -> None:
    assert plan(trial_days=0, grace_days=0).grace_days == 0


def test_a_plan_is_frozen() -> None:
    pro = plan()

    with pytest.raises(AttributeError):
        pro.price = 1  # type: ignore[misc]


@pytest.mark.parametrize("field", ["price", "trial_days", "grace_days"])
def test_a_plan_rejects_negative_counts(field: str) -> None:
    with pytest.raises(InvalidPlan):
        plan(**{field: -1})


def test_grace_must_be_shorter_than_the_period() -> None:
    """Otherwise paying inside grace renews into the past."""
    with pytest.raises(InvalidPlan):
        plan(period=Period.days(30), grace_days=30)


def test_grace_one_day_shorter_than_the_period_is_fine() -> None:
    assert plan(period=Period.days(30), grace_days=29).grace_days == 29


def test_a_monthly_plan_is_measured_against_february() -> None:
    """28 days, not 30: a plan that only breaks in February still breaks."""
    with pytest.raises(InvalidPlan):
        plan(period=Period.months(1), grace_days=28)

    assert plan(period=Period.months(1), grace_days=27).grace_days == 27


def test_an_invalid_plan_is_also_a_value_error() -> None:
    with pytest.raises(ValueError):
        plan(price=-1)


def test_grace_ends_after_the_paid_period() -> None:
    sub = subscription(expires_at=JANUARY, grace_days=3)

    assert sub.grace_ends_at == JANUARY + timedelta(days=3)


def test_without_grace_the_grace_boundary_is_the_expiry() -> None:
    assert subscription(grace_days=0).grace_ends_at == JANUARY


def test_a_subscription_without_an_expiry_has_no_grace_boundary() -> None:
    assert subscription(state=State.TRIAL, expires_at=None).grace_ends_at is None


def test_the_grace_boundary_follows_the_snapshot_not_the_current_plan() -> None:
    """The plan may be edited tomorrow. What was promised today does not change."""
    sub = subscription(grace_days=7)

    assert sub.grace_ends_at == JANUARY + timedelta(days=7)


@pytest.mark.parametrize("state", [State.TRIAL, State.ACTIVE, State.GRACE, State.CANCELLED])
def test_access_is_one_predicate(state: State) -> None:
    assert subscription(state=state).is_active


def test_an_expired_subscription_has_no_access() -> None:
    assert not subscription(state=State.EXPIRED).is_active


def test_the_due_boundary_of_a_trial_is_the_end_of_the_trial() -> None:
    trial_ends = datetime(2026, 1, 4, tzinfo=UTC)
    sub = subscription(state=State.TRIAL, trial_ends_at=trial_ends, expires_at=None)

    assert sub.due_at == trial_ends


def test_the_due_boundary_of_an_active_subscription_is_its_expiry() -> None:
    assert subscription(state=State.ACTIVE).due_at == JANUARY


def test_the_due_boundary_inside_grace_is_the_end_of_grace() -> None:
    sub = subscription(state=State.GRACE, grace_days=3)

    assert sub.due_at == JANUARY + timedelta(days=3)


def test_the_due_boundary_of_a_cancelled_subscription_is_its_expiry() -> None:
    """Cancelled keeps access until the paid period runs out, then it is over."""
    assert subscription(state=State.CANCELLED).due_at == JANUARY


def test_an_expired_subscription_has_nothing_left_to_wait_for() -> None:
    assert subscription(state=State.EXPIRED).due_at is None


def test_a_subscription_starts_without_referrer_pending_plan_or_promo() -> None:
    sub = subscription()

    assert sub.referrer_id is None
    assert sub.pending_plan_id is None
    assert sub.promo_code is None
    assert sub.promo_periods_left is None
    assert sub.cancelled_at is None
    assert sub.billing_anchor_day is None


@pytest.mark.parametrize("grace_days", [-1, -30])
def test_a_subscription_rejects_a_negative_grace_snapshot(grace_days: int) -> None:
    """A grace that ends before the period it follows is not a grace."""
    with pytest.raises(ValueError):
        subscription(grace_days=grace_days)


def test_a_subscription_is_a_record_the_engine_moves() -> None:
    sub = subscription()

    sub.state = State.GRACE

    assert sub.state is State.GRACE


@pytest.mark.parametrize("anchor", [0, 32, -1])
def test_the_billing_anchor_must_be_a_day_of_the_month(anchor: int) -> None:
    with pytest.raises(ValueError):
        subscription(billing_anchor_day=anchor)


def test_the_billing_anchor_survives_a_short_month() -> None:
    assert subscription(billing_anchor_day=31).billing_anchor_day == 31


def test_a_payment_is_identified_by_provider_and_external_id() -> None:
    payment = Payment(provider="cryptobot", external_id="inv_1", user_id="user_1", amount=29900)

    assert (payment.provider, payment.external_id) == ("cryptobot", "inv_1")
    assert payment.amount == 29900


def test_a_payment_is_frozen_and_hashable() -> None:
    payment = Payment(provider="cryptobot", external_id="inv_1", user_id="user_1", amount=29900)

    with pytest.raises(AttributeError):
        payment.amount = 1  # type: ignore[misc]

    assert len({payment, payment}) == 1


def test_a_payment_cannot_be_negative() -> None:
    with pytest.raises(ValueError):
        Payment(provider="cryptobot", external_id="inv_1", user_id="user_1", amount=-1)


def test_a_promo_applies_to_the_first_payment_by_default() -> None:
    code = PromoCode(code="WELCOME", kind=PromoKind.PERCENT, value=30)

    assert code.applies_to == PromoScope.first_payment()
    assert code.max_redemptions is None
    assert code.max_per_user == 1


def test_a_scope_of_n_periods_carries_n() -> None:
    scope = PromoScope.n_periods(3)

    assert scope.kind is ScopeKind.N_PERIODS
    assert scope.periods == 3


def test_the_unbounded_scopes_carry_no_count() -> None:
    assert PromoScope.first_payment().periods is None
    assert PromoScope.forever().periods is None


def test_n_periods_needs_a_positive_count() -> None:
    with pytest.raises(InvalidPromoCode):
        PromoScope.n_periods(0)


def test_an_unbounded_scope_refuses_a_count() -> None:
    with pytest.raises(InvalidPromoCode):
        PromoScope(kind=ScopeKind.FOREVER, periods=2)


def test_scopes_compare_by_value() -> None:
    assert PromoScope.n_periods(3) == PromoScope.n_periods(3)
    assert PromoScope.n_periods(3) != PromoScope.n_periods(4)
    assert PromoScope.forever() != PromoScope.first_payment()


@pytest.mark.parametrize("percent", [0, 1, 50, 100])
def test_a_percent_promo_lives_on_the_percentage_scale(percent: int) -> None:
    assert PromoCode(code="X", kind=PromoKind.PERCENT, value=percent).value == percent


@pytest.mark.parametrize("percent", [-1, 101, 1000])
def test_a_percent_promo_outside_the_scale_is_rejected(percent: int) -> None:
    with pytest.raises(InvalidPromoCode):
        PromoCode(code="X", kind=PromoKind.PERCENT, value=percent)


def test_a_fixed_promo_may_exceed_any_price() -> None:
    """Whether it exceeds the price is a payment-time question, not a promo-time one."""
    assert PromoCode(code="X", kind=PromoKind.FIXED, value=10_000_000).value == 10_000_000


@pytest.mark.parametrize("kind", [PromoKind.FIXED, PromoKind.PLUS_DAYS])
def test_a_negative_promo_value_is_rejected(kind: PromoKind) -> None:
    with pytest.raises(InvalidPromoCode):
        PromoCode(code="X", kind=kind, value=-1)


def test_an_invalid_promo_is_also_a_value_error() -> None:
    with pytest.raises(ValueError):
        PromoCode(code="X", kind=PromoKind.PERCENT, value=101)


def test_a_promo_is_frozen() -> None:
    code = PromoCode(code="X", kind=PromoKind.PERCENT, value=30)

    with pytest.raises(AttributeError):
        code.value = 50  # type: ignore[misc]


def test_a_referral_program_is_a_percentage_and_a_repeat_rule() -> None:
    bloggers = ReferralProgram(id="bloggers", percent=30, accrual=Accrual.EVERY_PAYMENT)

    assert bloggers.percent == 30
    assert bloggers.accrual is Accrual.EVERY_PAYMENT


def test_the_two_accrual_rules() -> None:
    assert [member.name for member in Accrual] == ["FIRST_PAYMENT_ONLY", "EVERY_PAYMENT"]


@pytest.mark.parametrize("percent", [-1, 101])
def test_a_referral_percent_outside_the_scale_is_rejected(percent: int) -> None:
    with pytest.raises(ValueError):
        ReferralProgram(id="x", percent=percent, accrual=Accrual.EVERY_PAYMENT)


def test_a_zero_percent_program_is_legal() -> None:
    program = ReferralProgram(id="x", percent=0, accrual=Accrual.FIRST_PAYMENT_ONLY)

    assert program.percent == 0


def test_a_referral_program_is_frozen() -> None:
    program = ReferralProgram(id="x", percent=30, accrual=Accrual.EVERY_PAYMENT)

    with pytest.raises(AttributeError):
        program.percent = 50  # type: ignore[misc]
