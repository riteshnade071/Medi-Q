import uuid
import enum
from datetime import datetime, date, timedelta
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Date,
    ForeignKey, Enum, UniqueConstraint
)
from sqlalchemy.orm import relationship
from .database import Base


def gen_uuid():
    return str(uuid.uuid4())


def gen_code():
    return uuid.uuid4().hex[:10]


def gen_patient_code():
    return "PAT-" + uuid.uuid4().hex[:8].upper()


def gen_ticket_number():
    return "TKT-" + uuid.uuid4().hex[:8].upper()


class Clinic(Base):
    __tablename__ = "clinics"
    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    slug = Column(String, nullable=False, unique=True, index=True)  # used in public booking links
    plan = Column(String, default="trial")  # trial, basic, pro, premium
    # Raw/last-synced status. Do not read this directly to decide feature access —
    # it is a cache written by subscription_service.sync_subscription_state();
    # the source of truth is subscription_service.get_subscription_state(clinic),
    # which recomputes live from the fields below on every check.
    # Values: trialing, active, past_due, expired, cancelled, suspended
    subscription_status = Column(String, default="trialing")
    trial_ends_at = Column(DateTime, default=lambda: datetime.utcnow() + timedelta(days=14))
    created_at = Column(DateTime, default=datetime.utcnow)

    # ---- Billing / Razorpay (added for subscription system) ----
    razorpay_customer_id = Column(String, nullable=True, index=True)
    razorpay_subscription_id = Column(String, nullable=True, index=True)
    subscription_started_at = Column(DateTime, nullable=True)
    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    payment_status = Column(String, nullable=True)  # paid, failed, pending (last known payment attempt outcome)
    last_payment_id = Column(String, nullable=True)
    cancel_at_period_end = Column(Boolean, default=False)
    grace_period_ends_at = Column(DateTime, nullable=True)  # set when a renewal charge fails (past_due)

    users = relationship("User", back_populates="clinic", cascade="all, delete-orphan")
    doctors = relationship("Doctor", back_populates="clinic", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="clinic", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=gen_uuid)
    clinic_id = Column(String, ForeignKey("clinics.id"), nullable=False, index=True)
    email = Column(String, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="owner")  # owner, receptionist
    whatsapp_number = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    clinic = relationship("Clinic", back_populates="users")

    __table_args__ = (UniqueConstraint("clinic_id", "email", name="uq_clinic_email"),)


