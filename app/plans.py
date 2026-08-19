"""
Single source of truth for Qurely's paid plans.

Prices/features here are NOT final — change them in this one place only.
Each plan's `razorpay_plan_id_env` points at the env var holding the actual
Razorpay Plan ID (created on the Razorpay Dashboard/API) for that plan; see
README/_env.example for setup instructions.
"""
import os

# The full set of features that require a non-trial, active-or-grace
# subscription. Anything not in this set is never gated (e.g. login, profile,
# billing pages, settings, support).
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
    "basic": {
        "name": "Basic",
        "price_inr": 499,
        "interval": "monthly",
        "razorpay_plan_id_env": "RAZORPAY_BASIC_PLAN_ID",
        "features": [
            "online_booking",
            "queue_management",
            "walkin_management",
            "receptionist_dashboard",
            "patient_registry",
        ],
    },
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
        ],
    },
    "premium": {
        "name": "Premium",
        "price_inr": 1999,
        "interval": "monthly",
        "razorpay_plan_id_env": "RAZORPAY_PREMIUM_PLAN_ID",
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
