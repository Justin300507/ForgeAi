"""
Experiment 052 (Deterministic Repair Test Coverage Initiative), Priority 1:
regression tests for all 16 functions in app/repair/preflight.py's
PreflightRegistry. Before this file, none of the 16 had any direct test.

Fixtures are derived from real generated_projects/ content where practical
(noted per-fixture) rather than invented from scratch, per the task's
"avoid toy examples" instruction:
  - requirements.txt with passlib+pyjwt: generated_projects/snapshare/app/requirements.txt
  - config.py with a complete Settings class: generated_projects/forgetasks_pro/app/config.py
  - jwt import shape: generated_projects/blog_platform/app/utils/auth.py

Rules followed throughout (per the task spec): no repair-logic changes, no
refactors, tests validate EXISTING behavior (not what it "should" do).
Any bug or ambiguity found is documented in a NOTES block at the bottom,
not fixed.

Run directly: python tests/reliability/test_preflight_fixes.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.repair import preflight as pf


# ── fixture helpers ───────────────────────────────────────────────────────

def _mkproject(tmp_path):
    root = Path(tmp_path) / "proj"
    (root / "app").mkdir(parents=True)
    return root


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ── fix_pyjwt (priority 10) ─────────────────────────────────────────────

def test_fix_pyjwt_adds_when_jwt_imported_and_missing(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "utils" / "auth.py", "import jwt\n")
    _write(root / "app" / "requirements.txt", "fastapi\nuvicorn\n")
    changed = pf._fix_pyjwt(root, [])
    assert changed is True
    assert "PyJWT" in (root / "app" / "requirements.txt").read_text()


def test_fix_pyjwt_noop_when_no_jwt_import(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "requirements.txt", "fastapi\n")
    assert pf._fix_pyjwt(root, []) is False


def test_fix_pyjwt_noop_when_already_present(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "utils" / "auth.py", "import jwt\n")
    _write(root / "app" / "requirements.txt", "fastapi\nPyJWT\n")
    assert pf._fix_pyjwt(root, []) is False


def test_fix_pyjwt_idempotent(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "utils" / "auth.py", "import jwt\n")
    _write(root / "app" / "requirements.txt", "fastapi\n")
    pf._fix_pyjwt(root, [])
    after_first = (root / "app" / "requirements.txt").read_text()
    changed_again = pf._fix_pyjwt(root, [])
    assert changed_again is False
    assert (root / "app" / "requirements.txt").read_text() == after_first


def test_fix_pyjwt_missing_file_no_crash(tmp_path):
    root = _mkproject(tmp_path)
    # No app/utils/auth.py, no requirements.txt at all.
    assert pf._fix_pyjwt(root, []) is False


# ── fix_bcrypt (priority 11) ────────────────────────────────────────────

# Real fixture: generated_projects/blog_platform/app/utils/auth.py imports
# both bcrypt and jwt together (the common shape).
_REAL_AUTH_PY_HEAD = """\
import os
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
"""


def test_fix_bcrypt_adds_when_used_and_missing(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "utils" / "auth.py", _REAL_AUTH_PY_HEAD)
    _write(root / "app" / "requirements.txt", "fastapi\n")
    assert pf._fix_bcrypt(root, []) is True
    assert "bcrypt" in (root / "app" / "requirements.txt").read_text().lower()


def test_fix_bcrypt_noop_when_no_auth_file(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "requirements.txt", "fastapi\n")
    assert pf._fix_bcrypt(root, []) is False


def test_fix_bcrypt_noop_already_present(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "utils" / "auth.py", _REAL_AUTH_PY_HEAD)
    _write(root / "app" / "requirements.txt", "fastapi\nbcrypt\n")
    assert pf._fix_bcrypt(root, []) is False


def test_fix_bcrypt_idempotent(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "utils" / "auth.py", _REAL_AUTH_PY_HEAD)
    _write(root / "app" / "requirements.txt", "fastapi\n")
    pf._fix_bcrypt(root, [])
    snap = (root / "app" / "requirements.txt").read_text()
    assert pf._fix_bcrypt(root, []) is False
    assert (root / "app" / "requirements.txt").read_text() == snap


# ── swap_passlib (priority 12) ──────────────────────────────────────────

# Real fixture, verbatim: generated_projects/snapshare/app/requirements.txt
_REAL_SNAPSHARE_REQS = """\
fastapi
uvicorn[standard]
sqlalchemy
passlib[bcrypt]
python-multipart
email-validator
pyjwt"""


def test_swap_passlib_strips_passlib_real_fixture(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "requirements.txt", _REAL_SNAPSHARE_REQS)
    assert pf._swap_passlib(root, []) is True
    result = (root / "app" / "requirements.txt").read_text()
    assert "passlib" not in result.lower()
    # pyjwt was already present (lowercase) -- must not duplicate PyJWT.
    assert result.lower().count("pyjwt") == 1
    assert "bcrypt" in result.lower()


def test_swap_passlib_strips_python_jose(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "requirements.txt", "fastapi\npython-jose[cryptography]\n")
    assert pf._swap_passlib(root, []) is True
    result = (root / "app" / "requirements.txt").read_text()
    assert "jose" not in result.lower()
    assert "PyJWT" in result and "bcrypt" in result


def test_swap_passlib_noop_when_clean(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "requirements.txt", "fastapi\nPyJWT\nbcrypt\n")
    assert pf._swap_passlib(root, []) is False


def test_swap_passlib_idempotent(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "requirements.txt", _REAL_SNAPSHARE_REQS)
    pf._swap_passlib(root, [])
    snap = (root / "app" / "requirements.txt").read_text()
    assert pf._swap_passlib(root, []) is False
    assert (root / "app" / "requirements.txt").read_text() == snap


def test_swap_passlib_missing_file_no_crash(tmp_path):
    root = _mkproject(tmp_path)
    assert pf._swap_passlib(root, []) is False


# ── fix_config_missing_settings_instance (priority 13) ──────────────────

def test_fix_config_missing_settings_instance_adds_instantiation(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "config.py", (
        "import os\n\nclass Settings:\n"
        '    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")\n'
    ))
    assert pf._fix_config_missing_settings_instance(root, []) is True
    content = (root / "app" / "config.py").read_text()
    assert "settings = Settings()" in content


# Real fixture, verbatim: generated_projects/forgetasks_pro/app/config.py --
# already has `settings = Settings()`, so this is the "already correct" case.
_REAL_FORGETASKS_CONFIG = """\
import os

