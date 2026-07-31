"""
Exp154: a model import that's PRESENT but points at the wrong sibling
module (e.g. `from app.models.user import User` when the real file is
app/models/users.py) is invisible to _patch_missing_model_imports_in_
routes, which only checks whether SOME import of that class name exists
anywhere, not whether it resolves. ModuleNotFoundError crashes the whole
app at import time.

Confirmed live (subscription_tracker, 2026-07-31): user_routes.py had
`from app.models.user import User` (singular) while auth_routes.py in
the SAME project correctly used `from app.models.users import User`
(plural, the real file). Model-import analogue of Exp149's broken
cross-module router import.

Run directly: python tests/reliability/test_exp154_broken_model_import_module.py
"""
import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import (
    _patch_broken_model_import_module_in_routes as _patch,
)


def _project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="exp154_test_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


MODEL_USER = '''\
from sqlalchemy import Column, Integer, String
from app.database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String)
'''


def test_fixes_singular_module_when_real_file_is_plural():
    root = _project({
        "app/models/users.py": MODEL_USER,
        "app/routes/user_routes.py": (
            "from fastapi import APIRouter, Depends\n"
            "from sqlalchemy.orm import Session\n"
            "from app.database import get_db\n"
            "from app.models.user import User\n\n"
            "user_router = APIRouter()\n\n"
            "@user_router.get('/users')\n"
            "def get_users(db: Session = Depends(get_db)):\n"
            "    return db.query(User).all()\n"
        ),
    })
    try:
        assert _patch(root) == 1
        out = (root / "app/routes/user_routes.py").read_text(encoding="utf-8")
        ast.parse(out)
        assert "from app.models.users import User" in out
        assert "from app.models.user import User" not in out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_already_correct_import_untouched():
    root = _project({
        "app/models/users.py": MODEL_USER,
        "app/routes/user_routes.py": (
            "from app.models.users import User\n\n"
            "def f():\n    return User\n"
        ),
    })
    try:
        before = (root / "app/routes/user_routes.py").read_text(encoding="utf-8")
        assert _patch(root) == 0
        assert (root / "app/routes/user_routes.py").read_text(encoding="utf-8") == before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_drops_broken_duplicate_when_correct_import_already_present():
    root = _project({
        "app/models/users.py": MODEL_USER,
        "app/routes/user_routes.py": (
            "from app.models.users import User\n"
            "from app.models.user import User\n\n"
            "def f():\n    return User\n"
        ),
    })
    try:
        n = _patch(root)
        assert n == 1
        out = (root / "app/routes/user_routes.py").read_text(encoding="utf-8")
        ast.parse(out)
        assert out.count("import User") == 1
        assert "from app.models.users import User" in out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_unknown_class_name_never_touched():
    """A class not in the model index at all (e.g. imported from a
    third-party package by coincidence) is left completely alone."""
    root = _project({
        "app/models/users.py": MODEL_USER,
        "app/routes/misc_routes.py": (
            "from app.models.thirdparty import SomeHelper\n\n"
            "def f():\n    return SomeHelper\n"
        ),
    })
    try:
        before = (root / "app/routes/misc_routes.py").read_text(encoding="utf-8")
        assert _patch(root) == 0
        assert (root / "app/routes/misc_routes.py").read_text(encoding="utf-8") == before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_no_models_or_routes_dir_is_noop():
    root = Path(tempfile.mkdtemp(prefix="exp154_test_"))
    try:
        assert _patch(root) == 0
    finally:
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
