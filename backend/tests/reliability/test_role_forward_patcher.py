"""
Verifies _patch_forward_role_to_duplicate_registrars (deterministic_patcher.py):
some generated apps have a SECOND, LLM-authored registration endpoint
(e.g. app/routes/api_routes.py, mirroring an /api/-prefixed path the
injected auth_routes.py doesn't serve) that imports and reuses _make_user
from auth_routes.py but calls it with only 3 positional args, silently
dropping role.

Root cause this fixes (confirmed live, 2026-07-11): a generated restaurant
app's app/routes/api_routes.py had exactly this shape. A signup with
role="staff" parsed correctly, reached THIS handler (a different path
than auth_routes.py's own), and still saved the schema's default role --
no error, just silently the wrong role, defeating the role-aware auth
template fix without any visible failure.

Run directly: python tests/reliability/test_role_forward_patcher.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import _patch_forward_role_to_duplicate_registrars


def _make_project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="roleforward_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


_API_ROUTES_REAL_SHAPE = '''\
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.routes.auth_routes import SignupRequest, LoginRequest, _identifier_value, _make_user, _read_password

api_router = APIRouter()

@api_router.post("/api/auth/register")
def api_register(req: SignupRequest, db: Session = Depends(get_db)):
    user = _make_user(req.email, req.password, req.display_name)
    db.add(user)
    return {"ok": True}
'''


def test_forwards_role_in_the_real_confirmed_shape():
    root = _make_project({"app/routes/api_routes.py": _API_ROUTES_REAL_SHAPE})
    n = _patch_forward_role_to_duplicate_registrars(root)
    content = (root / "app/routes/api_routes.py").read_text(encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    assert n == 1
    assert "getattr(req, 'role', None)" in content
    assert "_make_user(req.email, req.password, req.display_name, getattr(req, 'role', None))" in content


def test_auth_routes_py_itself_is_never_touched():
    """The template already handles its own role forwarding -- this
    patcher must not double-patch it."""
    root = _make_project({"app/routes/auth_routes.py": _API_ROUTES_REAL_SHAPE.replace(
        "app.routes.auth_routes import", "app.routes.somewhere_else import")})
    n = _patch_forward_role_to_duplicate_registrars(root)
    shutil.rmtree(root, ignore_errors=True)
    assert n == 0


def test_file_without_the_auth_routes_import_is_untouched():
    """A 3-arg _make_user-shaped call in a file that ISN'T importing from
    auth_routes.py is not this pattern -- must not touch unrelated code."""
    root = _make_project({"app/routes/other.py":
        'def f(req):\n    _make_user(req.email, req.password, req.display_name)\n'})
    n = _patch_forward_role_to_duplicate_registrars(root)
    content = (root / "app/routes/other.py").read_text(encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    assert n == 0
    assert "getattr" not in content


def test_already_4_arg_call_is_left_alone():
    root = _make_project({"app/routes/api_routes.py":
        'from app.routes.auth_routes import _make_user\n'
        'def f(req):\n    _make_user(req.email, req.password, req.display_name, req.role)\n'})
    n = _patch_forward_role_to_duplicate_registrars(root)
    content = (root / "app/routes/api_routes.py").read_text(encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    assert n == 0
    assert content.count("_make_user(") == 1


def test_no_routes_dir_is_a_noop():
    root = Path(tempfile.mkdtemp(prefix="roleforward_"))
    n = _patch_forward_role_to_duplicate_registrars(root)
    shutil.rmtree(root, ignore_errors=True)
    assert n == 0


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
