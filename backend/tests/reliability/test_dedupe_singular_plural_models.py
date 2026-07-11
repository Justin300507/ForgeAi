"""
Verifies _dedupe_class_files' new singular/plural class-NAME-variant
handling (app/services/deterministic_patcher.py) -- the existing dedup
logic only caught files that both define the SAME class name (e.g. both
`class Expense`), and completely missed the more dangerous shape where
the class names themselves differ by singular/plural along with the
filenames (e.g. user.py's `class User` vs users.py's `class Users`).

Root cause this fixes (confirmed live, 2026-07-11, via the Reliability
Opportunity Report's corpus sweep): gym_tracker, sports_league_manager,
and support_ticket_system all ship BOTH a `user.py` (usually a thin,
patcher-generated import-fallback stub with just an `id` column) AND a
`users.py` (the real model with every real column) in app/models/. Two
separate SQLAlchemy-mapped classes for one entity is a live ambiguity
risk for any relationship() string reference and pure, confusing dead
weight otherwise. `_dedupe_class_files`'s existing `classes1 & classes2`
check is empty for {"User"} & {"Users"} (different strings), so it
silently skipped every one of these real cases.

Run directly: python tests/reliability/test_dedupe_singular_plural_models.py
"""
import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import _dedupe_class_files


def _make_dir(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="dedupetest_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


_STUB_USER = '''\
from sqlalchemy import Column, Integer
from app.database import Base

class User(Base):  # stub: patcher
    __tablename__ = 'user'
    id = Column(Integer, primary_key=True)
'''

_REAL_USERS = '''\
from sqlalchemy import Column, Integer, String, DateTime, func
from app.database import Base

class Users(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False, unique=True)
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    display_name = Column(String, server_default='', nullable=False)
'''


def test_keeps_the_class_with_more_columns_regardless_of_which_file():
    # stub is user.py, real model is users.py -- must keep users.py's Users
    root = _make_dir({"user.py": _STUB_USER, "users.py": _REAL_USERS})
    n = _dedupe_class_files(root, "model")
    shutil.rmtree_errors = []
    assert n == 1
    assert not (root / "user.py").exists()
    assert (root / "users.py").exists()
    content = (root / "users.py").read_text(encoding="utf-8")
    ast.parse(content)
    assert "class Users(Base)" in content
    assert "User = Users" in content
    shutil.rmtree(root, ignore_errors=True)


def test_keeps_the_real_model_even_when_it_lives_in_the_singular_file():
    # mirror image: the REAL model happens to be in the singular-named file
    real_in_user_py = _REAL_USERS.replace("class Users(Base):", "class User(Base):").replace(
        '__tablename__ = "users"', '__tablename__ = "users"'
    )
    stub_in_users_py = _STUB_USER.replace("class User(Base):", "class Users(Base):").replace(
        "__tablename__ = 'user'", "__tablename__ = 'users_stub'"
    )
    root = _make_dir({"user.py": real_in_user_py, "users.py": stub_in_users_py})
    n = _dedupe_class_files(root, "model")
    assert n == 1
    assert (root / "user.py").exists()
    assert not (root / "users.py").exists()
    content = (root / "user.py").read_text(encoding="utf-8")
    ast.parse(content)
    assert "class User(Base)" in content
    assert "Users = User" in content
    shutil.rmtree(root, ignore_errors=True)


def test_dropped_class_import_still_resolves_via_alias():
    """The whole point of the alias line: code doing
    `from app.models.user import User` after user.py is deleted must still
    find `User` defined in the surviving users.py module."""
    root = _make_dir({"user.py": _STUB_USER, "users.py": _REAL_USERS})
    _dedupe_class_files(root, "model")
    content = (root / "users.py").read_text(encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    ns = {}
    exec(compile(content.replace("from app.database import Base", "Base = object"), "users.py", "exec"), ns)
    assert ns["User"] is ns["Users"]


def test_exact_same_class_name_behavior_unchanged():
    """Pre-existing behavior (identical class name in both files) must not
    regress: still dedupes, still keeps the longer file."""
    short = "from app.database import Base\nclass Expense(Base):\n    pass\n"
    long = ("from sqlalchemy import Column, Integer, String\n"
            "from app.database import Base\n"
            "class Expense(Base):\n"
            "    __tablename__ = 'expenses'\n"
            "    id = Column(Integer, primary_key=True)\n"
            "    amount = Column(String)\n")
    root = _make_dir({"expense.py": short, "expenses.py": long})
    n = _dedupe_class_files(root, "model")
    shutil.rmtree(root, ignore_errors=True)
    assert n == 1


def test_no_op_when_no_singular_plural_partner_exists():
    root = _make_dir({"widget.py": "from app.database import Base\nclass Widget(Base):\n    pass\n"})
    n = _dedupe_class_files(root, "model")
    shutil.rmtree(root, ignore_errors=True)
    assert n == 0


def test_no_op_when_partner_files_share_no_related_class_names():
    """user.py and users.py exist, but define totally unrelated classes --
    must not be treated as a singular/plural collision."""
    root = _make_dir({
        "user.py": "from app.database import Base\nclass Session(Base):\n    pass\n",
        "users.py": "from app.database import Base\nclass Widget(Base):\n    pass\n",
    })
    n = _dedupe_class_files(root, "model")
    shutil.rmtree(root, ignore_errors=True)
    assert n == 0


def test_missing_dir_is_a_noop():
    root = Path(tempfile.mkdtemp(prefix="dedupetest_"))
    assert _dedupe_class_files(root / "does_not_exist", "model") == 0
    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    import traceback
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
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
