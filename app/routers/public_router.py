from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db
from ..services import queue as queue_service
from ..services.queue import QueueError

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/clinic/{slug}/doctors", response_model=list[schemas.PublicDoctorOut])
def list_clinic_doctors(slug: str, db: Session = Depends(get_db)):
    clinic = db.query(models.Clinic).filter(models.Clinic.slug == slug).first()
    if not clinic:
        raise HTTPException(status_code=404, detail="Clinic not found")
    doctors = (
        db.query(models.Doctor)
        .filter(models.Doctor.clinic_id == clinic.id, models.Doctor.is_active == True)  # noqa: E712
        .all()
    )
    return [
        schemas.PublicDoctorOut(id=d.id, name=d.name, specialization=d.specialization, clinic_name=clinic.name)
        for d in doctors
    ]


@router.post("/book", response_model=schemas.BookingOut)
def book_token(payload: schemas.BookingCreate, request: Request, db: Session = Depends(get_db)):
    # Best-effort per-IP rate limit: max 8 bookings/minute from one address,
    # to blunt accidental double-submits and casual abuse of a no-auth endpoint.
    client_ip = request.client.host if request.client else "unknown"
    auth.enforce_rate_limit(f"book:{client_ip}", max_requests=8, window_seconds=60)

    doctor = db.query(models.Doctor).filter(
        models.Doctor.id == payload.doctor_id, models.Doctor.is_active == True  # noqa: E712
    ).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    if not payload.patient_name or not payload.patient_name.strip():
        raise HTTPException(status_code=400, detail="Patient name is required")

    try:
        token = queue_service.create_booking(
            db, doctor, payload.patient_name.strip(), payload.patient_mobile, models.TokenSource.ONLINE
        )
    except QueueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    clinic = db.query(models.Clinic).filter(models.Clinic.id == doctor.clinic_id).first()
    serving = queue_service.current_serving(db, doctor.id, token.queue_date)
    ahead = queue_service.ahead_count(db, doctor.id, token.token_number, token.queue_date)

    return schemas.BookingOut(
        public_code=token.public_code,
        token_number=token.token_number,
        doctor_name=doctor.name,
        clinic_name=clinic.name if clinic else "",
        current_serving=serving.token_number if serving else None,
        ahead_count=ahead,
        estimated_wait_minutes=queue_service.estimate_wait_minutes(doctor, ahead),
    )


@router.get("/status/{public_code}", response_model=schemas.PublicStatusOut)
def get_booking_status(public_code: str, db: Session = Depends(get_db)):
    token = db.query(models.Token).filter(models.Token.public_code == public_code).first()
    if not token:
        raise HTTPException(status_code=404, detail="Booking not found")
    doctor = db.query(models.Doctor).filter(models.Doctor.id == token.doctor_id).first()
    clinic = db.query(models.Clinic).filter(models.Clinic.id == token.clinic_id).first()
    serving = queue_service.current_serving(db, token.doctor_id, token.queue_date)
    ahead = queue_service.ahead_count(db, token.doctor_id, token.token_number, token.queue_date) if token.status == models.TokenStatus.WAITING else 0

    return schemas.PublicStatusOut(
        token_number=token.token_number,
        doctor_name=doctor.name if doctor else "",
        clinic_name=clinic.name if clinic else "",
        status=token.status.value if hasattr(token.status, "value") else token.status,
        current_serving=serving.token_number if serving else None,
        ahead_count=ahead,
        estimated_wait_minutes=queue_service.estimate_wait_minutes(doctor, ahead) if doctor else 0,
    )


@router.post("/cancel/{public_code}", response_model=schemas.PublicStatusOut)
def cancel_booking(public_code: str, db: Session = Depends(get_db)):
    """Patient-initiated cancellation. Anyone who knows the public_code (a
    random unguessable token, shared only with the booking patient via their
    confirmation page/link) may cancel it — no separate auth needed for MVP,
    consistent with how /status/{public_code} already works."""
    token = db.query(models.Token).filter(models.Token.public_code == public_code).first()
    if not token:
        raise HTTPException(status_code=404, detail="Booking not found")
    try:
        queue_service.cancel_token(db, token)
    except QueueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    doctor = db.query(models.Doctor).filter(models.Doctor.id == token.doctor_id).first()
    clinic = db.query(models.Clinic).filter(models.Clinic.id == token.clinic_id).first()
    return schemas.PublicStatusOut(
        token_number=token.token_number,
        doctor_name=doctor.name if doctor else "",
        clinic_name=clinic.name if clinic else "",
        status=token.status.value if hasattr(token.status, "value") else token.status,
        current_serving=None,
        ahead_count=0,
        estimated_wait_minutes=0,
    )


@router.get("/queue/{doctor_id}", response_model=schemas.PublicQueueBoardOut)
def get_public_queue_board(doctor_id: str, db: Session = Depends(get_db)):
    doctor = db.query(models.Doctor).filter(models.Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    clinic = db.query(models.Clinic).filter(models.Clinic.id == doctor.clinic_id).first()
    today = date.today()
    serving = queue_service.current_serving(db, doctor.id, today)
    waiting = queue_service.waiting_tokens(db, doctor.id, today)

    return schemas.PublicQueueBoardOut(
        doctor_name=doctor.name,
        clinic_name=clinic.name if clinic else "",
        current_serving=serving.token_number if serving else None,
        waiting_count=len(waiting),
        updated_at=datetime.utcnow(),
    )
