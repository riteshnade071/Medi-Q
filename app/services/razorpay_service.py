import os
from datetime import datetime
from typing import Optional

from .. import plans as plans_module

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET")

_client = None


class RazorpayNotConfiguredError(Exception):
    pass


class WebhookSignatureError(Exception):
    pass


def is_configured() -> bool:
    return bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)


def get_client():
    """Lazily constructs (and caches) the Razorpay SDK client. Kept lazy so the
    app can boot and serve billing-status/login even before Razorpay keys are
    set — only /billing/subscribe and the webhook actually need them."""
    global _client
    if _client is None:
        if not is_configured():
            raise RazorpayNotConfiguredError(
                "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not set."
            )
        import razorpay  # imported lazily so `pip install -r requirements.txt`
        # without razorpay installed doesn't crash the whole app at import time
        _client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    return _client


def create_subscription(plan_key: str, notify_email: str, notify_phone: Optional[str] = None,
                         total_count: int = 120) -> dict:
    """Creates a Razorpay Subscription for the given plan. total_count bounds
    how many billing cycles Razorpay will auto-charge before stopping — 120
    monthly cycles (10 years) is effectively "until cancelled" for a monthly
    plan; Razorpay requires either total_count or end_at to be set.
    Returns the raw Razorpay subscription entity (has "id", "status", etc.).
    """
    plan_id = plans_module.razorpay_plan_id(plan_key)
    if not plan_id:
        raise RazorpayNotConfiguredError(
            f"No Razorpay plan id configured for '{plan_key}' "
            f"(set the {plans_module.PLANS[plan_key]['razorpay_plan_id_env']} env var)."
        )
    client = get_client()
    payload = {
        "plan_id": plan_id,
        "total_count": total_count,
        "quantity": 1,
        "customer_notify": 1,
        "notify_info": {"notify_email": notify_email},
    }
    if notify_phone:
        payload["notify_info"]["notify_phone"] = notify_phone
    return client.subscription.create(payload)


def cancel_subscription(razorpay_subscription_id: str, cancel_at_cycle_end: bool = True) -> dict:
    client = get_client()
    return client.subscription.cancel(razorpay_subscription_id, {"cancel_at_cycle_end": 1 if cancel_at_cycle_end else 0})


def verify_webhook_signature(raw_body: bytes, signature: str) -> None:
    """Raises WebhookSignatureError if the signature doesn't match. Must be
    called with the *raw* request body bytes — never the re-serialized/parsed
    JSON, since the signature is computed over the exact bytes Razorpay sent."""
    if not RAZORPAY_WEBHOOK_SECRET:
        raise RazorpayNotConfiguredError("RAZORPAY_WEBHOOK_SECRET is not set.")
    if not signature:
        raise WebhookSignatureError("Missing X-Razorpay-Signature header.")
    import razorpay
    from razorpay.errors import SignatureVerificationError
    try:
        razorpay.Utility().verify_webhook_signature(
            raw_body.decode("utf-8"), signature, RAZORPAY_WEBHOOK_SECRET
        )
    except SignatureVerificationError as e:
        raise WebhookSignatureError(str(e))


def unix_to_datetime(ts) -> Optional[datetime]:
    if ts is None:
        return None
    return datetime.utcfromtimestamp(int(ts))
