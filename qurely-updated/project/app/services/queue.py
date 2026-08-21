import time
from datetime import date, datetime
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, OperationalError

from .. import models
from . import notifications, patients as patients_service


class QueueError(Exception):
    """Raised for expected, user-facing queue failures (as opposed to bugs)."""


def _today() -> date:
    return date.today()


def log_action(db: Session, clinic_id: str, action: str, detail: str = None, user_id: str = None) -> None:
    """Best-effort audit log write. Never raises — a logging failure should
    never break the underlying action it's recording."""
    try:
        db.add(models.AuditLog(clinic_id=clinic_id, user_id=user_id, action=action, detail=detail))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()


def next_token_number(db: Session, doctor_id: str, queue_date: date) -> int:
    max_num = (
        db.query(func.max(models.Token.token_number))
        .filter(models.Token.doctor_id == doctor_id, models.Token.queue_date == queue_date)
        .scalar()
    )
    return (max_num or 0) + 1


def waiting_tokens(db: Session, doctor_id: str, queue_date: date = None):
    queue_date = queue_date or _today()
    return (
        db.query(models.Token)
        .filter(
            models.Token.doctor_id == doctor_id,
            models.Token.queue_date == queue_date,
            models.Token.status == models.TokenStatus.WAITING,
        )
        .order_by(models.Token.token_number.asc())
        .all()
    )


def current_serving(db: Session, doctor_id: str, queue_date: date = None):
    queue_date = queue_date or _today()
    return (
        db.query(models.Token)
        .filter(
            models.Token.doctor_id == doctor_id,
            models.Token.queue_date == queue_date,
            models.Token.status == models.TokenStatus.SERVING,
        )
        .first()
    )


def ahead_count(db: Session, doctor_id: str, token_number: int, queue_date: date = None) -> int:
    """How many patients are genuinely still ahead of this token: everyone
    WAITING with a smaller number, *plus* whoever is currently SERVING (if
    their number is smaller) — the person in the room right now is still
    "ahead of you" from the patient's point of view, even though their
    status isn't WAITING anymore. Excluding them would make a patient's
    "ahead" count read as one less than reality for the whole time their
    predecessor is being seen."""
    queue_date = queue_date or _today()
    return (
        db.query(models.Token)
        .filter(
            models.Token.doctor_id == doctor_id,
            models.Token.queue_date == queue_date,
            models.Token.status.in_([models.TokenStatus.WAITING, models.TokenStatus.SERVING]),
            models.Token.token_number < token_number,
        )
        .count()
    )


def estimate_wait_minutes(doctor: models.Doctor, ahead: int) -> int:
    return max(0, ahead) * max(1, doctor.avg_consult_minutes or 6)


MAX_BOOKING_RETRIES = 5


def create_booking(db: Session, doctor: models.Doctor, patient_name: str, patient_mobile: str, source: models.TokenSource) -> models.Token:
    """Server-side token assignment with retry-on-conflict.

    Two patients booking at the exact same instant will race to read the
    current max token_number. The DB-level UNIQUE(doctor_id, queue_date,
    token_number) constraint guarantees they can never both succeed with the
    same number — but the loser needs to be retried with a fresh number
    rather than surfaced as a crash. This loop does that, and also absorbs
    transient "database is locked" errors under SQLite (Postgres in
    production doesn't hit that particular case, but it's a cheap safety net
    either way).
    """
    queue_date = _today()
    last_error = None

    # Resolve/create the Patient Registry record *before* the retry loop —
    # find-or-create is itself idempotent (same clinic+mobile always returns
    # the same row), so doing it once outside the loop avoids creating
    # duplicate patients if a token_number attempt collides and retries.
    patient = patients_service.find_or_create_patient(db, doctor.clinic_id, patient_name, patient_mobile)

    for attempt in range(MAX_BOOKING_RETRIES):
        token_number = next_token_number(db, doctor.id, queue_date)
        token = models.Token(
            clinic_id=doctor.clinic_id,
            doctor_id=doctor.id,
            patient_id=patient.id if patient else None,
            queue_date=queue_date,
            token_number=token_number,
            patient_name=patient_name,
            patient_mobile=patient_mobile,
            source=source,
        )
        db.add(token)
        try:
            db.commit()
        except IntegrityError as e:
            db.rollback()
            last_error = e
            continue
        except OperationalError as e:
            db.rollback()
            last_error = e
            time.sleep(0.05 * (attempt + 1))
            continue
        else:
            db.refresh(token)
            notifications.notify_booking_confirmation(db, token, doctor)
            return token

    raise QueueError("Could not assign a token right now — please try again.") from last_error


