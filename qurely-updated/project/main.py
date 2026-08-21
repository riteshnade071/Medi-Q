import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.database import Base, engine, run_startup_migrations
from app import models  # noqa: F401 (ensures models are registered before create_all)
from app.routers import (
    auth_router, doctors_router, queue_router, public_router,
    billing_router, dashboard_router, patients_router, support_router,
    settings_router, whatsapp_router,
)

# Order matters: create_all() first (adds any brand-new tables, e.g.
# patients/support_tickets/whatsapp_messages/clinic_settings on first boot
# against the existing live DB), then run_startup_migrations() to add any
# new columns to tables that already existed before this deploy (e.g.
# tokens.patient_id) — create_all() alone never alters an existing table.
Base.metadata.create_all(bind=engine)
_migrated = run_startup_migrations(engine)
if _migrated:
    print(f"[startup migration] added columns: {', '.join(_migrated)}")

app = FastAPI(title="Clinic Queue API", version="1.0.0")

# CORS_ORIGINS lets you restrict to a specific frontend domain (comma-separated)
# once one exists separately from this app's own built-in static pages. Left
# unset, it defaults to "*" for easy setup — safe here because auth uses a
# Bearer token (Authorization header), not cookies, so allow_credentials is
# correctly False. ["*"] + allow_credentials=True is an invalid combination
# browsers reject; the previous config silently relied on same-origin usage
# to avoid ever hitting that in practice.
_cors_origins_env = os.getenv("CORS_ORIGINS", "*")
_cors_origins = ["*"] if _cors_origins_env == "*" else [o.strip() for o in _cors_origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(doctors_router.router)
app.include_router(queue_router.router)
app.include_router(public_router.router)
app.include_router(billing_router.router)
app.include_router(dashboard_router.router)
app.include_router(patients_router.router)
app.include_router(support_router.router)
app.include_router(settings_router.router)
app.include_router(whatsapp_router.router)


@app.get("/api")
def root():
    return {"status": "ok", "service": "Clinic Queue API"}


@app.get("/health")
def health():
    return {"status": "ok"}


_static_dir = os.path.join(os.path.dirname(__file__), "app", "static")


def _serve(filename: str):
    path = os.path.join(_static_dir, filename)
    if os.path.isfile(path):
        return FileResponse(path)
    return {"error": f"{filename} not found"}


# Pretty, no-app-install patient URLs — client-side JS reads the id from the path.
@app.get("/book/{slug}")
def serve_booking_page(slug: str):
    return _serve("public.html")


@app.get("/q/{doctor_id}")
def serve_queue_board(doctor_id: str):
    return _serve("public.html")


if os.path.isdir(_static_dir):
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
