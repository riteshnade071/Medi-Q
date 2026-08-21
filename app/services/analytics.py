from datetime import date
from sqlalchemy.orm import Session

from .. import models
from . import queue as queue_service


def dashboard_summary(db: Session, clinic_id: str) -> dict:
    today = date.today()
    doctors = (
        db.query(models.Doctor)
        .filter(models.Doctor.clinic_id == clinic_id, models.Doctor.is_active == True)  # noqa: E712
        .all()
    )

    per_doctor = []
    total_tokens = 0
    total_waiting = 0
    total_done = 0
    total_no_show = 0
    total_collected = 0.0

    for doc in doctors:
        tokens_today = (
            db.query(models.Token)
            .filter(models.Token.doctor_id == doc.id, models.Token.queue_date == today)
            .all()
        )
        waiting = [t for t in tokens_today if t.status == models.TokenStatus.WAITING]
        done = [t for t in tokens_today if t.status == models.TokenStatus.DONE]
        no_show = [t for t in tokens_today if t.status == models.TokenStatus.NO_SHOW]
        serving = queue_service.current_serving(db, doc.id, today)

        total_tokens += len(tokens_today)
        total_waiting += len(waiting)
        total_done += len(done)
        total_no_show += len(no_show)
        total_collected += sum(t.amount_paid or 0 for t in tokens_today)

        per_doctor.append({
            "doctor_id": doc.id,
            "doctor_name": doc.name,
            "current_serving": serving.token_number if serving else None,
            "waiting_count": len(waiting),
            "done_count": len(done),
            "avg_wait_minutes": float(doc.avg_consult_minutes or 6),
        })

    return {
        "date": today,
        "total_tokens_today": total_tokens,
        "waiting_count": total_waiting,
        "done_count": total_done,
        "no_show_count": total_no_show,
        "total_collected_today": total_collected,
        "per_doctor": per_doctor,
    }
