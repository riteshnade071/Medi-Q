"""
Smoke tests for the subscription/billing system (spec section 18).
Uses FastAPI's TestClient plus direct DB access to simulate time passing
(trial expiry, grace period elapsing) and to fabricate signed Razorpay
webhook payloads, without needing a live Razorpay account.
"""
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta

os.environ["DATABASE_URL"] = "sqlite:///./smoke_test_billing.db"
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_dummykey"
os.environ["RAZORPAY_KEY_SECRET"] = "dummy_key_secret"
os.environ["RAZORPAY_WEBHOOK_SECRET"] = "whsec_test_dummy_secret"
os.environ["RAZORPAY_PRO_PLAN_ID"] = "plan_dummypro000"
os.environ["SUBSCRIPTION_GRACE_PERIOD_DAYS"] = "3"

import sys
sys.path.insert(0, os.path.dirname(__file__))

if os.path.exists("smoke_test_billing.db"):
    os.remove("smoke_test_billing.db")

from fastapi.testclient import TestClient
from main import app
from app.database import SessionLocal
from app import models

client = TestClient(app)


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        raise SystemExit(1)


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def sign(body_bytes: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()


def webhook_body(event_type, subscription_id, payment_id=None, current_start=None, current_end=None, amount=99900):
    payload = {}
    if subscription_id:
        payload["subscription"] = {
            "entity": {
                "id": subscription_id,
                "status": "active",
                "current_start": current_start,
                "current_end": current_end,
                "customer_id": "cust_test_123",
            }
        }
    if payment_id:
        payload["payment"] = {
            "entity": {
                "id": payment_id,
                "order_id": None,
                "subscription_id": subscription_id,
                "amount": amount,
                "currency": "INR",
                "status": "captured",
                "created_at": int(time.time()),
            }
        }
    return json.dumps({"event": event_type, "payload": payload}).encode("utf-8")


def send_webhook(body_bytes, event_id, secret=None, bad_signature=False):
    secret = secret or os.environ["RAZORPAY_WEBHOOK_SECRET"]
    sig = "deadbeef" * 8 if bad_signature else sign(body_bytes, secret)
    headers = {
        "Content-Type": "application/json",
        "x-razorpay-signature": sig,
        "x-razorpay-event-id": event_id,
    }
    return client.post("/billing/webhook/razorpay", content=body_bytes, headers=headers)


def db_session():
    return SessionLocal()


def main():
    # --- 1. New clinic gets a 14-day trial ---
    r = client.post("/auth/signup", json={"clinic_name": "Billing Test Clinic", "email": "owner@billingtest.com", "password": "pass1234"})
    check("signup", r.status_code == 200)
    token = r.json()["access_token"]
    h = auth_headers(token)

    r = client.get("/billing/status", headers=h)
    check("billing status after signup", r.status_code == 200)
    body = r.json()
    check("new clinic is trialing", body["subscription_status"] == "trialing")
    check("new clinic gets ~14 day trial", 13 <= body["trial_days_left"] <= 14)

    # --- 2. Trial-active clinic can use protected APIs ---
    r = client.post("/doctors", json={"name": "Dr. Trial", "specialization": "General"}, headers=h)
    check("trial clinic can create doctor (protected API)", r.status_code == 200)
    doctor_id = r.json()["id"]

    r = client.get("/dashboard/summary", headers=h)
    check("trial clinic can access analytics-gated endpoint", r.status_code == 200)

    # --- Force trial expiry (simulate 14 days passing) ---
    me = client.get("/auth/me", headers=h).json()
    slug = me["clinic_slug"]

    db = db_session()
    clinic = db.query(models.Clinic).filter(models.Clinic.slug == slug).first()
    clinic.trial_ends_at = datetime.utcnow() - timedelta(days=1)
    db.commit()
    clinic_id = clinic.id
    db.close()

    # --- 3. Trial-expired clinic cannot use protected APIs ---
    r = client.post("/doctors", json={"name": "Dr. Blocked", "specialization": "General"}, headers=h)
    check("expired clinic blocked from protected API", r.status_code == 402)
    check("expired 402 body has SUBSCRIPTION_REQUIRED code", r.json().get("code") == "SUBSCRIPTION_REQUIRED")
    check("expired 402 body reports expired status", r.json().get("subscription_status") == "expired")

    # --- 4. Expired clinic can still access billing + profile ---
    r = client.get("/billing/status", headers=h)
    check("expired clinic can view billing status", r.status_code == 200 and r.json()["subscription_status"] == "expired")
    r = client.get("/auth/me", headers=h)
    check("expired clinic can view profile", r.status_code == 200)
    r = client.get("/billing/plans", headers=h)
    check("expired clinic can view plans", r.status_code == 200 and len(r.json()) == 1)

    # Existing patient-facing booking link must still say "unavailable", not 500/expose data
    r = client.post("/public/book", json={"doctor_id": doctor_id, "patient_name": "Walk-in Patient"})
    check("public booking blocked for expired clinic (503, not leaking billing detail)", r.status_code == 503)

    # --- Fabricate a pending subscription (as if /billing/subscribe had been called) ---
    fake_sub_id = "sub_test_abc123"
    db = db_session()
    clinic = db.query(models.Clinic).filter(models.Clinic.id == clinic_id).first()
    clinic.razorpay_subscription_id = fake_sub_id
    clinic.plan = "pro"
    db.commit()
    db.close()

    # --- 5. Invalid Razorpay webhook is rejected ---
    body = webhook_body("subscription.activated", fake_sub_id, current_start=int(time.time()), current_end=int(time.time()) + 30 * 86400)
    r = send_webhook(body, "evt_bad_sig_001", bad_signature=True)
    check("invalid webhook signature rejected (400)", r.status_code == 400)

    # --- 6. Valid successful payment activates subscription ---
    now_ts = int(time.time())
    period_end_ts = now_ts + 30 * 86400
    body = webhook_body("subscription.activated", fake_sub_id, payment_id="pay_test_001",
                         current_start=now_ts, current_end=period_end_ts)
    r = send_webhook(body, "evt_activated_001")
    check("valid activated webhook accepted", r.status_code == 200)

    r = client.get("/billing/status", headers=h)
    check("subscription now active", r.json()["subscription_status"] == "active")

    # --- 8. Successful payment restores feature access ---
    r = client.post("/doctors", json={"name": "Dr. Restored", "specialization": "General"}, headers=h)
    check("feature access restored automatically after payment", r.status_code == 200)

    # --- 7. Duplicate webhook does not create duplicate payment records ---
    db = db_session()
    count_before = db.query(models.Payment).filter(models.Payment.razorpay_event_id == "evt_activated_001").count()
    db.close()
    r = send_webhook(body, "evt_activated_001")  # exact same event id + body again
    check("duplicate webhook acknowledged", r.status_code == 200 and r.json().get("duplicate") is True)
    db = db_session()
    count_after = db.query(models.Payment).filter(models.Payment.razorpay_event_id == "evt_activated_001").count()
    db.close()
    check("duplicate webhook did not insert a second payment row", count_before == count_after == 1)

    # --- 9. Failed renewal changes state appropriately ---
    body = webhook_body("subscription.pending", fake_sub_id)
    r = send_webhook(body, "evt_pending_001")
    check("renewal-failed webhook accepted", r.status_code == 200)
    r = client.get("/billing/status", headers=h)
    check("state moves to past_due after failed renewal", r.json()["subscription_status"] == "past_due")

    # --- 10. Grace period works: still has access while in grace ---
    r = client.post("/doctors", json={"name": "Dr. Grace", "specialization": "General"}, headers=h)
    check("access retained during grace period", r.status_code == 200)

    # elapse the grace period
    db = db_session()
    clinic = db.query(models.Clinic).filter(models.Clinic.id == clinic_id).first()
    clinic.grace_period_ends_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    db.close()

    r = client.get("/billing/status", headers=h)
    check("state moves to suspended after grace period elapses", r.json()["subscription_status"] == "suspended")
    r = client.post("/doctors", json={"name": "Dr. Suspended", "specialization": "General"}, headers=h)
    check("access blocked once suspended", r.status_code == 402)
    r = client.get("/billing/status", headers=h)
    check("suspended clinic can still view billing", r.status_code == 200)

    # --- 11. Another clinic cannot access this clinic's payment/subscription data ---
    r = client.post("/auth/signup", json={"clinic_name": "Other Clinic", "email": "owner@otherclinic.com", "password": "pass1234"})
    token2 = r.json()["access_token"]
    h2 = auth_headers(token2)
    r = client.get("/billing/payments", headers=h2)
    check("other clinic sees its own (empty) payment list", r.status_code == 200 and r.json() == [])
    r = client.get("/billing/payments", headers=h)
    check("original clinic sees its own payments", r.status_code == 200 and len(r.json()) >= 1)
    other_payment_ids = {p["id"] for p in client.get("/billing/payments", headers=h2).json()}
    own_payment_ids = {p["id"] for p in client.get("/billing/payments", headers=h).json()}
    check("no overlap between clinics' payment records", other_payment_ids.isdisjoint(own_payment_ids))

    print("\nAll billing/subscription smoke tests passed.")


if __name__ == "__main__":
    main()
