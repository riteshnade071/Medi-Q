from sqlalchemy.orm import Session
from sqlalchemy import func

from .. import models


def find_or_create_patient(db: Session, clinic_id: str, name: str, mobile: str | None) -> models.Patient | None:
    """Resolves a booking's (name, mobile) to a single clinic-level Patient
    record. Mobile number is the identifier — matching by name alone is
    explicitly disallowed by spec (two "Rahul Sharma"s are not necessarily
    the same person). No mobile on file -> no dedupe possible, so a fresh,
    unlinked Patient row is created (still lets us track walk-ins with no
    phone in the registry without blocking the booking).
    """
    mobile = (mobile or "").strip() or None
    name = (name or "").strip()

    if mobile:
        existing = (
            db.query(models.Patient)
            .filter(models.Patient.clinic_id == clinic_id, models.Patient.mobile == mobile)
            .first()
        )
        if existing:
            # Keep the most recently given name fresh (people update spellings,
            # correct typos) without touching anything else about the record.
            if name and existing.name != name:
                existing.name = name
            return existing

    patient = models.Patient(clinic_id=clinic_id, name=name or "Unknown", mobile=mobile)
    db.add(patient)
    db.flush()  # get patient.id without a full commit; caller commits with the booking
    return patient


def patient_stats(db: Session, patient: models.Patient) -> dict:
    """All visit/booking counters derived live from Token rows — never stored,
    so they can't drift out of sync with the real queue history."""
    rows = db.query(models.Token).filter(models.Token.patient_id == patient.id).all()

    total_visits = sum(1 for t in rows if t.status == models.TokenStatus.DONE)
    online = sum(1 for t in rows if t.source == models.TokenSource.ONLINE)
    walkin = sum(1 for t in rows if t.source == models.TokenSource.WALKIN)
    cancellations = sum(1 for t in rows if t.status == models.TokenStatus.CANCELLED)
    no_shows = sum(1 for t in rows if t.status == models.TokenStatus.NO_SHOW)

    visit_dates = sorted(t.created_at for t in rows) if rows else []
    first_visit = visit_dates[0] if visit_dates else None
    last_visit = visit_dates[-1] if visit_dates else None

    return {
        "total_bookings": len(rows),
        "total_visits": total_visits,
        "online_bookings": online,
        "walkins": walkin,
        "cancellations": cancellations,
        "no_shows": no_shows,
        "first_visit": first_visit,
        "last_visit": last_visit,
    }


def search_patients(db: Session, clinic_id: str, query: str | None):
    q = db.query(models.Patient).filter(models.Patient.clinic_id == clinic_id)
    if query:
        query = query.strip()
        like = f"%{query}%"
        q = q.filter((models.Patient.name.ilike(like)) | (models.Patient.mobile.ilike(like)))
    return q.order_by(models.Patient.created_at.desc()).all()
