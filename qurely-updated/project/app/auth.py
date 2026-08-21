import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from .database import get_db, IS_SQLITE
from . import models

_DEV_DEFAULT_SECRET = "dev-secret-change-me"
SECRET_KEY = os.getenv("JWT_SECRET", _DEV_DEFAULT_SECRET)

# Refuse to start against a real (non-SQLite) database with the hardcoded dev
# secret — that combination almost always means someone forgot to set
# JWT_SECRET on a real deployment, which would let anyone forge staff tokens.
if not IS_SQLITE and SECRET_KEY == _DEV_DEFAULT_SECRET:
    raise RuntimeError(
        "JWT_SECRET is not set. Refusing to start with the default development "
        "secret against a non-SQLite database. Set a long random JWT_SECRET "
        "environment variable."
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise credentials_exception
    return user


PASSWORD_MIN_LENGTH = 8


def validate_password_strength(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {PASSWORD_MIN_LENGTH} characters",
        )


# ---- Minimal in-memory rate limiting -----------------------------------
# Best-effort brute-force / spam protection for unauthenticated endpoints
# (login, signup, public booking). In-memory, so it resets on restart and
# does NOT share state across multiple server instances/workers — fine for
# a single free-tier Render instance, but a real multi-instance deployment
# should move this to Redis or a similar shared store.
import time
from collections import defaultdict

_rate_buckets: dict[str, list[float]] = defaultdict(list)


def enforce_rate_limit(key: str, max_requests: int, window_seconds: int) -> None:
    now = time.time()
    bucket = _rate_buckets[key]
    cutoff = now - window_seconds
    while bucket and bucket[0] < cutoff:
        bucket.pop(0)
    if len(bucket) >= max_requests:
        raise HTTPException(status_code=429, detail="Too many requests. Please try again shortly.")
    bucket.append(now)


def get_current_active_user(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)) -> models.User:
    """Blocks clinics with an expired/cancelled subscription from mutating data,
    while keeping account/billing endpoints reachable so a locked-out clinic can
    still see why and how to fix it."""
    clinic = db.query(models.Clinic).filter(models.Clinic.id == user.clinic_id).first()
    if clinic is None:
        raise HTTPException(status_code=401, detail="Clinic not found")

    if clinic.subscription_status == "cancelled":
        raise HTTPException(status_code=402, detail="Subscription cancelled. Contact support to reactivate.")

    if clinic.plan == "trial" and clinic.trial_ends_at and datetime.utcnow() > clinic.trial_ends_at:
        if clinic.subscription_status != "expired":
            clinic.subscription_status = "expired"
            db.commit()
        raise HTTPException(status_code=402, detail="Trial expired. Upgrade to keep using the queue system.")

    return user
