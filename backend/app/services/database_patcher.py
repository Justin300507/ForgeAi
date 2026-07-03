"""
Injects a known-good app/database.py into generated projects.

Fixes:
  - postgres:// → postgresql:// (Render/Heroku legacy URL scheme)
  - check_same_thread=False for SQLite (prevents threading errors)
  - pool_pre_ping=True for PostgreSQL (prevents dead connection errors)
  - Always exports get_db, Base, SessionLocal, engine (deterministic API)
"""
import re
from pathlib import Path

_DATABASE_PY = '''\
import importlib
import os
import threading
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


# Lazy, one-time schema creation on the SAME engine the request handlers use.
# This is the safety net that makes deployment work regardless of what main.py
# does. Generated main.py files frequently create their own throwaway engine
# (e.g. sqlite:///./sql_app.db) and run Base.metadata.create_all against THAT,
# so on a real deployment (where DATABASE_URL points at Postgres) the tables get
# created on the wrong database and every handler query hits an empty one —
# "relation does not exist" / "no such table" 500s on the very first request.
# Locally this is masked by an out-of-band pre-flight create_tables() that does
# not run on Render/Heroku. Creating the tables here, bound to `engine`
# (== SessionLocal's bind), guarantees the schema exists on the database the
# handlers actually query. create_all is idempotent (checkfirst=True), so this
# is a no-op when the tables already exist.
_tables_ready = False
_tables_lock = threading.Lock()


def _ensure_tables_once():
    global _tables_ready
    if _tables_ready:
        return
    with _tables_lock:
        if _tables_ready:
            return
        try:
            create_tables()
        except Exception:
            pass
        _tables_ready = True


def get_db():
    _ensure_tables_once()
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
    _patch_main_py_duplicate_engine(db_file.parent.parent)
    return True


# Matches a call's argument list including ONE level of nested parens, e.g.
# create_engine(settings.DATABASE_URL, connect_args={"check_same_thread": False})
_CALL_BODY = r"\((?:[^()]|\([^()]*\))*\)"


def _patch_main_py_duplicate_engine(app_dir: Path) -> None:
    """
    Strip module-level engine/SessionLocal/Base duplicates from main.py and
    re-point them at app/database.py.

    Generated main.py files often do their own
    `engine = create_engine(settings.DATABASE_URL)` alongside the known-good
    app/database.py. That second engine (a) crashes startup when Config lacks
    DATABASE_URL, and (b) has none of the SQLite WAL/busy-timeout pragmas —
    one 500 mid-transaction on it held the write lock forever and every
    subsequent request timed out, wedging the whole app.
    """
    main_file = app_dir / "main.py"
    if not main_file.exists():
        return

    text = main_file.read_text(encoding="utf-8", errors="replace")
    original = text

    replacements = [
        (re.compile(rf"^engine\s*=\s*create_engine\s*{_CALL_BODY}", re.MULTILINE),
         "from app.database import engine  # db_patcher: use the single shared engine"),
        (re.compile(rf"^SessionLocal\s*=\s*sessionmaker\s*{_CALL_BODY}", re.MULTILINE),
         "from app.database import SessionLocal  # db_patcher: use the shared sessionmaker"),
        (re.compile(rf"^Base\s*=\s*declarative_base\s*{_CALL_BODY}", re.MULTILINE),
         "from app.database import Base  # db_patcher: single metadata registry"),
    ]
    stripped = []
    for pattern, replacement in replacements:
        text, n = pattern.subn(replacement, text)
        if n:
            stripped.append(replacement.split("import ")[1].split(" ")[0])

    if text != original:
        main_file.write_text(text, encoding="utf-8")
        print(f"  [db_patcher] Stripped duplicate {', '.join(stripped)} from main.py "
              f"(now imported from app.database)")


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
        print(f"  [db_patcher] Patched main.py: create_all -> create_tables()")


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

    # Boolean-state flags. The LLM freely mixes the bare adjective (completed,
    # done, active) with the is_-prefixed column (is_complete, is_active), across
    # model / schema / route / stats files. When the model column and the code
    # disagree, .filter(Model.completed == True) raises AttributeError -> 500
    # (seen live: /stats/summary on a todo app). Map both directions so the
    # attribute-access fixer can rename to whichever name is the real column.
    "completed":    ["is_complete", "is_completed", "completed", "is_done", "done"],
    "complete":     ["is_complete", "is_completed", "completed"],
    "is_complete":  ["is_complete", "completed", "is_completed", "is_done"],
    "is_completed": ["is_completed", "is_complete", "completed"],
    "done":         ["is_done", "done", "is_complete", "is_completed", "completed"],
    "is_done":      ["is_done", "done", "is_complete", "completed"],
    "active":       ["is_active", "active"],
    "is_active":    ["is_active", "active"],
    "archived":     ["is_archived", "archived"],
    "is_archived":  ["is_archived", "archived"],
    "published":    ["is_published", "published"],
    "is_published": ["is_published", "published"],

    # Date / time fields
    "due_date":    ["due_date", "deadline", "due_at", "expires_at"],
    "deadline":    ["deadline", "due_date", "due_at", "expires_at"],

    # Ownership / membership
    "creator_id":  ["owner_id", "user_id", "creator_id", "created_by"],
    "author_id":   ["owner_id", "user_id", "author_id", "created_by"],
    "created_by":  ["owner_id", "user_id", "created_by", "creator_id"],
}


# SQLAlchemy / ORM class-level attributes that are NOT columns — the
# attribute-access fixer must never rewrite these even though they aren't in the
# model's column set (they're methods/descriptors on the mapped class).
_ORM_CLASS_ATTRS = {
    "query", "metadata", "c", "columns", "count", "filter", "filter_by",
    "all", "first", "one", "one_or_none", "get", "order_by", "group_by",
    "join", "outerjoin", "options", "delete", "update", "insert", "select",
    "where", "having", "limit", "offset", "distinct", "subquery", "alias",
    "registry", "mro", "id",
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

                # Fix constructor calls: ClassName(field=...) with an unknown kwarg.
                # This only touches the ClassName(...) call, so it can't rename an
                # attribute on a Pydantic schema object (e.g. list_in.name).
                src = re.sub(
                    rf"\b{cls_name}\(([^)]+)\)",
                    _fix_constructor,
                    src,
                    flags=re.DOTALL,
                )

                # Fix CLASS-level attribute accesses: ClassName.field used in
                # queries — .filter(Todo.completed == True), .order_by(Todo.done),
                # func.count(Habit.completed), etc. If `field` isn't a real column
                # but has a synonym that is, rename it. This is class-level only
                # (literal model name . attr), so an instance access like
                # `todo_in.completed` on a Pydantic schema is never touched — the
                # regex anchors on the model class name, not an arbitrary variable.
                def _fix_class_attr(m: re.Match) -> str:
                    attr = m.group(1)
                    if attr in valid_cols or attr in _ORM_CLASS_ATTRS or attr.startswith("__"):
                        return m.group(0)
                    candidates = _FIELD_SYNONYMS.get(attr, [])
                    replacement = next(
                        (c for c in candidates if c in valid_cols and c != attr),
                        None,
                    )
                    if replacement:
                        return f"{cls_name}.{replacement}"
                    return m.group(0)

                src = re.sub(
                    rf"(?<![\w.]){cls_name}\.(\w+)",
                    _fix_class_attr,
                    src,
                )

            if src != original:
                rf.write_text(src, encoding="utf-8")
                patched += 1
                print(f"  [field_patcher] Aligned model fields in {rf.name}")

        except Exception as exc:
            print(f"  [field_patcher] Skipped {rf.name}: {exc}")

    return patched


# Column-name pattern → (SQLAlchemy type, default clause, extra sqlalchemy
# import needed for the default). Checked in order (first match wins).
# nullable=False + a default is used (not nullable=True) for two reasons:
# it satisfies schema_model_validator's separate "required field on a
# nullable column" check as well as this file's own "field doesn't exist"
# check, and it matches this project's own documented convention for
# exactly this pattern (see shared_contract.py: "RIGHT: created_at =
# Column(DateTime, server_default=func.now(), nullable=False)").
_COLUMN_TYPE_RULES: list[tuple] = [
    (re.compile(r"^(is_|has_)"), "Boolean", "default=False", None),
    # Common boolean field names with no is_/has_ prefix. Without this, a
    # field like "completed" fell through to the String default below,
    # producing Column(String, server_default='') -- every response then
    # tried to serialize '' as the schema's `completed: bool`, raising
    # FastAPI's ResponseValidationError ("bool_parsing", input='') on every
    # single row. Seen live on a todo app: POST /todos 500'd on every call.
    (re.compile(r"^(completed|done|active|enabled|verified|published|"
                r"archived|cancell?ed|finished|resolved|closed|confirmed|"
                r"approved|deleted|visible|featured|locked|paid|seen|"
                r"read|starred|pinned|favou?rite[d]?|blocked|banned)$"),
     "Boolean", "default=False", None),
    (re.compile(r"(_at|_date|_on|deadline|expires)$"), "DateTime", "server_default=func.now()", "func"),
    (re.compile(r"_id$"), "Integer", "default=0", None),
    (re.compile(r"(count|qty|quantity|amount|_num|number|order|rank|priority)$"), "Integer", "default=0", None),
]


def _infer_column_spec(field_name: str) -> tuple:
    """Returns (sql_type, default_clause, extra_import_or_None)."""
    for pattern, sql_type, default_clause, extra_import in _COLUMN_TYPE_RULES:
        if pattern.search(field_name):
            return sql_type, default_clause, extra_import
    return "String", "server_default=''", None


def patch_add_missing_model_columns(project_path: str) -> int:
    """
    Route constructors sometimes pass a field the model genuinely has no
    column for (not a misnaming — patch_model_field_mismatches already
    handles renames via _FIELD_SYNONYMS). Left alone, this crashes every
    request that hits the constructor with a TypeError, and depending on the
    runtime-fix LLM call to add the column reliably is a single point of
    failure. This deterministically adds a nullable column instead.

    Must run AFTER patch_model_field_mismatches — any field still not on the
    model at that point has no synonym and is a genuine gap, not a rename.
    """
    project = Path(project_path)
    models_dir = project / "app" / "models"
    routes_dir = project / "app" / "routes"

    if not models_dir.exists() or not routes_dir.exists():
        return 0

    class_re = re.compile(r"^class\s+(\w+)\s*\(", re.MULTILINE)
    col_re = re.compile(r"^\s{4}(\w+)\s*=\s*Column\(", re.MULTILINE)

    # ClassName -> (file path, frozenset(existing columns))
    model_info: dict[str, tuple] = {}
    for mf in models_dir.glob("*.py"):
        if mf.name == "__init__.py":
            continue
        try:
            src = mf.read_text(encoding="utf-8", errors="replace")
            for cls_name in class_re.findall(src):
                if cls_name in ("Base", "Config", "Meta"):
                    continue
                cols = set(col_re.findall(src)) | {"id"}
                model_info[cls_name] = (mf, frozenset(cols))
        except Exception:
            pass

    if not model_info:
        return 0

    # ClassName -> {missing field names}
    missing_by_class: dict[str, set] = {}
    for rf in routes_dir.glob("*.py"):
        try:
            src = rf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        for cls_name, (_mf, valid_cols) in model_info.items():
            if cls_name not in src:
                continue

            for m in re.finditer(rf"\b{cls_name}\(([^)]+)\)", src, flags=re.DOTALL):
                for part in re.split(r",\s*\n?", m.group(1)):
                    kv = part.strip()
                    if "=" not in kv:
                        continue
                    field = kv.split("=", 1)[0].strip()
                    if not field or field.startswith("#") or not field.isidentifier():
                        continue
                    if field in valid_cols:
                        continue
                    missing_by_class.setdefault(cls_name, set()).add(field)

    # A field required by a response_model schema but entirely absent from
    # the model is a guaranteed ResponseValidationError at request time (not
    # a constructor TypeError — FastAPI can't serialize a field that doesn't
    # exist on the ORM instance). Scoped to response_model schemas only:
    # Create/Update input schemas legitimately have fields with no column
    # (e.g. `password` on a UserCreate).
    schemas_dir = project / "app" / "schemas"
    if schemas_dir.exists():
        import ast as _ast
        from app.services.schema_model_validator import (
            _is_optional_annotation, _collect_response_model_schemas,
        )

        response_model_schemas = _collect_response_model_schemas(str(project))

        def _is_list_annotation(annotation) -> bool:
            # List[...]-shaped fields (e.g. completions: List[HabitCompletionRead])
            # represent a relationship, not a scalar column — a synthesized
            # scalar default (e.g. "") fails Pydantic's list validation just
            # as badly as the missing field did. Leave these for the LLM fix
            # loop, which can add a real relationship() or hybrid property.
            base = annotation.value if isinstance(annotation, _ast.Subscript) else None
            if isinstance(base, _ast.Name) and base.id in ("List", "list"):
                return True
            if isinstance(base, _ast.Attribute) and base.attr in ("List", "list"):
                return True
            return False

        for sf in schemas_dir.glob("*.py"):
            if sf.name == "__init__.py":
                continue
            try:
                src = sf.read_text(encoding="utf-8", errors="replace")
                tree = _ast.parse(src)
            except Exception:
                continue

            for node in tree.body:
                if not isinstance(node, _ast.ClassDef):
                    continue
                if node.name not in response_model_schemas:
                    continue

                # Match this schema to a model by the same prefix convention
                # used in schema_model_validator.py (TaskRead -> Task).
                cls_name = next(
                    (m for m in model_info if node.name.lower().startswith(m.lower())),
                    None,
                )
                if not cls_name:
                    continue
                _mf, valid_cols = model_info[cls_name]

                for child in node.body:
                    if not isinstance(child, _ast.AnnAssign):
                        continue
                    if not isinstance(child.target, _ast.Name):
                        continue
                    field = child.target.id
                    if field in valid_cols:
                        continue
                    if _is_optional_annotation(child.annotation):
                        continue
                    if _is_list_annotation(child.annotation):
                        continue
                    missing_by_class.setdefault(cls_name, set()).add(field)

    patched_files = set()

    for cls_name, fields in missing_by_class.items():
        mf, valid_cols = model_info[cls_name]
        try:
            src = mf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        original = src

        # Bound this class's body: from "class ClassName(" to the next
        # top-level "class " (or EOF).
        start_m = re.search(rf"^class\s+{cls_name}\s*\(", src, re.MULTILINE)
        if not start_m:
            continue
        next_class_m = re.search(r"^class\s+\w+\s*\(", src[start_m.end():], re.MULTILINE)
        body_end = start_m.end() + next_class_m.start() if next_class_m else len(src)
        class_body = src[start_m.end():body_end]

        # Insert after the last existing "    field = Column(...)" line in
        # this class; fall back to right after the class header.
        col_matches = list(re.finditer(r"^\s{4}\w+\s*=\s*Column\([^\n]*\)\n", class_body, re.MULTILINE))
        insert_at = (
            start_m.end() + col_matches[-1].end() if col_matches
            else src.index("\n", start_m.end()) + 1
        )

        needed_types = set()
        new_lines = []
        for field in sorted(fields):
            sql_type, default_clause, extra_import = _infer_column_spec(field)
            needed_types.add(sql_type)
            if extra_import:
                needed_types.add(extra_import)
            new_lines.append(f"    {field} = Column({sql_type}, {default_clause}, nullable=False)\n")

        src = src[:insert_at] + "".join(new_lines) + src[insert_at:]

        # Ensure the inferred types are imported from sqlalchemy.
        import_m = re.search(r"^from sqlalchemy import ([^\n]+)$", src, re.MULTILINE)
        if import_m:
            existing = {n.strip() for n in import_m.group(1).split(",")}
            missing_types = needed_types - existing
            if missing_types:
                new_import_list = import_m.group(1).rstrip() + ", " + ", ".join(sorted(missing_types))
                src = src[:import_m.start(1)] + new_import_list + src[import_m.end(1):]
        else:
            src = f"from sqlalchemy import Column, {', '.join(sorted(needed_types))}\n" + src

        if src != original:
            mf.write_text(src, encoding="utf-8")
            patched_files.add(mf.name)
            print(f"  [field_patcher] Added missing column(s) {sorted(fields)} to {cls_name} in {mf.name}")

    return len(patched_files)


# Field-name pattern -> Pydantic annotation (all Optional so a missing input
# field never becomes a NEW 422 for the client). Same intent as
# _COLUMN_TYPE_RULES above but scoped to the schema side of this bug class.
_SCHEMA_FIELD_TYPE_RULES: list[tuple] = [
    (re.compile(r"^(is_|has_)"), "Optional[bool] = None"),
    (re.compile(r"^(completed|done|active|enabled|verified|published|"
                r"archived|cancell?ed|finished|resolved|closed|confirmed|"
                r"approved|deleted|visible|featured|locked|paid|seen|"
                r"read|starred|pinned|favou?rite[d]?|blocked|banned)$"),
     "Optional[bool] = None"),
    (re.compile(r"_id$"), "Optional[int] = None"),
    (re.compile(r"(count|qty|quantity|amount|_num|number|order|rank|priority|value)$"),
     "Optional[int] = None"),
]

# Pydantic BaseModel attributes/methods a naive `var.attr` scan must not
# mistake for a missing schema field.
_PYDANTIC_BUILTIN_ATTRS = {
    "dict", "json", "copy", "model_dump", "model_dump_json", "model_copy",
    "model_fields", "model_config", "model_construct", "model_validate",
    "model_validate_json", "schema", "schema_json", "parse_obj", "parse_raw",
    "construct", "validate", "Config", "fields", "__fields__", "__dict__",
}


def _infer_schema_field_spec(field_name: str) -> str:
    for pattern, annotation in _SCHEMA_FIELD_TYPE_RULES:
        if pattern.search(field_name):
            return annotation
    return "Optional[str] = None"


def patch_add_missing_schema_fields(project_path: str) -> int:
    """
    Route handlers routinely read `habit_in.target_unit` off a Create/Update
    Pydantic schema whose class never declared that field -- a distinct bug
    from patch_add_missing_model_columns above (that one fixes the SQLAlchemy
    model constructor call; this fixes the schema attribute the route reads
    to build that call in the first place). Left alone this crashes every
    request to the endpoint with AttributeError, and the resulting 500 has no
    file_path to ground a fix on (see engine.py's JourneyCRUDFailure
    handling) -- the run stays broken through the whole retry budget.

    Deterministically adds the missing field as Optional[...] = None so it
    never introduces a NEW required-field 422 for existing clients.
    """
    project = Path(project_path)
    schemas_dir = project / "app" / "schemas"
    routes_dir = project / "app" / "routes"
    if not schemas_dir.exists() or not routes_dir.exists():
        return 0

    import ast

    # class_name -> (file, {own field names}, {base class names})
    schema_info: dict[str, tuple] = {}
    for sf in schemas_dir.glob("*.py"):
        if sf.name == "__init__.py":
            continue
        try:
            tree = ast.parse(sf.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            fields = {
                child.target.id for child in node.body
                if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
            }
            bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
            schema_info[node.name] = (sf, fields, bases)

    if not schema_info:
        return 0

    def _all_fields(cls_name: str, seen: set) -> set:
        if cls_name not in schema_info or cls_name in seen:
            return set()
        seen.add(cls_name)
        _mf, fields, bases = schema_info[cls_name]
        result = set(fields)
        for base in bases:
            result |= _all_fields(base, seen)
        return result

    param_re = re.compile(r"\bdef\s+\w+\([^)]*\)", re.DOTALL)
    # missing field -> class name -> set of field names
    missing_by_class: dict[str, set] = {}

    for rf in routes_dir.glob("*.py"):
        try:
            src = rf.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except Exception:
            continue

        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            # param_name -> schema class, only for params typed as a known schema
            param_schema: dict[str, str] = {}
            for arg in func.args.args:
                if arg.annotation is None:
                    continue
                ann = arg.annotation
                cls_name = ann.id if isinstance(ann, ast.Name) else None
                if cls_name in schema_info:
                    param_schema[arg.arg] = cls_name

            if not param_schema:
                continue

            for sub in ast.walk(func):
                if not isinstance(sub, ast.Attribute):
                    continue
                if not isinstance(sub.value, ast.Name):
                    continue
                param = sub.value.id
                if param not in param_schema:
                    continue
                attr = sub.attr
                if attr.startswith("_") or attr in _PYDANTIC_BUILTIN_ATTRS:
                    continue
                cls_name = param_schema[param]
                if attr in _all_fields(cls_name, set()):
                    continue
                missing_by_class.setdefault(cls_name, set()).add(attr)

    patched_files = set()
    for cls_name, fields in missing_by_class.items():
        mf, _own_fields, _bases = schema_info[cls_name]
        try:
            src = mf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        original = src

        start_m = re.search(rf"^class\s+{cls_name}\s*\(", src, re.MULTILINE)
        if not start_m:
            continue
        next_class_m = re.search(r"^class\s+\w+\s*\(", src[start_m.end():], re.MULTILINE)
        body_end = start_m.end() + next_class_m.start() if next_class_m else len(src)
        class_body = src[start_m.end():body_end]

        field_matches = list(re.finditer(r"^\s{4}\w+\s*:[^\n]*\n", class_body, re.MULTILINE))
        insert_at = (
            start_m.end() + field_matches[-1].end() if field_matches
            else src.index("\n", start_m.end()) + 1
        )

        new_lines = [
            f"    {field}: {_infer_schema_field_spec(field)}\n"
            for field in sorted(fields)
        ]
        src = src[:insert_at] + "".join(new_lines) + src[insert_at:]

        typing_m = re.search(r"^from typing import ([^\n]+)$", src, re.MULTILINE)
        if typing_m:
            existing = {n.strip() for n in typing_m.group(1).split(",")}
            if "Optional" not in existing:
                new_list = typing_m.group(1).rstrip() + ", Optional"
                src = src[:typing_m.start(1)] + new_list + src[typing_m.end(1):]
        else:
            src = "from typing import Optional\n" + src

        if src != original:
            mf.write_text(src, encoding="utf-8")
            patched_files.add(mf.name)
            print(f"  [field_patcher] Added missing schema field(s) {sorted(fields)} to {cls_name} in {mf.name}")

    return len(patched_files)
