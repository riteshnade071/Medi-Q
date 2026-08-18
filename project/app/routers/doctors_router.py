from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/doctors", tags=["doctors"])


@router.post("", response_model=schemas.DoctorOut)
def create_doctor(
    payload: schemas.DoctorCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_active_user),
):
    doctor = models.Doctor(clinic_id=user.clinic_id, **payload.dict())
    db.add(doctor)
    db.commit()
    db.refresh(doctor)
    return doctor


@router.get("", response_model=list[schemas.DoctorOut])
def list_doctors(
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_active_user),
):
    return (
        db.query(models.Doctor)
        .filter(models.Doctor.clinic_id == user.clinic_id, models.Doctor.is_active == True)  # noqa: E712
        .order_by(models.Doctor.created_at.asc())
        .all()
    )


@router.patch("/{doctor_id}", response_model=schemas.DoctorOut)
def update_doctor(
    doctor_id: str,
    payload: schemas.DoctorUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_active_user),
):
    doctor = db.query(models.Doctor).filter(
        models.Doctor.id == doctor_id, models.Doctor.clinic_id == user.clinic_id
    ).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    for field, value in payload.dict(exclude_unset=True).items():
        setattr(doctor, field, value)
    db.commit()
    db.refresh(doctor)
    return doctor


@router.delete("/{doctor_id}")
def deactivate_doctor(
    doctor_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_active_user),
):
    doctor = db.query(models.Doctor).filter(
        models.Doctor.id == doctor_id, models.Doctor.clinic_id == user.clinic_id
    ).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    doctor.is_active = False
    db.commit()
    return {"status": "deactivated"}
