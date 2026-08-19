from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db
from ..services import analytics

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=schemas.DashboardSummary)
def get_dashboard_summary(db: Session = Depends(get_db), user: models.User = Depends(auth.require_feature("analytics"))):
    return analytics.dashboard_summary(db, user.clinic_id)
