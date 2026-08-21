import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db
from ..plans import PLANS, is_valid_plan
from ..services import subscription_service, razorpay_service
from ..services.razorpay_service import RazorpayNotConfiguredError, WebhookSignatureError

router = APIRouter(prefix="/billing", tags=["billing"])
logger = logging.getLogger("qurely.billing")


# ---------------------------------------------------------------------------
# Clinic-facing billing endpoints. All use get_current_active_user (NOT
# require_feature) — these must stay reachable for an expired/suspended
# clinic, since that's exactly who needs to see "why" and hit Pay Now.
# ---------------------------------------------------------------------------

@router.get("/status", response_model=schemas.BillingStatusOut)
def billing_status(db: Session = Depends(get_db), clinic: models.Clinic = Depends(auth.get_current_clinic)):
    state = subscription_service.sync_subscription_state(db, clinic)
    days_left = None
    if state == subscription_service.TRIALING and clinic.trial_ends_at:
        days_left = subscription_service.trial_days_left(clinic.trial_ends_at)
    return schemas.BillingStatusOut(
        clinic_name=clinic.name,
        clinic_slug=clinic.slug,
        plan=clinic.plan,
        subscription_status=state,
        trial_ends_at=clinic.trial_ends_at,
        trial_days_left=days_left,
        current_period_end=clinic.current_period_end,
        cancel_at_period_end=bool(clinic.cancel_at_period_end),
        payment_status=clinic.payment_status,
    )


@router.get("/plans", response_model=list[schemas.PlanOut])
def list_plans(user: models.User = Depends(auth.get_current_active_user)):
    return [
        schemas.PlanOut(key=key, name=p["name"], price_inr=p["price_inr"], interval=p["interval"], features=p["features"])
        for key, p in PLANS.items()
    ]


@router.get("/payments", response_model=list[schemas.PaymentOut])
def list_payments(db: Session = Depends(get_db), clinic: models.Clinic = Depends(auth.get_current_clinic)):
    # Filtered by clinic_id derived from the authenticated user's own clinic —
    # one clinic can never see another clinic's payment history this way.
    return (
        db.query(models.Payment)
        .filter(models.Payment.clinic_id == clinic.id)
        .order_by(models.Payment.created_at.desc())
        .all()
    )


@router.post("/subscribe", response_model=schemas.SubscribeResponse)
def subscribe(
    payload: schemas.SubscribeRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_active_user),
    clinic: models.Clinic = Depends(auth.get_current_clinic),
):
    if not is_valid_plan(payload.plan):
        raise HTTPException(status_code=400, detail=f"Unknown plan '{payload.plan}'")
    if not razorpay_service.is_configured():
        raise HTTPException(status_code=503, detail="Payment gateway is not configured yet. Contact support.")

    try:
        sub = razorpay_service.create_subscription(payload.plan, notify_email=user.email)
    except RazorpayNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:  # Razorpay API errors, network errors, etc.
        logger.exception("Razorpay subscription creation failed")
        raise HTTPException(status_code=502, detail="Could not start checkout with the payment gateway. Please try again.")

    # NOTE: this does not grant access. clinic.plan records which plan the
    # clinic *intends* to pay for; the state machine (subscription_service)
    # only grants feature access once the webhook confirms payment and sets
    # current_period_end — see get_subscription_state(). A clinic cannot gain
    # access by calling this endpoint alone.
    clinic.plan = payload.plan
    clinic.razorpay_subscription_id = sub["id"]
    db.commit()

    return schemas.SubscribeResponse(
        razorpay_subscription_id=sub["id"],
        razorpay_key_id=razorpay_service.RAZORPAY_KEY_ID or "",
        plan=payload.plan,
        status=sub.get("status", "created"),
    )


# ---------------------------------------------------------------------------
# Razorpay webhook — NOT behind user auth. Trust comes only from a verified
# X-Razorpay-Signature over the raw body. This is the only place that may
# ever set subscription_status to active/past_due/suspended/cancelled.
# ---------------------------------------------------------------------------

