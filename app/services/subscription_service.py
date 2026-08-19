import os
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .. import models
from ..plans import PAID_FEATURES, PLANS

# States (recommended set from the spec). `subscription_status` on the Clinic
# row is a cache of the last-computed value of these — always re-derive via
# get_subscription_state() rather than trusting the stored column directly.
TRIALING = "trialing"
ACTIVE = "active"
PAST_DUE = "past_due"
EXPIRED = "expired"
CANCELLED = "cancelled"
SUSPENDED = "suspended"

_ACCESS_GRANTING_STATES = {TRIALING, ACTIVE, PAST_DUE}

GRACE_PERIOD_DAYS = int(os.getenv("SUBSCRIPTION_GRACE_PERIOD_DAYS", "3"))


class SubscriptionRequiredException(Exception):
    """Raised by require_feature() when a clinic's subscription state does not
    grant access to a gated feature. Handled by a FastAPI exception handler in
    main.py so the response body always matches the documented 402 shape."""

    def __init__(self, message: str, subscription_status: str):
        self.message = message
        self.subscription_status = subscription_status
        super().__init__(message)


def get_subscription_state(clinic: models.Clinic) -> str:
    """Recomputes the clinic's true subscription state from its data, live,
    every time it's called. This is the backend's single source of truth for
    access control — never gate a feature on clinic.subscription_status or
    clinic.plan directly."""
    now = datetime.utcnow()

    # Explicit terminal states set only by billing_router/webhook handling.
    if clinic.subscription_status == CANCELLED:
        return CANCELLED

    has_paid_history = bool(clinic.razorpay_subscription_id) and clinic.current_period_end is not None

    if has_paid_history:
        if clinic.subscription_status == PAST_DUE:
            if clinic.grace_period_ends_at and now < clinic.grace_period_ends_at:
                return PAST_DUE
            return SUSPENDED
        if clinic.subscription_status == SUSPENDED:
            return SUSPENDED
        if clinic.current_period_end and now <= clinic.current_period_end:
            return ACTIVE
        # Period lapsed and we never got a past_due/renewal webhook — fail closed.
        return EXPIRED

    # Never had a paid subscription yet: trial governs access.
    if clinic.trial_ends_at and now < clinic.trial_ends_at:
        return TRIALING
    return EXPIRED


def sync_subscription_state(db: Session, clinic: models.Clinic) -> str:
    """Writes the freshly computed state back onto clinic.subscription_status
    if it's changed, so it's cheap to read for display (billing page, admin
    views) without recomputing. Safe to call on every request — no cron job
    required to enforce expiration (see get_subscription_state)."""
    computed = get_subscription_state(clinic)
    if clinic.subscription_status != computed:
        clinic.subscription_status = computed
        db.commit()
        db.refresh(clinic)
    return computed


def has_feature_access(clinic: models.Clinic, feature_name: str | None = None) -> bool:
    """The other half of the backend source of truth. Pass no feature_name to
    just check whether the clinic has *any* paid access right now."""
    state = get_subscription_state(clinic)
    if state not in _ACCESS_GRANTING_STATES:
        return False
    if feature_name is None or feature_name not in PAID_FEATURES:
        return True
    if state == TRIALING:
        # Full access to every paid feature during the trial (spec: "During
        # the trial, all Qurely services are active").
        return True
    plan = PLANS.get(clinic.plan)
    if not plan:
        # Paid/grace state but plan key doesn't match a known plan (shouldn't
        # normally happen) — fail open rather than lock out a paying clinic
        # over a config mismatch; this only ever applies once a real payment
        # has already been verified.
        return True
    return feature_name in plan["features"]


def start_grace_period(clinic: models.Clinic) -> None:
    clinic.subscription_status = PAST_DUE
    clinic.grace_period_ends_at = datetime.utcnow() + timedelta(days=GRACE_PERIOD_DAYS)


def not_subscribed_message(state: str) -> str:
    if state == EXPIRED:
        return "Your Qurely trial has expired. Please subscribe to continue."
    if state == SUSPENDED:
        return "Your subscription payment could not be renewed. Please update your payment method to continue."
    if state == CANCELLED:
        return "Your subscription has been cancelled. Subscribe again to continue."
    return "This feature requires an active Qurely subscription."
