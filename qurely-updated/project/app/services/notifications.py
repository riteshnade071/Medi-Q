import os
import smtplib
from datetime import date
from email.mime.text import MIMEText
from typing import Optional

from sqlalchemy.orm import Session

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
ALERT_FROM_EMAIL = os.getenv("ALERT_FROM_EMAIL", SMTP_USER or "")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM")  # e.g. "whatsapp:+14155238886"

# how many WAITING patients ahead counts as "approaching" / "go now"
APPROACHING_THRESHOLD = int(os.getenv("QUEUE_APPROACHING_THRESHOLD", "3"))
NOW_THRESHOLD = int(os.getenv("QUEUE_NOW_THRESHOLD", "1"))


def is_configured() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS)


def is_whatsapp_configured() -> bool:
    return bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM)


def send_email(to_email: str, subject: str, body: str) -> tuple[bool, Optional[str]]:
    if not is_configured():
        return False, "SMTP not configured (set SMTP_HOST, SMTP_USER, SMTP_PASS)"
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = ALERT_FROM_EMAIL
    msg["To"] = to_email
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(ALERT_FROM_EMAIL, [to_email], msg.as_string())
        return True, None
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def send_whatsapp(to_number: str, body: str) -> tuple[bool, Optional[str]]:
    """Best-effort WhatsApp send via Twilio. Never raises."""
    if not is_whatsapp_configured():
        return False, "WhatsApp not configured (set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM)"
    if not to_number:
        return False, "No WhatsApp/mobile number on file for this patient"
    try:
        from twilio.rest import Client  # imported lazily

        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        to = to_number if to_number.startswith("whatsapp:") else f"whatsapp:{to_number}"
        client.messages.create(from_=TWILIO_WHATSAPP_FROM, to=to, body=body)
        return True, None
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def log_message(db: Session, clinic_id: str, message_type, body: str, to_number: Optional[str] = None,
                 patient_id: Optional[str] = None, token_id: Optional[str] = None,
                 sent: bool = False, error: Optional[str] = None) -> None:
    """Persists one row per attempted WhatsApp send (spec: Part 5 — message
    log). Status reflects what actually happened — FAILED (with the reason)
    when the provider rejected/couldn't be reached, SENT only on confirmed
    provider acceptance. Never raises; a logging failure must not break the
    action that triggered it."""
    from datetime import datetime
    from .. import models  # local import to avoid a circular import with queue.py

    try:
        db.add(models.WhatsAppMessage(
            clinic_id=clinic_id,
            patient_id=patient_id,
            token_id=token_id,
            to_number=to_number,
            message_type=message_type,
            body=body,
            status=models.WhatsAppMessageStatus.SENT if sent else models.WhatsAppMessageStatus.FAILED,
            failure_reason=None if sent else (error or "WhatsApp not configured"),
            sent_at=datetime.utcnow() if sent else None,
        ))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()


def notify_booking_confirmation(db: Session, token, doctor) -> None:
    """Best-effort WhatsApp booking confirmation, fired right after a token is
    created. Never raises — a notification failure must never fail the booking
    itself, which is why this isn't wrapped around the DB commit."""
    from .. import models  # local import to avoid a circular import with queue.py

    if not token.patient_mobile:
        return
    body = (
        f"\u2705 {doctor.name}: Your token is #{token.token_number}. "
        f"We'll message you as your turn approaches."
    )
    sent, error = send_whatsapp(token.patient_mobile, body)
    log_message(
        db, clinic_id=token.clinic_id, message_type=models.WhatsAppMessageType.BOOKING_CONFIRMATION,
        body=body, to_number=token.patient_mobile, patient_id=token.patient_id, token_id=token.id,
        sent=sent, error=error,
    )


def notify_approaching_patients(db: Session, doctor) -> None:
    """Called after every 'call next'. Walks today's WAITING patients for this doctor
    and WhatsApps the ones who just crossed the 'approaching' or 'go now' threshold.
    Best-effort and idempotent (won't re-send once a flag is set) — never raises,
    since a notification failure should never break the queue-advance action."""
    from .. import models  # local import to avoid a circular import with queue.py

    if not is_whatsapp_configured():
        return

    clinic_settings = db.query(models.ClinicSettings).filter(models.ClinicSettings.clinic_id == doctor.clinic_id).first()
    if clinic_settings and not clinic_settings.whatsapp_enabled:
        return
    approaching_threshold = clinic_settings.approaching_threshold if clinic_settings else APPROACHING_THRESHOLD

    today = date.today()
    waiting = (
        db.query(models.Token)
        .filter(
            models.Token.doctor_id == doctor.id,
            models.Token.queue_date == today,
            models.Token.status == models.TokenStatus.WAITING,
        )
        .order_by(models.Token.token_number.asc())
        .all()
    )

    for position, token in enumerate(waiting):  # position 0 = next up
        if position < NOW_THRESHOLD and not token.notified_now:
            body = f"\U0001F6A8 {doctor.name}: Please reach the clinic now — your token #{token.token_number} is next."
            sent, error = send_whatsapp(token.patient_mobile, body)
            log_message(
                db, clinic_id=doctor.clinic_id, message_type=models.WhatsAppMessageType.YOUR_TURN,
                body=body, to_number=token.patient_mobile, patient_id=token.patient_id, token_id=token.id,
                sent=sent, error=error,
            )
            if sent:
                token.notified_now = True
                token.notified_approaching = True
        elif position < approaching_threshold and not token.notified_approaching:
            body = (
                f"\U0001F514 {doctor.name}: Your turn is approaching — token #{token.token_number}, "
                f"about {position} patient(s) ahead of you."
            )
            sent, error = send_whatsapp(token.patient_mobile, body)
            log_message(
                db, clinic_id=doctor.clinic_id, message_type=models.WhatsAppMessageType.APPROACHING,
                body=body, to_number=token.patient_mobile, patient_id=token.patient_id, token_id=token.id,
                sent=sent, error=error,
            )
            if sent:
                token.notified_approaching = True

    db.commit()
