import os
from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db
from ..services import notifications

router = APIRouter(prefix="/support", tags=["support"])

DEFAULT_SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "zalteom111@gmail.com")

FAQ_ITEMS = [
    schemas.FAQItem(question="How do I add a walk-in?",
                     answer="Go to Queue, click 'Add Walk-in', enter the patient's mobile number first — "
                            "if they've visited before we'll attach the token to their existing profile "
                            "instead of creating a duplicate."),
    schemas.FAQItem(question="How does the live queue work?",
                     answer="The queue board polls the server every few seconds and always reflects the "
                            "database — refreshing the page, closing the browser, or a backend restart never "
                            "loses waiting patients."),
    schemas.FAQItem(question="How do I call the next patient?",
                     answer="Open Queue for the doctor and click 'Next'. This marks the current patient DONE "
                            "and promotes the earliest WAITING token to SERVING."),
    schemas.FAQItem(question="How do I find an existing patient?",
                     answer="Use the Patients tab and search by name or mobile number."),
    schemas.FAQItem(question="How do patients book online?",
                     answer="Share your clinic's booking link (/book/your-clinic-slug) — patients pick a "
                            "doctor and get a token with a live-queue link, no app install needed."),
    schemas.FAQItem(question="How do I cancel a token?",
                     answer="Patients can cancel from their own status/queue link. Only a WAITING token can "
                            "be cancelled."),
    schemas.FAQItem(question="How do WhatsApp notifications work?",
                     answer="When WhatsApp is configured (Settings), patients get a booking confirmation and "
                            "automatic alerts as their turn approaches. If it isn't configured, the queue "
                            "still works normally — notifications are just skipped."),
    schemas.FAQItem(question="How do I add a doctor?",
                     answer="Go to Doctors and click 'Add Doctor'. Each doctor gets an independent queue."),
]


@router.get("/faq", response_model=list[schemas.FAQItem])
def get_faq():
    return FAQ_ITEMS


@router.post("/tickets", response_model=schemas.SupportTicketOut)
def create_ticket(
    payload: schemas.SupportTicketCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_active_user),
):
    ticket = models.SupportTicket(
        clinic_id=user.clinic_id,
        user_id=user.id,
        category=payload.category,
        subject=payload.subject,
        description=payload.description,
        contact_email=payload.contact_email or user.email,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    clinic = db.query(models.Clinic).filter(models.Clinic.id == user.clinic_id).first()
    settings = db.query(models.ClinicSettings).filter(models.ClinicSettings.clinic_id == user.clinic_id).first()
    support_email = (settings.support_email if settings and settings.support_email else DEFAULT_SUPPORT_EMAIL)

    # Real email send attempt — if SMTP isn't configured this returns
    # False/reason and we simply don't claim it was sent. The ticket is
    # already safely in the database either way.
    body = (
        f"New support ticket {ticket.ticket_number}\n"
        f"Clinic: {clinic.name if clinic else user.clinic_id}\n"
        f"User: {user.email}\n"
        f"Category: {payload.category}\n"
        f"Subject: {payload.subject}\n\n"
        f"{payload.description}\n\n"
        f"Submitted: {ticket.created_at.isoformat()}"
    )
    sent, _error = notifications.send_email(support_email, f"[Qurely Support] {payload.subject}", body)
    if sent:
        ticket.email_notified = True
        db.commit()
        db.refresh(ticket)

    return ticket


@router.get("/tickets", response_model=list[schemas.SupportTicketOut])
def list_tickets(
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_active_user),
):
    return (
        db.query(models.SupportTicket)
        .filter(models.SupportTicket.clinic_id == user.clinic_id)
        .order_by(models.SupportTicket.created_at.desc())
        .all()
    )
