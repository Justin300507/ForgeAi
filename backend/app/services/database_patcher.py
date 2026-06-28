"""
Injects a known-good app/database.py into generated projects.

Fixes:
  - postgres:// → postgresql:// (Render/Heroku legacy URL scheme)
  - check_same_thread=False for SQLite (prevents threading errors)
  - pool_pre_ping=True for PostgreSQL (prevents dead connection errors)
  - Always exports get_db, Base, SessionLocal, engine (deterministic API)
"""
from pathlib import Path

_DATABASE_PY = '''\
import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

# Render/Heroku provide postgres:// but SQLAlchemy 1.4+ requires postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
'''


def patch_database_py(project_path: str) -> bool:
    """
    Overwrite app/database.py with a known-good template.
    Returns True if the file was written.
    """
    db_file = Path(project_path) / "app" / "database.py"
    if not db_file.parent.exists():
        return False

    # Check if already good (has the postgres:// fix and correct exports)
    if db_file.exists():
        current = db_file.read_text(encoding="utf-8", errors="replace")
        needs_fix = (
            "postgres://" in current and "postgresql://" not in current  # missing URL fix
            or "pool_pre_ping" not in current  # missing PostgreSQL resilience
            or "check_same_thread" not in current  # missing SQLite fix
            or "get_db" not in current  # missing get_db export
        )
        if not needs_fix:
            return False

    db_file.write_text(_DATABASE_PY, encoding="utf-8")
    print(f"  [db_patcher] Injected known-good app/database.py")
    return True
