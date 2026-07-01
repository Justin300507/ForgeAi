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
from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

# Render/Heroku provide postgres:// but SQLAlchemy 1.4+ requires postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Top-level engine assignment so validators can detect the exported symbol
_engine_kwargs = (
    {"connect_args": {"check_same_thread": False}}
    if DATABASE_URL.startswith("sqlite")
    else {"pool_pre_ping": True, "pool_recycle": 300}
)
engine = create_engine(DATABASE_URL, **_engine_kwargs)

# SQLite: enable WAL mode + 5s busy timeout to prevent write-lock hangs after any 500 error.
# WAL allows concurrent reads while a write is in progress; busy_timeout retries the lock
# instead of immediately raising "database is locked", preventing the cascade of timeouts
# that occur when a failed db.commit() leaves the connection in a dirty state.
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def _add_missing_columns():
    """ALTER TABLE ADD COLUMN for any mapped columns missing from existing tables."""
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
    # Use CWD-relative path; uvicorn and pre-flight subprocess run from backend dir
    models_dir = os.path.join("app", "models")
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


def get_db_url() -> str:
    """Return the active database URL (exported so main.py can import it)."""
    return DATABASE_URL


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


# Common field-name synonyms: wrong_name → [possible_correct_names in priority order].
# When a route passes a field not on the model, we try each candidate in order
# and use the first one that actually exists as a Column on the model.
_FIELD_SYNONYMS: dict[str, list[str]] = {
    # Primary string / label fields
    "title":       ["name", "title", "project_name", "task_name", "label_name", "list_name"],
    "name":        ["title", "name", "project_name", "task_name", "label_name", "list_name", "full_name"],
    "label":       ["name", "label", "title", "tag"],
    "tag":         ["name", "tag", "label", "title"],

    # Content / body fields
    # "description" is the most commonly misused field — LLM adds it but model may only have "name"
    "description": ["name", "title", "description", "content", "body", "text", "summary", "notes"],
    "content":     ["content", "body", "description", "text", "message", "notes"],
    "body":        ["body", "content", "description", "text", "notes"],
    "text":        ["text", "content", "body", "description", "notes"],
    "summary":     ["summary", "description", "content", "notes"],
    "message":     ["message", "content", "body", "text", "description"],
    "notes":       ["notes", "description", "content", "body", "text"],
    "details":     ["description", "details", "content", "body", "notes"],

    # User identity fields
    "username":    ["email", "username", "user_handle", "name", "full_name"],
    "user_handle": ["username", "email", "name", "user_handle"],
    "display_name":["full_name", "display_name", "name", "username"],
    "full_name":   ["full_name", "display_name", "name", "username"],

    # Status / type / priority enums
    "status":      ["status", "state", "is_done", "is_complete", "is_completed"],
    "state":       ["state", "status", "is_done"],
    "priority":    ["priority", "importance", "urgency"],

    # Date / time fields
    "due_date":    ["due_date", "deadline", "due_at", "expires_at"],
    "deadline":    ["deadline", "due_date", "due_at", "expires_at"],

    # Ownership / membership
    "creator_id":  ["owner_id", "user_id", "creator_id", "created_by"],
    "author_id":   ["owner_id", "user_id", "author_id", "created_by"],
    "created_by":  ["owner_id", "user_id", "created_by", "creator_id"],
}


def patch_model_field_mismatches(project_path: str) -> int:
    """
    Scan route files for SQLAlchemy constructor calls that use field names not
    present on the model, and rename them to the closest valid column name.
    Returns the number of files patched.

    Algorithm:
    1. Parse app/models/*.py — extract {ClassName: {col_name, ...}} from Column() lines
    2. Parse app/routes/*.py — find ModelClass(field=val, ...) constructor calls
    3. For each field that doesn't exist on the model, try synonym mapping
    4. Also fix obj.attr accesses where attr isn't a model column
    """
    import re

    project = Path(project_path)
    models_dir = project / "app" / "models"
    routes_dir = project / "app" / "routes"

    if not models_dir.exists() or not routes_dir.exists():
        return 0

    # ── Step 1: build {ClassName → frozenset(column_names)} ──────────────────
    model_columns: dict[str, frozenset] = {}
    class_re = re.compile(r"^class\s+(\w+)\s*\(", re.MULTILINE)
    col_re = re.compile(r"^\s{4}(\w+)\s*=\s*Column\(", re.MULTILINE)

    for mf in models_dir.glob("*.py"):
        if mf.name == "__init__.py":
            continue
        try:
            src = mf.read_text(encoding="utf-8", errors="replace")
            for cls_name in class_re.findall(src):
                if cls_name in ("Base", "Config", "Meta"):
                    continue
                cols = set(col_re.findall(src)) | {"id"}
                model_columns[cls_name] = frozenset(cols)
        except Exception:
            pass

    if not model_columns:
        return 0

    # ── Step 2: scan route files ──────────────────────────────────────────────
    # Regex to find: SomeModel(\n    field=...,\n    field2=...\n)
    # We match the opening `ClassName(` then capture the arg block up to matching `)`
    patched = 0

    for rf in routes_dir.glob("*.py"):
        try:
            src = rf.read_text(encoding="utf-8", errors="replace")
            original = src

            for cls_name, valid_cols in model_columns.items():
                # Only process routes that actually reference this class
                if cls_name not in src:
                    continue

                def _fix_constructor(m: re.Match) -> str:
                    args_block = m.group(1)
                    parts = re.split(r",\s*\n?", args_block)

                    # Collect fields already present and valid (to detect duplicates)
                    existing_valid = set()
                    for part in parts:
                        kv = part.strip()
                        if "=" in kv:
                            f = kv.split("=", 1)[0].strip()
                            if f in valid_cols:
                                existing_valid.add(f)

                    fixed_lines = []
                    changed = False
                    renamed_to: set = set()  # track replacements we've already applied

                    for part in parts:
                        kv = part.strip()
                        if "=" not in kv:
                            fixed_lines.append(part)
                            continue
                        field, rest = kv.split("=", 1)
                        field = field.strip()
                        if not field or field.startswith("#") or field in valid_cols:
                            fixed_lines.append(part)
                            continue
                        # Field is invalid — find best synonym
                        candidates = _FIELD_SYNONYMS.get(field, [])
                        replacement = next(
                            (c for c in candidates if c in valid_cols and c != field),
                            None,
                        )
                        if replacement:
                            # Drop this kwarg if its replacement already exists (prevents duplicates)
                            if replacement in existing_valid or replacement in renamed_to:
                                changed = True  # kwarg dropped
                                continue
                            fixed_lines.append(
                                part.replace(f"{field}=", f"{replacement}=", 1)
                            )
                            renamed_to.add(replacement)
                            changed = True
                        else:
                            # Can't map it — leave as-is (LLM will handle it)
                            fixed_lines.append(part)
                    if changed:
                        return f"{cls_name}(" + ", ".join(fixed_lines) + ")"
                    return m.group(0)

                # Fix constructor calls only — never touch attribute accesses on other objects
                # (e.g. list_in.name must not be renamed even if `name` is not on the model,
                # because list_in is a Pydantic schema, not the SQLAlchemy model)
                src = re.sub(
                    rf"\b{cls_name}\(([^)]+)\)",
                    _fix_constructor,
                    src,
                    flags=re.DOTALL,
                )

            if src != original:
                rf.write_text(src, encoding="utf-8")
                patched += 1
                print(f"  [field_patcher] Aligned model fields in {rf.name}")

        except Exception as exc:
            print(f"  [field_patcher] Skipped {rf.name}: {exc}")

    return patched
