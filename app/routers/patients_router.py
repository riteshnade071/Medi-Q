from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db
from ..services import patients as patients_service

router = APIRouter(prefix="/patients", tags=["patients"])


@router.get("", response_model=list[schemas.PatientOut])
def list_patients(
    q: Optional[str] = Query(None, description="Search by name or mobile"),
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_feature("patient_registry")),
):
    return patients_service.search_patients(db, user.clinic_id, q)


@router.get("/{patient_id}", response_model=schemas.PatientDetailOut)
def get_patient(
    patient_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_feature("patient_registry")),
):
    patient = db.query(models.Patient).filter(
        models.Patient.id == patient_id, models.Patient.clinic_id == user.clinic_id
    ).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    stats = patients_service.patient_stats(db, patient)
    tokens = (
        db.query(models.Token)
        .filter(models.Token.patient_id == patient.id)
        .order_by(models.Token.created_at.desc())
        .all()
    )
    history = []
    for t in tokens:
        doctor = db.query(models.Doctor).filter(models.Doctor.id == t.doctor_id).first()
        history.append(schemas.PatientVisitOut(
            id=t.id,
            doctor_name=doctor.name if doctor else "",
            token_number=t.token_number,
            source=t.source.value if hasattr(t.source, "value") else t.source,
            status=t.status.value if hasattr(t.status, "value") else t.status,
            queue_date=t.queue_date,
            created_at=t.created_at,
            called_at=t.called_at,
            completed_at=t.completed_at,
            amount_paid=t.amount_paid,
        ))

    return schemas.PatientDetailOut(
        patient=patient,
        history=history,
        **stats,
    )