@router.post("/webhook/razorpay")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")
    event_id = request.headers.get("x-razorpay-event-id")  # unique per event; used for idempotency

    try:
        razorpay_service.verify_webhook_signature(raw_body, signature)
    except WebhookSignatureError:
        raise HTTPException(status_code=400, detail="Invalid webhook signature")
    except RazorpayNotConfiguredError as e:
        # Misconfiguration on our side — reject rather than silently trusting
        # an unverifiable payload.
        logger.error("Webhook received but RAZORPAY_WEBHOOK_SECRET is not set")
        raise HTTPException(status_code=503, detail=str(e))

    import json
    try:
        body = json.loads(raw_body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event_type = body.get("event", "")
    payload = body.get("payload", {}) or {}
    sub_entity = (payload.get("subscription") or {}).get("entity")
    payment_entity = (payload.get("payment") or {}).get("entity")

    # Idempotency: Razorpay retries webhooks on non-2xx / timeout, and can
    # occasionally deliver the same event more than once even on success.
    if event_id:
        existing = db.query(models.Payment).filter(models.Payment.razorpay_event_id == event_id).first()
        if existing:
            return {"status": "ok", "duplicate": True}

    razorpay_subscription_id = None
    if sub_entity:
        razorpay_subscription_id = sub_entity.get("id")
    elif payment_entity:
        razorpay_subscription_id = payment_entity.get("subscription_id")

    clinic = None
    if razorpay_subscription_id:
        clinic = db.query(models.Clinic).filter(
            models.Clinic.razorpay_subscription_id == razorpay_subscription_id
        ).first()

    if clinic is None:
        # Nothing in our system to reconcile against (e.g. a test event fired
        # from the Dashboard with no real subscription). Ack so Razorpay
        # doesn't keep retrying, but don't fabricate a Payment/clinic update.
        logger.warning("Webhook %s for unknown subscription %s", event_type, razorpay_subscription_id)
        return {"status": "ok", "ignored": True}

    _apply_event(db, clinic, event_type, sub_entity, payment_entity)

    payment = models.Payment(
        clinic_id=clinic.id,
        razorpay_payment_id=(payment_entity or {}).get("id"),
        razorpay_order_id=(payment_entity or {}).get("order_id"),
        razorpay_subscription_id=razorpay_subscription_id,
        amount=(payment_entity or {}).get("amount"),
        currency=(payment_entity or {}).get("currency", "INR"),
        status=(payment_entity or {}).get("status", event_type),
        plan=clinic.plan,
        event_type=event_type,
        razorpay_event_id=event_id,
        paid_at=razorpay_service.unix_to_datetime((payment_entity or {}).get("created_at")) if payment_entity else None,
    )
    db.add(payment)
    try:
        db.commit()
    except IntegrityError:
        # Two concurrent deliveries of the same event both passed the
        # pre-check above — the unique constraint on razorpay_event_id is the
        # real guard. Roll back the duplicate insert and ack normally.
        db.rollback()
        return {"status": "ok", "duplicate": True}

    return {"status": "ok"}


def _apply_event(db: Session, clinic: models.Clinic, event_type: str, sub_entity: dict | None, payment_entity: dict | None) -> None:
    """Mutates `clinic` according to a verified Razorpay event. Does not
    commit — caller commits once, together with the Payment row, in one
    transaction."""
    if event_type in ("subscription.activated", "subscription.charged"):
        if sub_entity:
            clinic.current_period_start = razorpay_service.unix_to_datetime(sub_entity.get("current_start"))
            clinic.current_period_end = razorpay_service.unix_to_datetime(sub_entity.get("current_end"))
            if sub_entity.get("customer_id"):
                clinic.razorpay_customer_id = sub_entity["customer_id"]
        if clinic.subscription_started_at is None:
            clinic.subscription_started_at = datetime.utcnow()
        clinic.subscription_status = subscription_service.ACTIVE
        clinic.payment_status = "paid"
        clinic.cancel_at_period_end = False
        clinic.grace_period_ends_at = None
        if payment_entity and payment_entity.get("id"):
            clinic.last_payment_id = payment_entity["id"]

    elif event_type == "subscription.pending":
        # A renewal charge failed; Razorpay will auto-retry. Start our own
        # grace period during which access stays on (see has_feature_access).
        subscription_service.start_grace_period(clinic)
        clinic.payment_status = "failed"

    elif event_type == "subscription.halted":
        # Retries exhausted — lock paid features but never touch clinic data.
        clinic.subscription_status = subscription_service.SUSPENDED
        clinic.payment_status = "failed"
        clinic.grace_period_ends_at = None

    elif event_type == "subscription.cancelled":
        clinic.subscription_status = subscription_service.CANCELLED
        clinic.cancel_at_period_end = True

    elif event_type == "payment.failed":
        clinic.payment_status = "failed"

    elif event_type == "payment.captured":
        # Usually accompanies subscription.charged; if it arrives alone just
        # record the payment status (Payment row is written by the caller).
        if payment_entity and payment_entity.get("id"):
            clinic.last_payment_id = payment_entity["id"]
