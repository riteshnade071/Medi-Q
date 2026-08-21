"""
Single source of truth for Clinic Queue's paid plan.

Price/features here are NOT final — change them in this one place only.
`razorpay_plan_id_env` points at the env var holding the actual Razorpay
Plan ID (created on the Razorpay Dashboard/API); see README/_env.example.

NOTE: Clinic Queue currently sells one plan. The structure is still a dict of
plans (not a single flat config) so adding a second tier later is just
adding another entry here — no other code needs to change.
"""
import os

# The full set of features that require an active (non-trial-expired)
# subscription. Anything not in this set is never gated (e.g. login,
# profile, billing pages, settings, support).
PAID_FEATURES = {
    "online_booking",
    "live_queue",
    "walkin_management",
    "doctor_dashboard",
    "receptionist_dashboard",
    "queue_management",
    "patient_registry",
    "notifications",
    "analytics",
}

PLANS = {
    "pro": {
        "name": "Pro",
        "price_inr": 999,
        "interval": "monthly",
        "razorpay_plan_id_env": "RAZORPAY_PRO_PLAN_ID",
        "features": [
            "online_booking",
            "queue_management",
            "walkin_management",
            "receptionist_dashboard",
            "patient_registry",
            "live_queue",
            "notifications",
            "doctor_dashboard",
            "analytics",
        ],
    },
}


def get_plan(plan_key: str) -> dict | None:
    return PLANS.get(plan_key)


def is_valid_plan(plan_key: str) -> bool:
    return plan_key in PLANS


def razorpay_plan_id(plan_key: str) -> str | None:
    plan = PLANS.get(plan_key)
    if not plan:
        return None
    return os.getenv(plan["razorpay_plan_id_env"])


def plan_features(plan_key: str) -> set[str]:
    plan = PLANS.get(plan_key)
    return set(plan["features"]) if plan else set()