class Doctor(Base):
    __tablename__ = "doctors"
    id = Column(String, primary_key=True, default=gen_uuid)
    clinic_id = Column(String, ForeignKey("clinics.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    specialization = Column(String, nullable=True)
    avg_consult_minutes = Column(Integer, default=6)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    clinic = relationship("Clinic", back_populates="doctors")
    tokens = relationship("Token", back_populates="doctor", cascade="all, delete-orphan")


class Patient(Base):
    """Clinic-level patient registry record. One row per (clinic, mobile) —
    online bookings and walk-ins both resolve to the same Patient when the
    mobile number matches, so returning patients don't get duplicated (spec:
    Part 2 — Patient Registry / Returning Patient Detection). Never merged on
    name alone.
    Visit/booking stats are intentionally NOT stored as counters here — they're
    always derived live from the Token table (see services/patients.py) so they
    can never drift out of sync with the actual queue history."""
    __tablename__ = "patients"
    id = Column(String, primary_key=True, default=gen_uuid)
    clinic_id = Column(String, ForeignKey("clinics.id"), nullable=False, index=True)
    patient_code = Column(String, nullable=False, unique=True, default=gen_patient_code, index=True)
    name = Column(String, nullable=False)
    mobile = Column(String, nullable=True, index=True)
    email = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    tokens = relationship("Token", back_populates="patient")

    __table_args__ = (UniqueConstraint("clinic_id", "mobile", name="uq_clinic_patient_mobile"),)


class TokenStatus(str, enum.Enum):
    WAITING = "WAITING"
    SERVING = "SERVING"
    DONE = "DONE"
    SKIPPED = "SKIPPED"
    NO_SHOW = "NO_SHOW"
    CANCELLED = "CANCELLED"


class TokenSource(str, enum.Enum):
    ONLINE = "ONLINE"
    WALKIN = "WALKIN"


class Token(Base):
    __tablename__ = "tokens"
    id = Column(String, primary_key=True, default=gen_uuid)
    clinic_id = Column(String, ForeignKey("clinics.id"), nullable=False, index=True)
    doctor_id = Column(String, ForeignKey("doctors.id"), nullable=False, index=True)
    patient_id = Column(String, ForeignKey("patients.id"), nullable=True, index=True)  # nullable: rows created before Patient Registry existed
    public_code = Column(String, nullable=False, unique=True, default=gen_code, index=True)
    queue_date = Column(Date, default=date.today, index=True)
    token_number = Column(Integer, nullable=False)
    patient_name = Column(String, nullable=False)
    patient_mobile = Column(String, nullable=True)
    status = Column(Enum(TokenStatus), default=TokenStatus.WAITING, index=True)
    source = Column(Enum(TokenSource), default=TokenSource.ONLINE)
    notified_approaching = Column(Boolean, default=False)
    notified_now = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    checked_in_at = Column(DateTime, nullable=True)
    called_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    doctor = relationship("Doctor", back_populates="tokens")
    patient = relationship("Patient", back_populates="tokens")

    __table_args__ = (UniqueConstraint("doctor_id", "queue_date", "token_number", name="uq_doctor_date_token"),)


class SupportTicketStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class SupportTicketPriority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class SupportTicket(Base):
    """Clinic Help & Support ticket (spec: Part 4). Always persisted to the DB
    regardless of whether the notification email to SUPPORT_EMAIL actually goes
    out — a missing/broken SMTP config must never mean a lost ticket."""
    __tablename__ = "support_tickets"
    id = Column(String, primary_key=True, default=gen_uuid)
    clinic_id = Column(String, ForeignKey("clinics.id"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    ticket_number = Column(String, nullable=False, unique=True, default=gen_ticket_number, index=True)
    contact_email = Column(String, nullable=True)
    category = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    description = Column(String, nullable=False)
    status = Column(Enum(SupportTicketStatus), default=SupportTicketStatus.OPEN, index=True)
    priority = Column(Enum(SupportTicketPriority), default=SupportTicketPriority.MEDIUM)
    email_notified = Column(Boolean, default=False)  # whether the SUPPORT_EMAIL alert actually sent
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)


class WhatsAppMessageType(str, enum.Enum):
    BOOKING_CONFIRMATION = "BOOKING_CONFIRMATION"
    APPROACHING = "APPROACHING"
    YOUR_TURN = "YOUR_TURN"
    RECALL = "RECALL"
    CANCELLATION = "CANCELLATION"


class WhatsAppMessageStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class WhatsAppMessage(Base):
    """Persistent WhatsApp delivery log (spec: Part 5 — WhatsApp Message Log).
    One row is written per attempted send, success or failure — status must
    reflect what the provider actually reported, never a hardcoded 'SENT'."""
    __tablename__ = "whatsapp_messages"
    id = Column(String, primary_key=True, default=gen_uuid)
    clinic_id = Column(String, ForeignKey("clinics.id"), nullable=False, index=True)
    patient_id = Column(String, ForeignKey("patients.id"), nullable=True, index=True)
    token_id = Column(String, ForeignKey("tokens.id"), nullable=True, index=True)
    message_type = Column(Enum(WhatsAppMessageType), nullable=False)
    to_number = Column(String, nullable=True)
    body = Column(String, nullable=True)
    status = Column(Enum(WhatsAppMessageStatus), default=WhatsAppMessageStatus.QUEUED, index=True)
    provider_message_id = Column(String, nullable=True)
    failure_reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)


class ClinicSettings(Base):
    """Dynamic per-clinic configuration (spec: Part 7). One row per clinic,
    created with defaults at signup. Editing these values must never require a
    redeploy."""
    __tablename__ = "clinic_settings"
    id = Column(String, primary_key=True, default=gen_uuid)
    clinic_id = Column(String, ForeignKey("clinics.id"), nullable=False, unique=True, index=True)
    token_prefix = Column(String, default="")
    max_daily_tokens = Column(Integer, default=200)
    approaching_threshold = Column(Integer, default=3)
    online_booking_enabled = Column(Boolean, default=True)
    walkin_enabled = Column(Boolean, default=True)
    whatsapp_enabled = Column(Boolean, default=True)  # clinic-level toggle; still requires provider creds to actually send
    support_email = Column(String, nullable=True)  # falls back to SUPPORT_EMAIL env var when unset
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FAQItem(Base):
    __tablename__ = "faq_items"
    id = Column(String, primary_key=True, default=gen_uuid)
    clinic_id = Column(String, ForeignKey("clinics.id"), nullable=True, index=True)  # null = global default FAQ
    question = Column(String, nullable=False)
    answer = Column(String, nullable=False)
    sort_order = Column(Integer, default=0)


class Payment(Base):
    """One row per payment/subscription event verified from Razorpay (webhook or
    server-side verification) — never trust-written from a frontend callback.
    Amount is stored in paise (Razorpay's native unit, integer, no float
    rounding issues); divide by 100 for display."""
    __tablename__ = "payments"
    id = Column(String, primary_key=True, default=gen_uuid)
    clinic_id = Column(String, ForeignKey("clinics.id"), nullable=False, index=True)
    razorpay_payment_id = Column(String, nullable=True, index=True)
    razorpay_order_id = Column(String, nullable=True)
    razorpay_subscription_id = Column(String, nullable=True, index=True)
    amount = Column(Integer, nullable=True)  # paise
    currency = Column(String, default="INR")
    status = Column(String, nullable=False)  # captured, failed, refunded, etc.
    plan = Column(String, nullable=True)
    event_type = Column(String, nullable=True)  # razorpay event name, e.g. subscription.charged
    razorpay_event_id = Column(String, nullable=True, unique=True, index=True)  # x-razorpay-event-id — dedupes webhook retries
    paid_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    clinic = relationship("Clinic", back_populates="payments")


class AuditLog(Base):
    """Append-only record of sensitive/administrative actions. Not exposed to
    patients; used for staff/admin accountability (spec: audit logs)."""
    __tablename__ = "audit_logs"
    id = Column(String, primary_key=True, default=gen_uuid)
    clinic_id = Column(String, ForeignKey("clinics.id"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)  # null for public/patient-triggered actions
    action = Column(String, nullable=False, index=True)  # e.g. "queue.next", "doctor.create", "auth.login"
    detail = Column(String, nullable=True)  # short human-readable context, no sensitive payloads
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
