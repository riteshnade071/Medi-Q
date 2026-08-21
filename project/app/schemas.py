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
