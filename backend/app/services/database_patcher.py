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
import importlib
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


def _add_missing_columns() -> None:
    """ALTER TABLE ADD COLUMN for any mapped columns missing from existing tables.

    Fixes schema mismatches when create_all() ran before all models were imported.
    SQLite never modifies existing tables on create_all() — this patches them.
    """
    try:
        from sqlalchemy import inspect as _inspect, text as _text
        insp = _inspect(engine)
        existing_tables = set(insp.get_table_names())
        with engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if table.name not in existing_tables:
                    continue
                try:
                    existing_cols = {c["name"] for c in insp.get_columns(table.name)}
                except Exception:
                    continue
                for col in table.columns:
                    if col.name in existing_cols:
                        continue
                    try:
                        type_str = col.type.compile(dialect=engine.dialect)
                    except Exception:
                        type_str = "TEXT"
                    try:
                        conn.execute(_text(
                            f"ALTER TABLE {table.name} ADD COLUMN {col.name} {type_str}"
                        ))
                    except Exception:
                        pass
    except Exception:
        pass


def create_tables():
    """Import every model in app/models/ then create all tables."""
    models_dir = os.path.join(os.path.dirname(__file__), "models")
    if os.path.isdir(models_dir):
        for fname in sorted(os.listdir(models_dir)):
            if fname.endswith(".py") and fname not in ("__init__.py",):
                mod_name = fname[:-3]
                try:
                    importlib.import_module(f"app.models.{mod_name}")
                except Exception:
                    pass
    Base.metadata.create_all(bind=engine)
    if DATABASE_URL.startswith("sqlite"):
        _add_missing_columns()


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
'''


def patch_database_py(project_path: str) -> bool:
    """
    Overwrite app/database.py with a known-good template.
    Always injects — the LLM runtime fix loop may have overwritten the file
    between retry attempts, so we force the known-good version every time.
    Returns True if the file was written.
    """
    db_file = Path(project_path) / "app" / "database.py"
    if not db_file.parent.exists():
        return False

    db_file.write_text(_DATABASE_PY, encoding="utf-8")
    print(f"  [db_patcher] Injected known-good app/database.py")

    # Patch main.py to call create_tables() instead of Base.metadata.create_all()
    _patch_main_py_create_all(db_file.parent.parent)
    return True


def _patch_main_py_create_all(app_dir: Path) -> None:
    """Replace Base.metadata.create_all(...) in main.py with create_tables()."""
    import re
    main_file = app_dir / "main.py"
    if not main_file.exists():
        return

    text = main_file.read_text(encoding="utf-8", errors="replace")
    original = text

    # Add create_tables to the existing database import line
    def _add_create_tables_to_import(m: re.Match) -> str:
        line = m.group(0)
        if "create_tables" in line:
            return line
        # append create_tables to the import list
        return line.rstrip() + ", create_tables\n"

    text = re.sub(
        r"^from app\.database import .+\n",
        _add_create_tables_to_import,
        text,
        flags=re.MULTILINE,
    )

    # If no app.database import found, insert one before Base.metadata.create_all
    if "create_tables" not in text:
        text = re.sub(
            r"(Base\.metadata\.create_all)",
            "from app.database import create_tables  # noqa\n\\1",
            text,
            count=1,
        )

    # Replace Base.metadata.create_all(...) with create_tables()
    text = re.sub(
        r"Base\.metadata\.create_all\([^)]*\)",
        "create_tables()",
        text,
    )

    if text != original:
        main_file.write_text(text, encoding="utf-8")
        print(f"  [db_patcher] Patched main.py: create_all → create_tables()")
