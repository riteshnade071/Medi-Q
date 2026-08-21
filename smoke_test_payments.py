"""
Smoke tests for optional payment-amount tracking on tokens: recording an
amount at walk-in time, adding/editing/clearing it later via PATCH, it
showing up in patient visit history, and rolling into the dashboard's daily
total — all optional and clinic-isolated.
"""
import os
os.environ["DATABASE_URL"] = "sqlite:///./smoke_test_payments.db"

import sys
sys.path.insert(0, os.path.dirname(__file__))

if os.path.exists("smoke_test_payments.db"):
    os.remove("smoke_test_payments.db")

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
    r = client.post("/auth/signup", json={"clinic_name": "Payment Test Clinic", "email": "owner@paytest.com", "password": "pass1234"})
    h = auth_headers(r.json()["access_token"])

    d = client.post("/doctors", json={"name": "Dr. Pay", "specialization": "GP"}, headers=h).json()

    # --- Walk-in with an amount ---
    r = client.post("/queue/walkin", json={"doctor_id": d["id"], "patient_name": "Ramesh", "amount_paid": 500}, headers=h)
    check("walk-in with amount succeeds", r.status_code == 200)
    check("amount_paid recorded", r.json()["amount_paid"] == 500)
    ramesh_id = r.json()["id"]

    # --- Walk-in without an amount (fully optional) ---
    r = client.post("/queue/walkin", json={"doctor_id": d["id"], "patient_name": "Suresh"}, headers=h)
    check("walk-in without amount succeeds", r.status_code == 200)
    check("amount_paid is null when omitted", r.json()["amount_paid"] is None)
    suresh_id = r.json()["id"]

    # --- Set the amount later (e.g. paid at counter after booking) ---
    r = client.patch(f"/queue/token/{suresh_id}/payment", json={"amount_paid": 300}, headers=h)
    check("setting amount later succeeds", r.status_code == 200 and r.json()["amount_paid"] == 300)

    # --- Edit an existing amount ---
    r = client.patch(f"/queue/token/{ramesh_id}/payment", json={"amount_paid": 650}, headers=h)
    check("editing an existing amount succeeds", r.status_code == 200 and r.json()["amount_paid"] == 650)

    # --- Clear an amount (send null) ---
    r = client.patch(f"/queue/token/{ramesh_id}/payment", json={"amount_paid": None}, headers=h)
    check("clearing an amount succeeds", r.status_code == 200 and r.json()["amount_paid"] is None)
    # restore it for the rest of the test
    client.patch(f"/queue/token/{ramesh_id}/payment", json={"amount_paid": 500}, headers=h)

    # --- Negative amount rejected ---
    r = client.patch(f"/queue/token/{ramesh_id}/payment", json={"amount_paid": -50}, headers=h)
    check("negative amount rejected", r.status_code == 400)

    # --- Unknown token id ---
    r = client.patch("/queue/token/does-not-exist/payment", json={"amount_paid": 100}, headers=h)
    check("unknown token id returns 404", r.status_code == 404)

    # --- Patient visit history shows the amount ---
    patients = client.get("/patients", headers=h).json()
    ramesh_patient = next(p for p in patients if p["name"] == "Ramesh")
    detail = client.get(f"/patients/{ramesh_patient['id']}", headers=h).json()
    check("visit history includes amount_paid", detail["history"][0]["amount_paid"] == 500)

    # --- Dashboard total rolls up both amounts ---
    dash = client.get("/dashboard/summary", headers=h).json()
    check("dashboard total_collected_today sums recorded amounts", dash["total_collected_today"] == 800)

    # --- Clinic isolation: another clinic can't touch or see this one's tokens ---
    r = client.post("/auth/signup", json={"clinic_name": "Other Clinic", "email": "owner@otherpay.com", "password": "pass1234"})
    h2 = auth_headers(r.json()["access_token"])
    r = client.patch(f"/queue/token/{ramesh_id}/payment", json={"amount_paid": 999}, headers=h2)
    check("other clinic cannot modify this clinic's token payment", r.status_code == 404)
    dash2 = client.get("/dashboard/summary", headers=h2).json()
    check("other clinic's dashboard total is unaffected", dash2["total_collected_today"] == 0)

    print("\nAll payment-tracking smoke tests passed.")


if __name__ == "__main__":
    main()