def call_next(db: Session, doctor: models.Doctor, user_id: str = None) -> models.Token | None:
    """Marks the currently-serving token DONE (if any), then promotes the earliest
    WAITING token to SERVING. Returns the new current-serving token, or None if
    the queue is empty. Fires WhatsApp notifications for patients now approaching."""
    queue_date = _today()
    serving = current_serving(db, doctor.id, queue_date)
    if serving:
        serving.status = models.TokenStatus.DONE
        serving.completed_at = datetime.utcnow()

    nxt = (
        db.query(models.Token)
        .filter(
            models.Token.doctor_id == doctor.id,
            models.Token.queue_date == queue_date,
            models.Token.status == models.TokenStatus.WAITING,
        )
        .order_by(models.Token.token_number.asc())
        .first()
    )
    if nxt:
        nxt.status = models.TokenStatus.SERVING
        nxt.called_at = datetime.utcnow()

    db.commit()
    if nxt:
        db.refresh(nxt)
    notifications.notify_approaching_patients(db, doctor)
    log_action(db, doctor.clinic_id, "queue.next", detail=f"doctor={doctor.id} new_serving={nxt.token_number if nxt else None}", user_id=user_id)
    return nxt


def complete_current(db: Session, doctor: models.Doctor, user_id: str = None) -> models.Token | None:
    """Marks the currently-serving token DONE without auto-advancing the queue.
    Distinct from `call_next`, which does both in one step — this exists for
    staff who want to close out a consultation without immediately calling
    the next patient (e.g. the room needs a minute to reset)."""
    queue_date = _today()
    serving = current_serving(db, doctor.id, queue_date)
    if not serving:
        raise QueueError("No patient is currently being served")
    serving.status = models.TokenStatus.DONE
    serving.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(serving)
    log_action(db, doctor.clinic_id, "queue.complete", detail=f"token={serving.token_number}", user_id=user_id)
    return serving


def recall_current(db: Session, doctor: models.Doctor, user_id: str = None) -> models.Token | None:
    """Re-announces the currently-serving patient (e.g. they didn't show up at
    the door the first time) without changing queue position. Does NOT
    advance the queue. Best-effort re-sends the 'your turn' WhatsApp alert if
    the patient has a mobile number on file."""
    queue_date = _today()
    serving = current_serving(db, doctor.id, queue_date)
    if not serving:
        raise QueueError("No patient is currently being served to recall")
    if serving.patient_mobile:
        body = f"\U0001F514 {doctor.name}: You're being called again — token #{serving.token_number}, please come to the counter now."
        sent, error = notifications.send_whatsapp(serving.patient_mobile, body)
        notifications.log_message(
            db, clinic_id=doctor.clinic_id, message_type=models.WhatsAppMessageType.RECALL,
            body=body, to_number=serving.patient_mobile, patient_id=serving.patient_id, token_id=serving.id,
            sent=sent, error=error,
        )
    log_action(db, doctor.clinic_id, "queue.recall", detail=f"token={serving.token_number}", user_id=user_id)
    return serving


def skip_current(db: Session, doctor: models.Doctor, user_id: str = None) -> models.Token | None:
    queue_date = _today()
    serving = current_serving(db, doctor.id, queue_date)
    if serving:
        serving.status = models.TokenStatus.SKIPPED
        serving.completed_at = datetime.utcnow()
        db.commit()
        log_action(db, doctor.clinic_id, "queue.skip", detail=f"token={serving.token_number}", user_id=user_id)
    return call_next(db, doctor, user_id=user_id)


def mark_no_show(db: Session, token: models.Token, user_id: str = None):
    token.status = models.TokenStatus.NO_SHOW
    token.completed_at = datetime.utcnow()
    db.commit()
    log_action(db, token.clinic_id, "queue.no_show", detail=f"token={token.token_number}", user_id=user_id)


def cancel_token(db: Session, token: models.Token) -> models.Token:
    """Patient-initiated cancellation. Only a WAITING token can be cancelled —
    once called/served/done there's nothing meaningful left to cancel."""
    if token.status != models.TokenStatus.WAITING:
        raise QueueError("Only a waiting token can be cancelled")
    token.status = models.TokenStatus.CANCELLED
    token.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(token)
    if token.patient_mobile:
        body = f"Your token #{token.token_number} has been cancelled. You may book another token when required."
        sent, error = notifications.send_whatsapp(token.patient_mobile, body)
        notifications.log_message(
            db, clinic_id=token.clinic_id, message_type=models.WhatsAppMessageType.CANCELLATION,
            body=body, to_number=token.patient_mobile, patient_id=token.patient_id, token_id=token.id,
            sent=sent, error=error,
        )
    log_action(db, token.clinic_id, "queue.cancel", detail=f"token={token.token_number} (patient-initiated)")
    return token
