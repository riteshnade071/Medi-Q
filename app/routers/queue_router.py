from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db
from ..services import queue as queue_service
from ..services.queue import QueueError

router = APIRouter(prefix="/queue", tags=["queue"])


def _get_owned_doctor(db: Session, doctor_id: str, clinic_id: str) -> models.Doctor:
    doctor = db.query(models.Doctor).filter(
        models.Doctor.id == doctor_id, models.Doctor.clinic_id == clinic_id
    ).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return doctor


@router.post("/walkin", response_model=schemas.TokenOut)
def add_walkin(
    payload: schemas.WalkinCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_active_user),
):
    doctor = _get_owned_doctor(db, payload.doctor_id, user.clinic_id)
    try:
        token = queue_service.create_booking(
            db, doctor, payload.patient_name, payload.patient_mobile, models.TokenSource.WALKIN
        )
    except QueueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return token


@router.get("/{doctor_id}", response_model=schemas.DoctorQueueOut)
def get_doctor_queue(
    doctor_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_active_user),
):
    doctor = _get_owned_doctor(db, doctor_id, user.clinic_id)
    today = date.today()
    serving = queue_service.current_serving(db, doctor.id, today)
    waiting = queue_service.waiting_tokens(db, doctor.id, today)
    done_count = (
        db.query(models.Token)
        .filter(models.Token.doctor_id == doctor.id, models.Token.queue_date == today, models.Token.status == models.TokenStatus.DONE)
        .count()
    )
    skipped_count = (
        db.query(models.Token)
        .filter(models.Token.doctor_id == doctor.id, models.Token.queue_date == today, models.Token.status == models.TokenStatus.SKIPPED)
        .count()
    )
    no_show_count = (
        db.query(models.Token)
        .filter(models.Token.doctor_id == doctor.id, models.Token.queue_date == today, models.Token.status == models.TokenStatus.NO_SHOW)
        .count()
    )
    return schemas.DoctorQueueOut(
        doctor=doctor, current_serving=serving, waiting=waiting,
        done_count=done_count, skipped_count=skipped_count, no_show_count=no_show_count,
    )


@router.post("/{doctor_id}/next", response_model=schemas.TokenOut | None)
def call_next_patient(
    doctor_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_active_user),
):
    doctor = _get_owned_doctor(db, doctor_id, user.clinic_id)
    return queue_service.call_next(db, doctor, user_id=user.id)


@router.post("/{doctor_id}/skip", response_model=schemas.TokenOut | None)
def skip_current_patient(
    doctor_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_active_user),
):
    doctor = _get_owned_doctor(db, doctor_id, user.clinic_id)
    return queue_service.skip_current(db, doctor, user_id=user.id)


@router.post("/{doctor_id}/complete", response_model=schemas.TokenOut | None)
def complete_current_patient(
    doctor_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_active_user),
):
    doctor = _get_owned_doctor(db, doctor_id, user.clinic_id)
    try:
        return queue_service.complete_current(db, doctor, user_id=user.id)
    except QueueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{doctor_id}/recall", response_model=schemas.TokenOut | None)
def recall_current_patient(
    doctor_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_active_user),
):
    doctor = _get_owned_doctor(db, doctor_id, user.clinic_id)
    try:
        return queue_service.recall_current(db, doctor, user_id=user.id)
    except QueueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/token/{token_id}/no-show")
def mark_token_no_show(
    token_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_active_user),
):
    token = db.query(models.Token).filter(
        models.Token.id == token_id, models.Token.clinic_id == user.clinic_id
    ).first()
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    if token.status != models.TokenStatus.WAITING:
        raise HTTPException(status_code=400, detail="Only a waiting token can be marked no-show")
    queue_service.mark_no_show(db, token, user_id=user.id)
    return {"status": "ok"}
