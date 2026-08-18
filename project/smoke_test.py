"""
End-to-end smoke test using FastAPI's TestClient (no server needed to be running).
Covers: signup -> doctor create -> walk-in -> online booking -> call-next -> skip ->
no-show -> public status -> dashboard summary -> multi-tenant isolation.
"""
import os
os.environ["DATABASE_URL"] = "sqlite:///./smoke_test.db"

import sys
sys.path.insert(0, os.path.dirname(__file__))

if os.path.exists("smoke_test.db"):
    os.remove("smoke_test.db")

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
    # --- health check ---
    r = client.get("/health")
    check("health endpoint", r.status_code == 200 and r.json() == {"status": "ok"})

    # --- signup ---
    r = client.post("/auth/signup", json={"clinic_name": "Dr. Sharma Clinic", "email": "owner1@clinic.com", "password": "pass1234"})
    check("signup", r.status_code == 200)
    token1 = r.json()["access_token"]
    h1 = auth_headers(token1)

    r = client.post("/auth/login", data={"username": "owner1@clinic.com", "password": "pass1234"})
    check("login", r.status_code == 200)

    r = client.get("/auth/me", headers=h1)
    check("profile has clinic_slug", r.status_code == 200 and r.json()["clinic_slug"])
    slug = r.json()["clinic_slug"]

    # --- doctor ---
    r = client.post("/doctors", headers=h1, json={"name": "Dr. Sharma", "specialization": "General Physician", "avg_consult_minutes": 5})
    check("create doctor", r.status_code == 200)
    doctor_id = r.json()["id"]

    r = client.get("/doctors", headers=h1)
    check("list doctors", r.status_code == 200 and len(r.json()) == 1)

    # --- public doctor listing ---
    r = client.get(f"/public/clinic/{slug}/doctors")
    check("public doctor listing", r.status_code == 200 and len(r.json()) == 1)

    # --- walk-in ---
    r = client.post("/queue/walkin", headers=h1, json={"doctor_id": doctor_id, "patient_name": "Walkin Person", "patient_mobile": None})
    check("add walk-in", r.status_code == 200 and r.json()["token_number"] == 1)

    # --- online booking ---
    r = client.post("/public/book", json={"doctor_id": doctor_id, "patient_name": "Online Patient", "patient_mobile": "+911234567890"})
    check("online booking", r.status_code == 200 and r.json()["token_number"] == 2)
    booking = r.json()
    check("ahead count correct before anyone called", booking["ahead_count"] == 1)
    public_code = booking["public_code"]

    # --- public status before call ---
    r = client.get(f"/public/status/{public_code}")
    check("public status works", r.status_code == 200 and r.json()["status"] == "WAITING")

    # --- doctor queue view ---
    r = client.get(f"/queue/{doctor_id}", headers=h1)
    check("queue view shows 2 waiting", r.status_code == 200 and len(r.json()["waiting"]) == 2)
    check("no one serving yet", r.json()["current_serving"] is None)

    # --- call next (should serve token #1, the walk-in) ---
    r = client.post(f"/queue/{doctor_id}/next", headers=h1)
    check("call next serves token 1", r.status_code == 200 and r.json()["token_number"] == 1)

    r = client.get(f"/queue/{doctor_id}", headers=h1)
    check("current serving is now #1", r.json()["current_serving"]["token_number"] == 1)
    check("1 waiting left", len(r.json()["waiting"]) == 1)

    # --- public status for online patient should now show ahead=1 ---
    # (the walk-in, token #1, is currently SERVING — still "ahead" of token #2
    # from the patient's point of view, even though technically no one is WAITING
    # ahead of them anymore. See services/queue.py::ahead_count for the rationale.)
    r = client.get(f"/public/status/{public_code}")
    check("online patient sees walk-in still ahead while being served (ahead=1)", r.json()["ahead_count"] == 1)
    check("current_serving visible to patient", r.json()["current_serving"] == 1)

    # --- call next again (serves token #2, the online patient) ---
    r = client.post(f"/queue/{doctor_id}/next", headers=h1)
    check("call next serves token 2", r.status_code == 200 and r.json()["token_number"] == 2)

    r = client.get(f"/public/status/{public_code}")
    check("online patient status is SERVING", r.json()["status"] == "SERVING")

    # --- call next with empty waiting list should finish current and return None ---
    r = client.post(f"/queue/{doctor_id}/next", headers=h1)
    check("call next with empty queue returns null", r.status_code == 200 and r.json() is None)

    r = client.get(f"/public/status/{public_code}")
    check("online patient status is DONE", r.json()["status"] == "DONE")

    # --- public queue board ---
    r = client.get(f"/public/queue/{doctor_id}")
    check("public queue board works", r.status_code == 200 and r.json()["waiting_count"] == 0)

    # --- no-show flow ---
    r = client.post("/queue/walkin", headers=h1, json={"doctor_id": doctor_id, "patient_name": "No Show Guy", "patient_mobile": None})
    token3_id = r.json()["id"]
    r = client.post(f"/queue/token/{token3_id}/no-show", headers=h1)
    check("mark no-show works", r.status_code == 200)

    # --- dashboard summary ---
    r = client.get("/dashboard/summary", headers=h1)
    check("dashboard summary works", r.status_code == 200)
    summary = r.json()
    check("dashboard total tokens = 3", summary["total_tokens_today"] == 3)
    check("dashboard no_show_count = 1", summary["no_show_count"] == 1)

    # --- billing ---
    r = client.get("/billing/status", headers=h1)
    check("billing status works", r.status_code == 200 and r.json()["plan"] == "trial")

    # --- cancel flow (patient-initiated) ---
    r = client.post("/public/book", json={"doctor_id": doctor_id, "patient_name": "Cancel Me", "patient_mobile": None})
    check("booking for cancel test", r.status_code == 200)
    cancel_code = r.json()["public_code"]
    r = client.post(f"/public/cancel/{cancel_code}")
    check("cancel token works", r.status_code == 200 and r.json()["status"] == "CANCELLED")
    r = client.post(f"/public/cancel/{cancel_code}")
    check("cancelling an already-cancelled token is rejected", r.status_code == 400)
    r = client.get(f"/public/status/{cancel_code}")
    check("cancelled token status persists", r.json()["status"] == "CANCELLED")

    # --- complete (without auto-advancing) and recall ---
    r = client.post("/queue/walkin", headers=h1, json={"doctor_id": doctor_id, "patient_name": "Complete Test", "patient_mobile": "+911111111111"})
    check("walk-in for complete test", r.status_code == 200)
    r = client.post(f"/queue/{doctor_id}/next", headers=h1)
    check("call next to serve the complete-test patient", r.status_code == 200 and r.json()["patient_name"] == "Complete Test")

    r = client.post(f"/queue/{doctor_id}/recall", headers=h1)
    check("recall works while someone is being served", r.status_code == 200)

    r = client.post(f"/queue/{doctor_id}/complete", headers=h1)
    check("complete works and marks current DONE", r.status_code == 200 and r.json()["status"] == "DONE")

    r = client.post(f"/queue/{doctor_id}/complete", headers=h1)
    check("complete with nobody serving is rejected", r.status_code == 400)

    r = client.post(f"/queue/{doctor_id}/recall", headers=h1)
    check("recall with nobody serving is rejected", r.status_code == 400)

    # --- concurrent booking safety: no duplicate token numbers, no crashes ---
    import concurrent.futures
    r = client.post("/doctors", headers=h1, json={"name": "Dr. Concurrent", "avg_consult_minutes": 5})
    concurrent_doctor_id = r.json()["id"]

    def book_one(i):
        res = client.post("/public/book", json={"doctor_id": concurrent_doctor_id, "patient_name": f"Race {i}", "patient_mobile": None})
        return res.status_code, res.json() if res.status_code == 200 else None

    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex:
        race_results = list(ex.map(book_one, range(15)))
    race_numbers = sorted(r[1]["token_number"] for r in race_results if r[0] == 200)
    check("no crashes under concurrent booking (all requests handled)", all(r[0] in (200, 429) for r in race_results))
    check("no duplicate token numbers under concurrent booking", len(race_numbers) == len(set(race_numbers)))

    # --- multi-tenant isolation ---
    r = client.post("/auth/signup", json={"clinic_name": "Second Clinic", "email": "owner2@clinic2.com", "password": "pass5678"})
    token2 = r.json()["access_token"]
    h2 = auth_headers(token2)

    r = client.get("/doctors", headers=h2)
    check("clinic2 sees zero doctors (isolation)", r.status_code == 200 and len(r.json()) == 0)

    r = client.get("/dashboard/summary", headers=h2)
    check("clinic2 dashboard is empty (isolation)", r.json()["total_tokens_today"] == 0)

    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()
