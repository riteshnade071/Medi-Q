"""
End-to-end smoke test for the v2 additions: Patient Registry (dedup by
mobile), Support Tickets, Dynamic Settings, WhatsApp message log.
"""
import os
os.environ["DATABASE_URL"] = "sqlite:///./smoke_test_v2.db"

import sys
sys.path.insert(0, os.path.dirname(__file__))

if os.path.exists("smoke_test_v2.db"):
    os.remove("smoke_test_v2.db")

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        raise SystemExit(1)


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def main():
    # --- setup: signup + doctor ---
    r = client.post("/auth/signup", json={"clinic_name": "Dr. Iyer Clinic", "email": "owner@iyer.com", "password": "pass1234"})
    check("signup", r.status_code == 200)
    token = r.json()["access_token"]
    h = auth_headers(token)

    r = client.post("/doctors", json={"name": "Dr. Iyer", "specialization": "General"}, headers=h)
    check("create doctor", r.status_code == 200)
    doctor_id = r.json()["id"]

    # --- Patient Registry: returning patient detection by mobile ---
    r = client.post("/queue/walkin", json={"doctor_id": doctor_id, "patient_name": "Rahul Sharma", "patient_mobile": "9800000021"}, headers=h)
    check("first walk-in for Rahul", r.status_code == 200)

    r = client.post("/public/book", json={"doctor_id": doctor_id, "patient_name": "Rahul Sharma", "patient_mobile": "9800000021"})
    check("second (online) booking, same mobile", r.status_code == 200)

    r = client.get("/patients", headers=h)
    check("patients list works", r.status_code == 200)
    patients = r.json()
    check("exactly ONE patient record for Rahul (deduped)", len([p for p in patients if p["mobile"] == "9800000021"]) == 1)

    rahul = [p for p in patients if p["mobile"] == "9800000021"][0]
    r = client.get(f"/patients/{rahul['id']}", headers=h)
    check("patient detail works", r.status_code == 200)
    detail = r.json()
    check("patient has 2 total bookings (1 walkin + 1 online)", detail["total_bookings"] == 2)
    check("patient has 1 walkin", detail["walkins"] == 1)
    check("patient has 1 online booking", detail["online_bookings"] == 1)
    check("patient history has 2 entries", len(detail["history"]) == 2)

    # --- Different mobile -> different (new) patient, no false merge ---
    r = client.post("/queue/walkin", json={"doctor_id": doctor_id, "patient_name": "Rahul Sharma", "patient_mobile": "9800000099"}, headers=h)
    check("walk-in for a different Rahul Sharma (diff mobile)", r.status_code == 200)
    r = client.get("/patients", headers=h)
    rahuls = [p for p in r.json() if p["name"] == "Rahul Sharma"]
    check("two distinct Rahul Sharma patient records (not merged by name)", len(rahuls) == 2)

    # --- No mobile at all still books fine, just no dedup ---
    r = client.post("/queue/walkin", json={"doctor_id": doctor_id, "patient_name": "No Mobile Patient"}, headers=h)
    check("walk-in with no mobile still works", r.status_code == 200)

    # --- Support tickets ---
    r = client.get("/support/faq")
    check("FAQ endpoint works", r.status_code == 200 and len(r.json()) > 0)

    r = client.post("/support/tickets", json={
        "category": "Queue Problem", "subject": "Token stuck", "description": "Token #5 won't advance."
    }, headers=h)
    check("create support ticket", r.status_code == 200)
    ticket = r.json()
    check("ticket has a ticket_number", bool(ticket["ticket_number"]))
    check("ticket defaults to OPEN", ticket["status"] == "OPEN")
    check("ticket email_notified is False (no SMTP configured in test env)", ticket["email_notified"] is False)

    r = client.get("/support/tickets", headers=h)
    check("list tickets works", r.status_code == 200 and len(r.json()) == 1)

    # --- Settings ---
    r = client.get("/settings", headers=h)
    check("get settings works (auto-created defaults)", r.status_code == 200)
    s = r.json()
    check("default online_booking_enabled is True", s["online_booking_enabled"] is True)
    check("whatsapp_provider_configured is False (no Twilio creds in test env)", s["whatsapp_provider_configured"] is False)

    r = client.put("/settings", json={"online_booking_enabled": False}, headers=h)
    check("update settings works", r.status_code == 200 and r.json()["online_booking_enabled"] is False)

    r = client.post("/public/book", json={"doctor_id": doctor_id, "patient_name": "Blocked Patient"})
    check("online booking blocked when disabled in settings", r.status_code == 403)

    r = client.put("/settings", json={"online_booking_enabled": True}, headers=h)
    check("re-enable online booking", r.status_code == 200)

    r = client.put("/settings", json={"walkin_enabled": False}, headers=h)
    check("disable walk-ins", r.status_code == 200)
    r = client.post("/queue/walkin", json={"doctor_id": doctor_id, "patient_name": "Blocked Walkin"}, headers=h)
    check("walk-in blocked when disabled in settings", r.status_code == 403)

    # --- WhatsApp message log (no provider configured -> logged as FAILED, never fake-SENT) ---
    r = client.get("/whatsapp/status", headers=h)
    check("whatsapp status endpoint works", r.status_code == 200 and r.json()["configured"] is False)

    r = client.get("/whatsapp/messages", headers=h)
    check("whatsapp messages log reachable", r.status_code == 200)
    messages = r.json()
    # No Twilio creds configured in this test env — every attempted booking-confirmation
    # send is still logged (never silently dropped), but honestly as FAILED with a reason,
    # never as a fake SENT.
    check("booking-confirmation attempts were logged", len(messages) > 0)
    check("all logged messages are FAILED, not fake-SENT, when unconfigured", all(m["status"] == "FAILED" for m in messages))
    check("failure reason explains WhatsApp isn't configured", all("not configured" in (m["failure_reason"] or "") for m in messages))

    print("\nAll v2 smoke tests passed.")


if __name__ == "__main__":
    main()
