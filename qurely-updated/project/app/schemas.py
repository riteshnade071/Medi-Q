from datetime import date, datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr


# ---------- Auth ----------
class ClinicSignup(BaseModel):
    clinic_name: str
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserProfileOut(BaseModel):
    email: EmailStr
    whatsapp_number: Optional[str] = None
    clinic_name: str
    clinic_slug: str

    class Config:
        from_attributes = True


class UserProfileUpdate(BaseModel):
    whatsapp_number: Optional[str] = None


# ---------- Doctors ----------
class DoctorCreate(BaseModel):
    name: str
    specialization: Optional[str] = None
    avg_consult_minutes: int = 6


class DoctorUpdate(BaseModel):
    name: Optional[str] = None
    specialization: Optional[str] = None
    avg_consult_minutes: Optional[int] = None
    is_active: Optional[bool] = None


class DoctorOut(BaseModel):
    id: str
    name: str
    specialization: Optional[str] = None
    avg_consult_minutes: int
    is_active: bool

    class Config:
        from_attributes = True


class PublicDoctorOut(BaseModel):
    id: str
    name: str
    specialization: Optional[str] = None
    clinic_name: str


# ---------- Queue (staff side) ----------
class WalkinCreate(BaseModel):
    doctor_id: str
    patient_name: str
    patient_mobile: Optional[str] = None


class TokenOut(BaseModel):
    id: str
    token_number: int
    patient_name: str
    patient_mobile: Optional[str] = None
    status: str
    source: str
    created_at: datetime
    called_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DoctorQueueOut(BaseModel):
    doctor: DoctorOut
    current_serving: Optional[TokenOut] = None
    waiting: List[TokenOut] = []
    done_count: int = 0
    skipped_count: int = 0
    no_show_count: int = 0


# ---------- Public booking ----------
class BookingCreate(BaseModel):
    doctor_id: str
    patient_name: str
    patient_mobile: Optional[str] = None


class BookingOut(BaseModel):
    public_code: str
    token_number: int
    doctor_name: str
    clinic_name: str
    current_serving: Optional[int] = None
    ahead_count: int
    estimated_wait_minutes: int


class PublicStatusOut(BaseModel):
    token_number: int
    doctor_name: str
    clinic_name: str
    status: str
    current_serving: Optional[int] = None
    ahead_count: int
    estimated_wait_minutes: int


class PublicQueueBoardOut(BaseModel):
    doctor_name: str
    clinic_name: str
    current_serving: Optional[int] = None
    waiting_count: int
    updated_at: datetime


# ---------- Patients ----------
class PatientOut(BaseModel):
    id: str
    patient_code: str
    name: str
    mobile: Optional[str] = None
    email: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class PatientVisitOut(BaseModel):
    id: str
    doctor_name: str
    token_number: int
    source: str
    status: str
    queue_date: date
    created_at: datetime
    called_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class PatientDetailOut(BaseModel):
    patient: PatientOut
    total_bookings: int
    total_visits: int
    online_bookings: int
    walkins: int
    cancellations: int
    no_shows: int
    first_visit: Optional[datetime] = None
    last_visit: Optional[datetime] = None
    history: List[PatientVisitOut] = []


# ---------- Support ----------
class SupportTicketCreate(BaseModel):
    category: str
    subject: str
    description: str
    contact_email: Optional[EmailStr] = None


class SupportTicketOut(BaseModel):
    id: str
    ticket_number: str
    category: str
    subject: str
    description: str
    status: str
    priority: str
    email_notified: bool
    created_at: datetime
    updated_at: datetime
    resolved_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class FAQItem(BaseModel):
    question: str
    answer: str


# ---------- WhatsApp log ----------
class WhatsAppMessageOut(BaseModel):
    id: str
    message_type: str
    to_number: Optional[str] = None
    status: str
    failure_reason: Optional[str] = None
    created_at: datetime
    sent_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ---------- Settings ----------
class ClinicSettingsOut(BaseModel):
    token_prefix: str
    max_daily_tokens: int
    approaching_threshold: int
    online_booking_enabled: bool
    walkin_enabled: bool
    whatsapp_enabled: bool
    support_email: Optional[str] = None
    whatsapp_provider_configured: bool = False

    class Config:
        from_attributes = True


class ClinicSettingsUpdate(BaseModel):
    token_prefix: Optional[str] = None
    max_daily_tokens: Optional[int] = None
    approaching_threshold: Optional[int] = None
    online_booking_enabled: Optional[bool] = None
    walkin_enabled: Optional[bool] = None
    whatsapp_enabled: Optional[bool] = None
    support_email: Optional[str] = None


# ---------- Dashboard ----------
class DoctorDaySummary(BaseModel):
    doctor_id: str
    doctor_name: str
    current_serving: Optional[int] = None
    waiting_count: int
    done_count: int
    avg_wait_minutes: float


class DashboardSummary(BaseModel):
    date: date
    total_tokens_today: int
    waiting_count: int
    done_count: int
    no_show_count: int
    per_doctor: List[DoctorDaySummary]
