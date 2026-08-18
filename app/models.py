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


class Clinic(Base):
    __tablename__ = "clinics"
    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    slug = Column(String, nullable=False, unique=True, index=True)  # used in public booking links
    plan = Column(String, default="trial")  # trial, pro
    subscription_status = Column(String, default="active")  # active, expired, cancelled
    trial_ends_at = Column(DateTime, default=lambda: datetime.utcnow() + timedelta(days=14))
    created_at = Column(DateTime, default=datetime.utcnow)

    users = relationship("User", back_populates="clinic", cascade="all, delete-orphan")
    doctors = relationship("Doctor", back_populates="clinic", cascade="all, delete-orphan")


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
    called_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    doctor = relationship("Doctor", back_populates="tokens")

    __table_args__ = (UniqueConstraint("doctor_id", "queue_date", "token_number", name="uq_doctor_date_token"),)


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
