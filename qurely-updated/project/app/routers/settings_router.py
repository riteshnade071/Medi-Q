from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db
from ..services import notifications

router = APIRouter(prefix="/settings", tags=["settings"])


def _get_or_create_settings(db: Session, clinic_id: str) -> models.ClinicSettings:
    settings = db.query(models.ClinicSettings).filter(models.ClinicSettings.clinic_id == clinic_id).first()
    if not settings:
        # Backfill for clinics created before Settings existed.
        settings = models.ClinicSettings(clinic_id=clinic_id)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


@router.get("", response_model=schemas.ClinicSettingsOut)
def get_settings(
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_active_user),
):
    settings = _get_or_create_settings(db, user.clinic_id)
    return schemas.ClinicSettingsOut(
        token_prefix=settings.token_prefix or "",
        max_daily_tokens=settings.max_daily_tokens,
        approaching_threshold=settings.approaching_threshold,
        online_booking_enabled=settings.online_booking_enabled,
        walkin_enabled=settings.walkin_enabled,
        whatsapp_enabled=settings.whatsapp_enabled,
        support_email=settings.support_email,
        whatsapp_provider_configured=notifications.is_whatsapp_configured(),
    )


@router.put("", response_model=schemas.ClinicSettingsOut)
def update_settings(
    payload: schemas.ClinicSettingsUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_active_user),
):
    if user.role != "owner":
        raise HTTPException(status_code=403, detail="Only the clinic owner can change settings")

    settings = _get_or_create_settings(db, user.clinic_id)
    for field, value in payload.dict(exclude_unset=True).items():
        setattr(settings, field, value)
    db.commit()
    db.refresh(settings)

    return schemas.ClinicSettingsOut(
        token_prefix=settings.token_prefix or "",
        max_daily_tokens=settings.max_daily_tokens,
        approaching_threshold=settings.approaching_threshold,
        online_booking_enabled=settings.online_booking_enabled,
        walkin_enabled=settings.walkin_enabled,
        whatsapp_enabled=settings.whatsapp_enabled,
        support_email=settings.support_email,
        whatsapp_provider_configured=notifications.is_whatsapp_configured(),
    )