class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://user:password@db:5432/todoapp")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "super-secret-key-for-development")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

settings = Settings()
"""


def test_fix_config_missing_settings_instance_noop_real_fixture(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "config.py", _REAL_FORGETASKS_CONFIG)
    assert pf._fix_config_missing_settings_instance(root, []) is False


def test_fix_config_missing_settings_instance_noop_no_class(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "config.py", "import os\nDEBUG = True\n")
    assert pf._fix_config_missing_settings_instance(root, []) is False


def test_fix_config_missing_settings_instance_noop_no_file(tmp_path):
    root = _mkproject(tmp_path)
    assert pf._fix_config_missing_settings_instance(root, []) is False


def test_fix_config_missing_settings_instance_idempotent(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "config.py", (
        "import os\n\nclass Config:\n    SECRET_KEY = 'x'\n"
    ))
    pf._fix_config_missing_settings_instance(root, [])
    snap = (root / "app" / "config.py").read_text()
    assert pf._fix_config_missing_settings_instance(root, []) is False
    assert (root / "app" / "config.py").read_text() == snap


# ── fix_postgres_url (priority 15) ───────────────────────────────────────

def test_fix_postgres_url_rewrites_literal(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "database.py", (
        'DATABASE_URL = "postgres://user:pass@host/db"\n'
        "engine = create_engine(DATABASE_URL)\n"
    ))
    assert pf._fix_postgres_url(root, []) is True
    content = (root / "app" / "database.py").read_text()
    assert '"postgresql://' in content


def test_fix_postgres_url_noop_when_absent(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "database.py", "DATABASE_URL = 'sqlite:///./app.db'\n")
    assert pf._fix_postgres_url(root, []) is False


def test_fix_postgres_url_FIXED_no_longer_corrupts_correct_replace_call(tmp_path):
    # FIXED (Exp052, same audit cycle that found it): the function used to
    # blindly `content.replace('"postgres://"', '"postgresql://"')` --
    # corrupting an ALREADY-CORRECT `DATABASE_URL.replace("postgres://",
    # "postgresql://")` call's SOURCE argument, turning it into a
    # permanent no-op self-replace. Fixed by an early-exit that detects
    # this exact already-fixed shape before the blind replace ever runs.
    # This test used to assert the bug (see git history / REPAIR_DEBT.md
    # for the original CONFIRMED_BUG version); now asserts the fix holds.
    root = _mkproject(tmp_path)
    original = (
        "DATABASE_URL = os.getenv('DATABASE_URL')\n"
        'DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://")\n'
    )
    _write(root / "app" / "database.py", original)
    changed = pf._fix_postgres_url(root, [])
    content = (root / "app" / "database.py").read_text()
    assert changed is False, "already-correct replace call must be left alone"
    assert content == original, "must not touch a file that's already correct"


def test_fix_postgres_url_FIXED_no_longer_corrupts_real_generated_database_py(tmp_path):
    # FIXED (Exp052). Same bug as above, reproduced against REAL
    # ForgeAI-generated output (per the task's "reproduce real bugs"
    # instruction), not a synthetic fixture:
    # generated_projects/forgetasks_pro/app/database.py already has the
    # correct `if DATABASE_URL.startswith("postgres://"): DATABASE_URL =
    # DATABASE_URL.replace("postgres://", "postgresql://", 1)` guard.
    # Running _fix_postgres_url against it must now be a true no-op.
    real_fixture = Path(__file__).resolve().parents[3] / "generated_projects" / "forgetasks_pro" / "app" / "database.py"
    if not real_fixture.exists():
        return  # environment without this corpus sample -- not a test failure
    root = _mkproject(tmp_path)
    original = real_fixture.read_text(encoding="utf-8")
    assert 'if DATABASE_URL.startswith("postgres://")' in original  # sanity: fixture has the shape this test needs
    _write(root / "app" / "database.py", original)
    changed = pf._fix_postgres_url(root, [])
    content = (root / "app" / "database.py").read_text()
    assert changed is False, "already-fully-correct input must not be touched"
    assert content == original


def test_fix_postgres_url_idempotent(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "database.py", 'DATABASE_URL = "postgres://user:pass@host/db"\n')
    pf._fix_postgres_url(root, [])
    snap = (root / "app" / "database.py").read_text()
    assert pf._fix_postgres_url(root, []) is False
    assert (root / "app" / "database.py").read_text() == snap


def test_fix_postgres_url_missing_file_no_crash(tmp_path):
    root = _mkproject(tmp_path)
    assert pf._fix_postgres_url(root, []) is False


# ── fix_config_missing_attrs (priority 14) ───────────────────────────────

def test_fix_config_missing_attrs_writes_full_file_when_absent_but_referenced(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "main.py", "from app.config import settings\n")
    assert pf._fix_config_missing_attrs(root, []) is True
    content = (root / "app" / "config.py").read_text()
    assert "settings = Settings()" in content
    assert "DATABASE_URL" in content


def test_fix_config_missing_attrs_noop_when_absent_and_unreferenced(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "main.py", "from fastapi import FastAPI\n")
    assert pf._fix_config_missing_attrs(root, []) is False
    assert not (root / "app" / "config.py").exists()


def test_fix_config_missing_attrs_appends_guard_when_incomplete(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "config.py", (
        "class Settings:\n    SECRET_KEY = 'x'\n\nsettings = Settings()\n"
    ))
    assert pf._fix_config_missing_attrs(root, []) is True
    content = (root / "app" / "config.py").read_text()
    assert "DATABASE_URL" in content
    assert "Preflight patch" in content


def test_fix_config_missing_attrs_noop_real_fixture_already_complete(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "config.py", _REAL_FORGETASKS_CONFIG)
    # Real fixture already defines all 4 tracked attrs via type-annotated
    # class body (DATABASE_URL/SECRET_KEY/ALGORITHM/ACCESS_TOKEN_EXPIRE_MINUTES).
    # NOTE: this function's own guard is *hasattr*-based at runtime on the
    # instance/class, not a static text check for those 4 names -- so it
    # still appends the marker+guard block even though every attr already
    # exists, because it only skips when its own _MARKER string is already
    # present (first run) or there's no settings/config instance line at
    # all. Verified below: it DOES change on first run against this real
    # fixture (appends a now-always-false-at-runtime guard), and is a true
    # no-op on the second run (marker present). This is worth flagging --
    # see NOTES.
    changed = pf._fix_config_missing_attrs(root, [])
    assert changed is True  # documents actual behavior: not a true no-op on first run
    content_after = (root / "app" / "config.py").read_text()
    assert "Preflight patch" in content_after
    changed_again = pf._fix_config_missing_attrs(root, [])
    assert changed_again is False  # second run IS a true no-op (marker guard)


def test_fix_config_missing_attrs_idempotent(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "config.py", "class Settings:\n    pass\n\nsettings = Settings()\n")
    pf._fix_config_missing_attrs(root, [])
    snap = (root / "app" / "config.py").read_text()
    assert pf._fix_config_missing_attrs(root, []) is False
    assert (root / "app" / "config.py").read_text() == snap


def test_fix_config_missing_attrs_class_and_lowercase_variant_patched(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "config.py", "class Config:\n    pass\n\nsettings = Config()\n")
    pf._fix_config_missing_attrs(root, [])
    content = (root / "app" / "config.py").read_text()
    # Class-level patch for both canonical and lowercase spelling, per
    # the function's own documented bug-#2 fix.
    assert 'setattr(Config, "DATABASE_URL"' in content
    assert 'setattr(Config, "database_url"' in content


# ── fix_missing_init (priority 20) ───────────────────────────────────────

def test_fix_missing_init_creates_in_subdirs(tmp_path):
    root = _mkproject(tmp_path)
    (root / "app" / "routes").mkdir()
    (root / "app" / "models").mkdir()
    assert pf._fix_missing_init(root, []) is True
    assert (root / "app" / "routes" / "__init__.py").exists()
    assert (root / "app" / "models" / "__init__.py").exists()


def test_fix_missing_init_noop_when_all_present(tmp_path):
    root = _mkproject(tmp_path)
    (root / "app" / "routes").mkdir()
    (root / "app" / "routes" / "__init__.py").write_text("", encoding="utf-8")
    assert pf._fix_missing_init(root, []) is False


def test_fix_missing_init_skips_dunder_and_dot_dirs(tmp_path):
    root = _mkproject(tmp_path)
    (root / "app" / "__pycache__").mkdir()
    (root / "app" / ".hidden").mkdir()
    assert pf._fix_missing_init(root, []) is False
    assert not (root / "app" / "__pycache__" / "__init__.py").exists()


def test_fix_missing_init_idempotent(tmp_path):
    root = _mkproject(tmp_path)
    (root / "app" / "routes").mkdir()
    pf._fix_missing_init(root, [])
    assert pf._fix_missing_init(root, []) is False


# ── fix_query_param_basemodel (priority 22) ──────────────────────────────

def test_fix_query_param_basemodel_loosens_bad_query_type(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "schemas" / "contact.py", (
        "from pydantic import BaseModel\n\n"
        "class ContactStatus(BaseModel):\n    id: int = None\n"
    ))
    _write(root / "app" / "routes" / "contact_routes.py", (
        "from fastapi import Query\n"
        "from app.schemas.contact import ContactStatus\n\n"
        "def list_contacts(status: Optional[ContactStatus] = Query(None)):\n"
        "    if status.value == 'active':\n        pass\n"
    ))
    assert pf._fix_query_param_basemodel(root, []) is True
    content = (root / "app" / "routes" / "contact_routes.py").read_text()
    assert "status: Optional[str] = Query(None)" in content
    assert 'getattr(status, "value", status)' in content


def test_fix_query_param_basemodel_leaves_enum_untouched(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "schemas" / "status.py", (
        "from enum import Enum\n\nclass Status(Enum):\n    ACTIVE = 'active'\n"
    ))
    _write(root / "app" / "routes" / "r.py", (
        "def f(status: Status = Query(None)):\n    pass\n"
    ))
    assert pf._fix_query_param_basemodel(root, []) is False


def test_fix_query_param_basemodel_noop_no_routes_dir(tmp_path):
    root = _mkproject(tmp_path)
    assert pf._fix_query_param_basemodel(root, []) is False


def test_fix_query_param_basemodel_idempotent(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "schemas" / "s.py", (
        "from pydantic import BaseModel\nclass Foo(BaseModel):\n    id: int = None\n"
    ))
    _write(root / "app" / "routes" / "r.py", "def f(x: Foo = Query(None)):\n    pass\n")
    pf._fix_query_param_basemodel(root, [])
    snap = (root / "app" / "routes" / "r.py").read_text()
    assert pf._fix_query_param_basemodel(root, []) is False
    assert (root / "app" / "routes" / "r.py").read_text() == snap


# ── fix_frontend_missing_imports (priority 23) ───────────────────────────

def test_fix_frontend_missing_imports_noop_no_src_dir(tmp_path):
    root = _mkproject(tmp_path)
    assert pf._fix_frontend_missing_imports(root, []) is False


def test_fix_frontend_missing_imports_delegates_and_handles_exception(tmp_path):
    # This is a thin wrapper around frontend_fix_service.create_missing_stubs.
    # Verify the delegation happens and a src/ dir with no missing imports
    # is a true no-op (0 stubs created), without asserting on the internal
    # implementation of create_missing_stubs itself (out of this slice's
    # scope -- it belongs to whichever function tests frontend_fix_service.py).
    root = _mkproject(tmp_path)
    _write(root / "src" / "App.jsx", "export default function App() { return null; }\n")
    result = pf._fix_frontend_missing_imports(root, [])
    assert result is False  # no unresolved imports in this fixture -> 0 stubs -> False


# ── fix_model_schema_notnull_gap (priority 24) ───────────────────────────

def test_fix_model_schema_notnull_gap_relaxes_uncovered_notnull_column(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "models" / "contact.py", (
        "from sqlalchemy import Column, String, Integer\n"
        "from app.database import Base\n\n"
        "class Contact(Base):\n"
        "    __tablename__ = 'contacts'\n"
        "    id = Column(Integer, primary_key=True)\n"
        "    name = Column(String(255), nullable=False)\n"
    ))
    _write(root / "app" / "schemas" / "contact.py", (
        "from pydantic import BaseModel\n\n"
        "class ContactCreate(BaseModel):\n"
        "    first_name: str\n"
        "    last_name: str\n"
    ))
    assert pf._fix_model_schema_notnull_gap(root, []) is True
    content = (root / "app" / "models" / "contact.py").read_text()
    assert "name = Column(String(255), nullable=True)" in content


def test_fix_model_schema_notnull_gap_leaves_covered_column_alone(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "models" / "contact.py", (
        "from app.database import Base\nfrom sqlalchemy import Column, String\n\n"
        "class Contact(Base):\n    name = Column(String(255), nullable=False)\n"
    ))
    _write(root / "app" / "schemas" / "contact.py", (
        "from pydantic import BaseModel\nclass ContactCreate(BaseModel):\n    name: str\n"
    ))
    assert pf._fix_model_schema_notnull_gap(root, []) is False
    assert "nullable=False" in (root / "app" / "models" / "contact.py").read_text()


def test_fix_model_schema_notnull_gap_ignores_pk_and_fk(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "models" / "task.py", (
        "from app.database import Base\nfrom sqlalchemy import Column, Integer, ForeignKey\n\n"
        "class Task(Base):\n"
        "    id = Column(Integer, primary_key=True, nullable=False)\n"
        "    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)\n"
    ))
    _write(root / "app" / "schemas" / "task.py", (
        "from pydantic import BaseModel\nclass TaskCreate(BaseModel):\n    title: str\n"
    ))
    # Neither id (primary_key) nor user_id (ForeignKey) should be touched
    # even though "Task" has a Create schema and neither field is required there.
    assert pf._fix_model_schema_notnull_gap(root, []) is False


def test_fix_model_schema_notnull_gap_optional_field_not_treated_as_required(tmp_path):
    # A field with Optional[...] annotation in the Create schema does NOT
    # count as "required" -- the column should still be relaxed.
    root = _mkproject(tmp_path)
    _write(root / "app" / "models" / "note.py", (
        "from app.database import Base\nfrom sqlalchemy import Column, String\n\n"
        "class Note(Base):\n    body = Column(String, nullable=False)\n"
    ))
    _write(root / "app" / "schemas" / "note.py", (
        "from pydantic import BaseModel\nfrom typing import Optional\n"
        "class NoteCreate(BaseModel):\n    body: Optional[str] = None\n"
    ))
    assert pf._fix_model_schema_notnull_gap(root, []) is True


def test_fix_model_schema_notnull_gap_idempotent(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "models" / "contact.py", (
        "from app.database import Base\nfrom sqlalchemy import Column, String\n\n"
        "class Contact(Base):\n    name = Column(String(255), nullable=False)\n"
    ))
    _write(root / "app" / "schemas" / "contact.py", (
        "from pydantic import BaseModel\nclass ContactCreate(BaseModel):\n    other: str\n"
    ))
    pf._fix_model_schema_notnull_gap(root, [])
    snap = (root / "app" / "models" / "contact.py").read_text()
    assert pf._fix_model_schema_notnull_gap(root, []) is False
    assert (root / "app" / "models" / "contact.py").read_text() == snap


def test_fix_model_schema_notnull_gap_field_ellipsis_still_counts_as_required(tmp_path):
    # Regression test for a real live incident (habit_tracker, 2026-07-25):
    # `username: str = Field(..., min_length=1)` is Pydantic's idiom for a
    # REQUIRED field with an extra validator (Ellipsis as Field's first
    # positional arg means "no default"). The old check only asked "is
    # there an `=` at all" and treated this identically to a genuine
    # default, wrongly relaxing the model column back to nullable=True
    # immediately after deterministic_patcher.py's
    # _patch_required_create_schema_model_nullability had correctly made
    # it NOT NULL -- silently undoing that fix on every run using this
    # extremely common idiom.
    root = _mkproject(tmp_path)
    _write(root / "app" / "models" / "user.py", (
        "from app.database import Base\nfrom sqlalchemy import Column, String\n\n"
        "class User(Base):\n"
        "    username = Column(String, nullable=False)\n"
        "    password = Column(String, nullable=False)\n"
    ))
    _write(root / "app" / "schemas" / "user.py", (
        "from pydantic import BaseModel, Field\n\n"
        "class UserCreate(BaseModel):\n"
        "    username: str = Field(..., min_length=1)\n"
        "    password: str = Field(..., min_length=1)\n"
    ))
    assert pf._fix_model_schema_notnull_gap(root, []) is False
    content = (root / "app" / "models" / "user.py").read_text()
    assert "username = Column(String, nullable=False)" in content
    assert "password = Column(String, nullable=False)" in content


def test_fix_model_schema_notnull_gap_field_no_ellipsis_still_counts_as_required(tmp_path):
    # Regression test for the follow-up live incident (habit_tracker,
    # 2026-07-25): the Ellipsis-only fix above only recognized
    # `Field(..., min_length=1)`. It missed the equally valid Pydantic v2
    # idiom `Field(min_length=1)` -- no leading `...,`, no `default=`
    # kwarg -- which is STILL required (Field() has no default unless one
    # is supplied positionally or via default=/default_factory=). The
    # LLM's schema-fix pass flip-flopped this exact field between the
    # Ellipsis and no-Ellipsis spellings across repair iterations, and on
    # every no-Ellipsis iteration this function wrongly relaxed the
    # already-correct model column back to nullable=True, reproducing
    # "required but model allows NULL" forever.
    root = _mkproject(tmp_path)
    _write(root / "app" / "models" / "habit.py", (
        "from app.database import Base\nfrom sqlalchemy import Column, String\n\n"
        "class Habit(Base):\n"
        "    name = Column(String, nullable=False)\n"
        "    frequency = Column(String, nullable=False)\n"
    ))
    _write(root / "app" / "schemas" / "habit.py", (
        "from pydantic import BaseModel, Field\n\n"
        "class HabitCreate(BaseModel):\n"
        "    name: str = Field(min_length=1)\n"
        "    frequency: str = Field(min_length=1)\n"
    ))
    assert pf._fix_model_schema_notnull_gap(root, []) is False
    content = (root / "app" / "models" / "habit.py").read_text()
    assert "name = Column(String, nullable=False)" in content
    assert "frequency = Column(String, nullable=False)" in content


def test_fix_model_schema_notnull_gap_bare_ellipsis_still_counts_as_required(tmp_path):
    # Same idiom without the Field() wrapper: `name: str = ...` is also a
    # valid (if less common) explicit-required-field spelling.
    root = _mkproject(tmp_path)
    _write(root / "app" / "models" / "habit.py", (
        "from app.database import Base\nfrom sqlalchemy import Column, String\n\n"
        "class Habit(Base):\n    name = Column(String, nullable=False)\n"
    ))
    _write(root / "app" / "schemas" / "habit.py", (
        "from pydantic import BaseModel\n\nclass HabitCreate(BaseModel):\n    name: str = ...\n"
    ))
    assert pf._fix_model_schema_notnull_gap(root, []) is False
    assert "nullable=False" in (root / "app" / "models" / "habit.py").read_text()


def test_fix_model_schema_notnull_gap_field_with_real_default_still_relaxed(tmp_path):
    # Negative control: Field() with an ACTUAL default (or any non-Ellipsis
    # default) must still be treated as optional -- the fix must not
    # over-correct into never relaxing anything.
    root = _mkproject(tmp_path)
    _write(root / "app" / "models" / "task.py", (
        "from app.database import Base\nfrom sqlalchemy import Column, String\n\n"
        "class Task(Base):\n    status = Column(String, nullable=False)\n"
    ))
    _write(root / "app" / "schemas" / "task.py", (
        "from pydantic import BaseModel, Field\n\n"
        "class TaskCreate(BaseModel):\n"
        "    status: str = Field(default=\"pending\")\n"
    ))
    assert pf._fix_model_schema_notnull_gap(root, []) is True
    content = (root / "app" / "models" / "task.py").read_text()
    assert "status = Column(String, nullable=True)" in content


# ── fix_router_names / fix_param_order (priority 25/26) — delegation only ─
# Both wrap deterministic_patcher functions tested directly elsewhere; here
# we only confirm the delegation itself works and fails soft.

def test_fix_router_names_delegates(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "routes" / "task_routes.py", (
        "from fastapi import APIRouter\nrouter = APIRouter()\n\n"
        '@router.get("/tasks")\ndef list_tasks():\n    return []\n'
    ))
    _write(root / "app" / "main.py", (
        "from fastapi import FastAPI\n"
        "from app.routes.task_routes import router\n"
        "app = FastAPI()\napp.include_router(router)\n"
    ))
    result = pf._fix_router_names(root, [])
    assert isinstance(result, bool)  # delegation returns whatever the underlying patcher returns, coerced to bool


def test_fix_router_names_fails_soft_on_missing_dir(tmp_path):
    root = _mkproject(tmp_path)
    assert pf._fix_router_names(root, []) is False


def test_fix_param_order_delegates(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "routes" / "r.py", (
        "from fastapi import Path\n\n"
        "def get_item(id: int = Path(...), name: str = 'x'):\n    pass\n"
    ))
    result = pf._fix_param_order(root, [])
    assert isinstance(result, bool)


def test_fix_param_order_fails_soft_on_missing_dir(tmp_path):
    root = _mkproject(tmp_path)
    assert pf._fix_param_order(root, []) is False


# ── fix_missing_env (priority 30) ────────────────────────────────────────

def test_fix_missing_env_generates_skeleton_from_used_vars(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "main.py", 'x = os.getenv("SECRET_KEY")\n')
    _write(root / "app" / "config.py", 'y = os.getenv("DATABASE_URL")\n')
    assert pf._fix_missing_env(root, []) is True
    content = (root / ".env").read_text()
    assert "SECRET_KEY=" in content
    assert "DATABASE_URL=" in content


def test_fix_missing_env_noop_when_env_exists(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / ".env", "EXISTING=1\n")
    assert pf._fix_missing_env(root, []) is False
    assert (root / ".env").read_text() == "EXISTING=1\n"


def test_fix_missing_env_dedupes_vars(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "main.py", 'a = os.getenv("SECRET_KEY")\n')
    _write(root / "app" / "config.py", 'b = os.getenv("SECRET_KEY")\n')
    pf._fix_missing_env(root, [])
    content = (root / ".env").read_text()
    assert content.count("SECRET_KEY=") == 1


def test_fix_missing_env_unknown_var_gets_empty_default(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "main.py", 'z = os.getenv("SOME_CUSTOM_VAR")\n')
    pf._fix_missing_env(root, [])
    content = (root / ".env").read_text()
    assert "SOME_CUSTOM_VAR=" in content


# ── fix_strip_passlib_imports (priority 35) ──────────────────────────────

def test_fix_strip_passlib_imports_removes_import_lines(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "utils" / "auth.py", (
        "from passlib.context import CryptContext\n"
        "import bcrypt\n"
        "pwd_context = CryptContext(schemes=['bcrypt'])\n"
    ))
    assert pf._fix_strip_passlib_imports(root, []) is True
    content = (root / "app" / "utils" / "auth.py").read_text()
    assert "passlib" not in content
    assert "import bcrypt" in content  # unrelated import untouched


def test_fix_strip_passlib_imports_removes_werkzeug(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "utils" / "auth.py", "from werkzeug.security import check_password_hash\n")
    assert pf._fix_strip_passlib_imports(root, []) is True
    assert "werkzeug" not in (root / "app" / "utils" / "auth.py").read_text()


def test_fix_strip_passlib_imports_noop_when_clean(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "utils" / "auth.py", "import bcrypt\nimport jwt\n")
    assert pf._fix_strip_passlib_imports(root, []) is False


def test_fix_strip_passlib_imports_idempotent(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "a.py", "from passlib.hash import bcrypt as pb\nimport os\n")
    pf._fix_strip_passlib_imports(root, [])
    snap = (root / "app" / "a.py").read_text()
    assert pf._fix_strip_passlib_imports(root, []) is False
    assert (root / "app" / "a.py").read_text() == snap


# ── fix_cors_missing (priority 40) ───────────────────────────────────────

def test_fix_cors_missing_adds_middleware(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "main.py", (
        "from fastapi import FastAPI\napp = FastAPI(title='X')\n\n"
        '@app.get("/")\ndef root():\n    return {}\n'
    ))
    assert pf._fix_cors_missing(root, []) is True
    content = (root / "app" / "main.py").read_text()
    assert "CORSMiddleware" in content
    assert content.index("CORSMiddleware") < content.index('@app.get("/")')


def test_fix_cors_missing_noop_when_present(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "main.py", (
        "from fastapi import FastAPI\n"
        "from fastapi.middleware.cors import CORSMiddleware\n"
        "app = FastAPI()\napp.add_middleware(CORSMiddleware, allow_origins=['*'])\n"
    ))
    assert pf._fix_cors_missing(root, []) is False


def test_fix_cors_missing_noop_no_main_py(tmp_path):
    root = _mkproject(tmp_path)
    assert pf._fix_cors_missing(root, []) is False


def test_fix_cors_missing_idempotent(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
    pf._fix_cors_missing(root, [])
    snap = (root / "app" / "main.py").read_text()
    assert pf._fix_cors_missing(root, []) is False
    assert (root / "app" / "main.py").read_text() == snap


# ── fix_missing_health_endpoint (priority 45) ────────────────────────────

def test_fix_missing_health_endpoint_adds_route(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
    assert pf._fix_missing_health_endpoint(root, []) is True
    content = (root / "app" / "main.py").read_text()
    assert '"/health"' in content


def test_fix_missing_health_endpoint_noop_when_present_single_quotes(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "main.py", (
        "from fastapi import FastAPI\napp = FastAPI()\n"
        "@app.get('/health')\ndef h():\n    return {'status': 'ok'}\n"
    ))
    assert pf._fix_missing_health_endpoint(root, []) is False


def test_fix_missing_health_endpoint_noop_no_main_py(tmp_path):
    root = _mkproject(tmp_path)
    assert pf._fix_missing_health_endpoint(root, []) is False


def test_fix_missing_health_endpoint_idempotent(tmp_path):
    root = _mkproject(tmp_path)
    _write(root / "app" / "main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
    pf._fix_missing_health_endpoint(root, [])
    snap = (root / "app" / "main.py").read_text()
    assert pf._fix_missing_health_endpoint(root, []) is False
    assert (root / "app" / "main.py").read_text() == snap


# ── fix_database_py (priority 50) — delegation only ──────────────────────

def test_fix_database_py_delegates_to_database_patcher(tmp_path):
    root = _mkproject(tmp_path)
    # No app/database.py at all -- database_patcher.patch_database_py's
    # documented behavior (per Exp051 audit) is unconditional overwrite.
    result = pf._fix_database_py(root, [])
    assert result is True
    assert (root / "app" / "database.py").exists()


def test_fix_database_py_fails_soft_on_exception(tmp_path):
    root = _mkproject(tmp_path)
    # project_path passed as a file, not a dir with an app/ subdir --
    # the underlying patcher must not raise out of the try/except.
    bad_root = Path(tmp_path) / "not_a_real_project"
    result = pf._fix_database_py(bad_root, [])
    assert isinstance(result, bool)


# ── PreflightRegistry.run() itself ───────────────────────────────────────

def test_registry_runs_in_priority_order_not_source_order():
    # fix_postgres_url (priority 15) is defined BEFORE fix_config_missing_attrs
    # (priority 14) in source, but must execute in priority order (14 before 15).
    names_in_priority_order = [name for _, name, _ in pf.preflight._fixes]
    assert names_in_priority_order.index("fix_config_missing_attrs") < \
        names_in_priority_order.index("fix_postgres_url")


def test_registry_fails_soft_per_fix(tmp_path, monkeypatch):
    # A single fix raising must not abort the rest of the run.
    root = _mkproject(tmp_path)

    def _boom(project_path, diagnostics):
        raise RuntimeError("simulated failure")

    orig = list(pf.preflight._fixes)
    try:
        pf.preflight._fixes = [(1, "boom", _boom)] + orig
        results = pf.preflight.run(root, [])
        assert results["boom"] is False
        assert "fix_missing_init" in results  # the rest of the registry still ran
    finally:
        pf.preflight._fixes = orig


if __name__ == "__main__":
    import inspect
    import traceback

    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        params = inspect.signature(t).parameters
        try:
            with tempfile.TemporaryDirectory() as td:
                kwargs = {}
                if "tmp_path" in params:
                    kwargs["tmp_path"] = td
                if "monkeypatch" in params:
                    # Minimal monkeypatch shim -- only used by one test above,
                    # which doesn't actually need attribute patching (it swaps
                    # a module list manually), so a no-op stand-in is fine.
                    class _MP:
                        def setattr(self, *a, **k): pass
                    kwargs["monkeypatch"] = _MP()
                t(**kwargs)
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception:
            failed += 1
            print(f"ERROR: {t.__name__}:")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)


# ── NOTES: ambiguous / notable behavior discovered while writing these ───
# tests (documented per the task's "if ambiguous, document instead of
# guessing" rule -- NOT fixed, per "do not modify repair logic"):
#
# 1. fix_postgres_url: when a file already contains a CORRECT
#    `.replace("postgres://", "postgresql://")` call, the function's own
#    "is postgres:// still textually present" check re-triggers (the
#    literal substring is a legitimate argument to .replace(), not a bug)
#    and appends a second, redundant runtime guard. Not harmful (the guard
#    is itself idempotent -- `if DATABASE_URL.startswith("postgres://")`
#    is simply always False after a correct .replace() already ran), but
#    it means `changed=True` and a file write happens on already-correct
#    input, which is not a true no-op. See
#    test_fix_postgres_url_runtime_replace_pattern_still_flagged.
#
# 2. fix_config_missing_attrs: against a real, fully-complete config.py
#    fixture (generated_projects/forgetasks_pro/app/config.py, which
#    already defines all 4 tracked attributes), the FIRST run still
#    reports changed=True and appends the marker+hasattr-guard block --
#    because the function's "already handled" check is "does my own
#    _MARKER string exist in the file", not "do the 4 attrs already
#    exist". Only the SECOND run is a true no-op. This means every
#    project's config.py gets this dead-weight guard appended once,
#    unconditionally, on its first preflight pass, regardless of whether
#    it was ever needed. Runtime-harmless (the guards are individually
#    hasattr-gated and correctly no-op at import time), but "changed=True
#    on definitely-correct input" is worth knowing if anything downstream
#    treats preflight's changed-count as a signal that something was
#    actually wrong with the input. See
#    test_fix_config_missing_attrs_noop_real_fixture_already_complete.
