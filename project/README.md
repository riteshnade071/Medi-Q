# Clinic Queue — Digital Token & Live Queue System

Multi-tenant FastAPI + PostgreSQL backend with two built-in web UIs (no
separate frontend deploy needed):

- **Staff app** (`/`) — login, live queue dashboard per doctor (Next / Skip /
  Complete / Recall / No-show), walk-in entry, doctor management, billing status.
- **Patient page** (`/book/{clinic-slug}`) — no app install, opens in any
  mobile browser: pick a doctor, enter name + mobile, get a token, watch the
  queue live, cancel if plans change. Also doubles as a **waiting-room TV
  display** at `/q/{doctor_id}` — just the live "now serving" number, nothing
  patient-specific.

Every clinic's data is scoped to its `clinic_id` — nothing leaks across
clinics. Sold per-clinic, one deployment serves many.

## Quick start (local)

```bash
cp _env.example .env    # fill in DATABASE_URL and JWT_SECRET
pip install -r requirements.txt --break-system-packages
uvicorn main:app --reload --port 8000
```

Open **http://localhost:8000** for the staff app. Sign up a clinic, add a
doctor, then open `http://localhost:8000/book/<your-clinic-slug>` (shown on
the Queue tab after login) to try the patient side.

No Postgres yet? Skip `DATABASE_URL` in `.env` and it falls back to a local
`dev.db` SQLite file — fine for development. In production, `JWT_SECRET`
**must** be set to a real random value against a non-SQLite database — the
app refuses to boot otherwise, rather than silently running with a guessable
default secret.

## Testing it end-to-end

```bash
python3 smoke_test.py
```

38 checks covering signup, doctor setup, walk-ins, online booking, the full
call-next/complete/recall/skip/no-show/cancel flow, public patient status,
the live queue board, the dashboard summary, concurrent-booking safety (no
duplicate tokens, no crashes under simultaneous requests), and multi-tenant
isolation between two clinics.

## Deploying it live

**Render (recommended, has a free tier):**
1. Push this folder to a GitHub repo.
2. In Render, "New +" → "Blueprint" → point it at your repo. `render.yaml`
   sets up the web service *and* a free Postgres database automatically, and
   generates `JWT_SECRET` for you.
3. First deploy takes ~2 minutes. Your clinic's app is then live at
   `https://<your-service-name>.onrender.com`.
4. Health check: `GET /health` returns `{"status": "ok"}` — used by Render
   (and any uptime monitor) to confirm the service is alive.

Free tiers on Render sleep after inactivity — first request after idle can
take 20-30s. Fine for a demo, worth a paid tier once a real clinic depends on
it (patients waiting on a slow-loading queue page is a bad look).

Render's managed Postgres gives you a `postgres://` connection string; the
app normalizes this to `postgresql://` automatically (recent SQLAlchemy
otherwise refuses the legacy scheme), so you don't need to edit it by hand.

## What's built (MVP)

- Multi-tenant `Clinic` → `User` (staff login), `Doctor`, `Token` models,
  plus an append-only `AuditLog` for sensitive/administrative actions.
- JWT auth, trial/subscription gating (14-day trial, same pattern as our
  other product — data-mutating endpoints return 402 once it lapses, billing
  status endpoint always stays reachable). Refuses to boot with the default
  JWT secret against a real database. Passwords must be 8+ characters.
- Sequential per-doctor-per-day token numbers, shared between online
  bookings and walk-ins (`app/services/queue.py`), with **retry-on-conflict**
  so two patients booking at the exact same instant both succeed with
  distinct sequential numbers instead of one of them crashing.
- `POST /queue/{doctor_id}/next` — completes whoever's being served, promotes
  the next WAITING token, fires WhatsApp notifications to patients who just
  crossed the "approaching" or "go now" threshold (`QUEUE_APPROACHING_THRESHOLD`
  / `QUEUE_NOW_THRESHOLD` env vars, default 3 / 1). Also sends a WhatsApp
  booking confirmation the moment a token is created.
- `POST /queue/{doctor_id}/complete` — marks the current patient done
  *without* auto-advancing (for when staff need a moment before calling the
  next patient). `POST /queue/{doctor_id}/skip` and
  `POST /queue/token/{id}/no-show` for the messy real-world cases.
  `POST /queue/{doctor_id}/recall` re-pings a patient who didn't show up at
  the counter, without changing their queue position.
