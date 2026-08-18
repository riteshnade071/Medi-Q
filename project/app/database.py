import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./dev.db")

# Render (and Heroku-style) Postgres connection strings use the legacy
# "postgres://" scheme. SQLAlchemy 1.4+ / psycopg2 requires "postgresql://" —
# without this normalization the app fails to boot against Render's own
# managed database.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

IS_SQLITE = DATABASE_URL.startswith("sqlite")
connect_args = {"check_same_thread": False} if IS_SQLITE else {}
engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    # Render's free Postgres can silently drop idle connections; pre_ping
    # avoids serving requests against a dead connection.
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
