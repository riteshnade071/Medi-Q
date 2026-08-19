from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, auth
from ..database import get_db

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/status")
def billing_status(db: Session = Depends(get_db), user: models.User = Depends(auth.get_current_user)):
    clinic = db.query(models.Clinic).filter(models.Clinic.id == user.clinic_id).first()
    days_left = None
    if clinic.plan == "trial" and clinic.trial_ends_at:
        days_left = max(0, (clinic.trial_ends_at - datetime.utcnow()).days)
    return {
        "clinic_name": clinic.name,
        "clinic_slug": clinic.slug,
        "plan": clinic.plan,
        "subscription_status": clinic.subscription_status,
        "trial_ends_at": clinic.trial_ends_at,
        "trial_days_left": days_left,
    }
