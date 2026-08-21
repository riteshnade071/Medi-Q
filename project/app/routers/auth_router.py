import re
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "clinic"
    return slug


def _unique_slug(db: Session, base_slug: str) -> str:
    slug = base_slug
    while db.query(models.Clinic).filter(models.Clinic.slug == slug).first():
        slug = f"{base_slug}-{uuid.uuid4().hex[:4]}"
    return slug


@router.post("/signup", response_model=schemas.Token)
def signup(payload: schemas.ClinicSignup, request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    auth.enforce_rate_limit(f"signup:{client_ip}", max_requests=5, window_seconds=300)
    auth.validate_password_strength(payload.password)

    slug = _unique_slug(db, _slugify(payload.clinic_name))
    clinic = models.Clinic(name=payload.clinic_name, slug=slug)
    db.add(clinic)
    db.flush()

    existing = db.query(models.User).filter(
        models.User.clinic_id == clinic.id, models.User.email == payload.email
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered for this clinic")

    user = models.User(
        clinic_id=clinic.id,
        email=payload.email,
        hashed_password=auth.hash_password(payload.password),
        role="owner",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = auth.create_access_token({"sub": user.id, "clinic_id": clinic.id})
    return {"access_token": token}


@router.post("/login", response_model=schemas.Token)
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    # Rate-limit by IP+email together so one slow brute-force attempt can't
    # lock out a legitimate user typing their own password wrong a couple times.
    auth.enforce_rate_limit(f"login:{client_ip}:{form_data.username}", max_requests=10, window_seconds=300)

    user = db.query(models.User).filter(models.User.email == form_data.username).first()
    if not user or not auth.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    token = auth.create_access_token({"sub": user.id, "clinic_id": user.clinic_id})
    return {"access_token": token}


@router.get("/me", response_model=schemas.UserProfileOut)
def get_profile(db: Session = Depends(get_db), user: models.User = Depends(auth.get_current_active_user)):
    clinic = db.query(models.Clinic).filter(models.Clinic.id == user.clinic_id).first()
    return schemas.UserProfileOut(
        email=user.email, whatsapp_number=user.whatsapp_number,
        clinic_name=clinic.name if clinic else "", clinic_slug=clinic.slug if clinic else "",
    )


@router.patch("/me", response_model=schemas.UserProfileOut)
def update_profile(
    payload: schemas.UserProfileUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_active_user),
):
    for field, value in payload.dict(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    clinic = db.query(models.Clinic).filter(models.Clinic.id == user.clinic_id).first()
    return schemas.UserProfileOut(
        email=user.email, whatsapp_number=user.whatsapp_number,
        clinic_name=clinic.name if clinic else "", clinic_slug=clinic.slug if clinic else "",
    )