- `POST /public/cancel/{public_code}` — patients can cancel their own
  waiting token from the confirmation page.
- Estimated wait = doctor's average consultation minutes × patients ahead
  (counting whoever's currently being served, plus everyone still waiting
  ahead of you) — an estimate, not a guarantee, and it says so in the UI.
- Public, no-auth endpoints for booking and status polling — patients never
  need an account or the app installed. Rate-limited per IP to blunt
  accidental double-submits and casual abuse.
- `GET /dashboard/summary` — today's totals across all doctors, for the
  Queue tab's link box and future admin views.
- Staff dashboard buttons (Next/Skip/Complete/Recall/Add walk-in) are
  disabled while a request is in flight, so a slow connection can't cause a
  double-click to skip two patients instead of one.

## API summary

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness check — always `{"status":"ok"}` |
| `POST /auth/signup`, `POST /auth/login` | Clinic + staff auth, returns JWT (rate-limited) |
| `GET/PATCH /auth/me` | Staff profile, incl. WhatsApp number for future staff alerts |
| `POST/GET/PATCH/DELETE /doctors` | Doctor management |
| `POST /queue/walkin` | Add a walk-in patient to a doctor's queue |
| `GET /queue/{doctor_id}` | Full queue state for the staff dashboard |
| `POST /queue/{doctor_id}/next` | Call next patient (completes current + promotes next, fires notifications) |
| `POST /queue/{doctor_id}/complete` | Mark current patient done without advancing |
| `POST /queue/{doctor_id}/recall` | Re-ping the current patient without changing queue position |
| `POST /queue/{doctor_id}/skip` | Skip the current patient |
| `POST /queue/token/{id}/no-show` | Mark a waiting token no-show |
| `GET /public/clinic/{slug}/doctors` | Doctor list for the booking page |
| `POST /public/book` | Patient books a token (no auth, rate-limited) |
| `GET /public/status/{public_code}` | Patient's personal live status |
| `POST /public/cancel/{public_code}` | Patient cancels their own waiting token |
| `GET /public/queue/{doctor_id}` | Public live queue board (TV display) |
| `GET /dashboard/summary` | Today's totals across all doctors |
| `GET /billing/status` | Trial/subscription status (always reachable) |

## Environment variables

| Variable | Required? | Purpose |
|---|---|---|
| `DATABASE_URL` | Recommended in prod | Postgres connection string (falls back to local SQLite if unset) |
| `JWT_SECRET` | **Required in prod** | Long random string; app refuses to boot without it against a real DB |
| `CORS_ORIGINS` | Optional | Comma-separated allowed origins; defaults to `*` (safe here since auth uses Bearer tokens, not cookies) |
| `SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASS`/`ALERT_FROM_EMAIL` | Optional | Email alerts (not used by core queue features yet) |
| `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`/`TWILIO_WHATSAPP_FROM` | Optional | WhatsApp booking confirmation + approaching/now notifications |
| `QUEUE_APPROACHING_THRESHOLD`/`QUEUE_NOW_THRESHOLD` | Optional | How many patients-ahead trigger each notification (default 3/1) |

## Not built yet

- Actual payment processor integration — billing status is tracked, not charged
- Appointment-slot booking (currently token/queue-based only, as scoped for the MVP)
- Multiple branches per clinic
- Real receptionist/doctor/patient role separation — `owner`/`receptionist`
  exist as a field but every authenticated staff user currently has full
  access; a real role split needs per-endpoint permission checks
- OTP-based patient identification — currently patients are identified by an
  unguessable `public_code` link (a reasonable MVP capability-URL pattern),
  not a verified mobile number; a phone-number-based "retrieve my booking"
  flow would need OTP infrastructure layered on top
- Patient history across visits
- SMS fallback when a patient has no WhatsApp (Twilio SMS is a small addition
  to `app/services/notifications.py` if needed later)
- Structured application logging / external monitoring integration (Sentry,
  etc.) — currently only the audit log table and default Uvicorn access logs
- Shared-state rate limiting across multiple server instances — the current
  rate limiter is in-memory and per-process, which is correct for a single
  Render instance but would need Redis to work across multiple instances

