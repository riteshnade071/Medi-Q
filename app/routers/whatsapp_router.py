from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db
from ..services import notifications

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


@router.get("/status")
def whatsapp_status(user: models.User = Depends(auth.get_current_active_user)):
    return {"configured": notifications.is_whatsapp_configured()}


@router.get("/messages", response_model=list[schemas.WhatsAppMessageOut])
def list_messages(
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_feature("notifications")),
):
    return (
        db.query(models.WhatsAppMessage)
        .filter(models.WhatsAppMessage.clinic_id == user.clinic_id)
        .order_by(models.WhatsAppMessage.created_at.desc())
        .limit(200)
        .all()
    )
