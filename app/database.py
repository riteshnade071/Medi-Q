import os
from sqlalchemy import create_engine, inspect, text
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


# ---- Lightweight startup "migration" for columns added to tables that ----
# ---- already exist on a live deployment. --------------------------------
#
# `Base.metadata.create_all()` (called once at boot, in main.py) only creates
# tables that don't exist yet — it never alters a table that's already there.
# That's exactly the situation on the existing Render deployment: `tokens`
# was created long before the Patient Registry existed, so its live schema
# is missing the new `patient_id` column. Without this step, every booking
# on the real deployed DB would start failing the moment this code ships,
# even though it passes every test against a fresh SQLite file (which gets
# the column for free since the table is brand new there).
#
# This intentionally stays a short, explicit list rather than a full
# migration framework (Alembic) — it's enough to safely evolve this schema
# incrementally without ever dropping/recreating a table that holds real
# clinic data.
_REQUIRED_COLUMNS = {
    "tokens": [
        ("patient_id", "VARCHAR"),
        ("checked_in_at", "TIMESTAMP"),
    ],
    # Subscription/billing fields added to the existing `clinics` table for the
    # trial + Razorpay subscription system. `payments` is a brand-new table so
    # create_all() alone handles it — no migration entry needed for it here.
    "clinics": [
        ("razorpay_customer_id", "VARCHAR"),
        ("razorpay_subscription_id", "VARCHAR"),
        ("subscription_started_at", "TIMESTAMP"),
        ("current_period_start", "TIMESTAMP"),
        ("current_period_end", "TIMESTAMP"),
        ("payment_status", "VARCHAR"),
        ("last_payment_id", "VARCHAR"),
        ("cancel_at_period_end", "BOOLEAN DEFAULT FALSE"),
        ("grace_period_ends_at", "TIMESTAMP"),
    ],
}


def run_startup_migrations(engine) -> list[str]:
    """Adds any missing columns from _REQUIRED_COLUMNS to tables that already
    exist. Safe to run on every boot: it only acts on columns that are
    actually absent, and never touches existing data. Returns a list of
    human-readable descriptions of what it changed (for startup logging)."""
    applied = []
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table_name, columns in _REQUIRED_COLUMNS.items():
            if table_name not in existing_tables:
                # Table doesn't exist yet at all — create_all() will make it
                # (with the column already included), nothing to migrate.
                continue
            existing_columns = {c["name"] for c in inspector.get_columns(table_name)}
            for col_name, col_type in columns:
                if col_name in existing_columns:
                    continue
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"))
                applied.append(f"{table_name}.{col_name} ({col_type})")

    return applied
