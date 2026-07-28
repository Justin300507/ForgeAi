"""
Regression test for _patch_get_current_user_name_collision.

Reproduced live (ForgeBench v1.0, recipe_manager, 2026-07-28): a "/users/me"
endpoint handler was named literally `get_current_user`, colliding with the
real auth dependency of the same name:

    @user_router.get("/users/me", response_model=UserSchema)
    def get_current_user(current_user: Any = Depends(get_current_user)):
        ...

Python evaluates a function's default-argument expressions at DEFINITION
time, so this tries to reference `get_current_user` before its own
assignment statement completes -- NameError: name 'get_current_user' is
not defined, raised the instant the module is imported. The whole app
failed to start (every dimension failing at once), not just this one
endpoint -- and the real app.utils.auth.get_current_user was never even
imported in this file, so every OTHER endpoint depending on it in the same
file would have been broken too, even without the self-reference.

Run directly: python tests/reliability/test_get_current_user_name_collision.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import _patch_get_current_user_name_collision


def _proj(tmp_path: Path) -> Path:
    (tmp_path / "app" / "routes").mkdir(parents=True, exist_ok=True)
    return tmp_path


# Exact real shape reproduced live -- no auth import at all.
NO_IMPORT_COLLISION = '''\
from fastapi import APIRouter, Depends
from typing import Any

user_router = APIRouter()

@user_router.get("/users/me")
def get_current_user(current_user: Any = Depends(get_current_user)):
    return {"id": current_user.id}

@user_router.get("/users/me/favorites")
def get_favorite_recipes(current_user: Any = Depends(get_current_user)):
    return []
'''


def test_renames_colliding_handler_and_injects_missing_import(tmp_path):
    p = _proj(Path(tmp_path))
    route = p / "app" / "routes" / "user_routes.py"
    route.write_text(NO_IMPORT_COLLISION, encoding="utf-8")
    n = _patch_get_current_user_name_collision(p)
    assert n == 1
    out = route.read_text(encoding="utf-8")
    assert "def get_current_user(" not in out
    assert "def _route_get_current_user(" in out
    assert "from app.utils.auth import get_current_user" in out
    # The second endpoint's Depends(get_current_user) must now resolve to
    # the real, imported one -- untouched, still referencing that name.
    assert "def get_favorite_recipes(current_user: Any = Depends(get_current_user))" in out


# Import present but for OTHER names -- must be merged in, not duplicated
# or left as a second, separate import line.
EXISTING_UNRELATED_IMPORT = '''\
from fastapi import APIRouter, Depends
from app.utils.auth import verify_password
from typing import Any

user_router = APIRouter()

@user_router.get("/users/me")
def get_current_user(current_user: Any = Depends(get_current_user)):
    return {"id": current_user.id}
'''


def test_merges_into_existing_unrelated_auth_import(tmp_path):
    p = _proj(Path(tmp_path))
    route = p / "app" / "routes" / "user_routes.py"
    route.write_text(EXISTING_UNRELATED_IMPORT, encoding="utf-8")
    n = _patch_get_current_user_name_collision(p)
    assert n == 1
    out = route.read_text(encoding="utf-8")
    assert out.count("from app.utils.auth import") == 1
    assert "verify_password" in out and "get_current_user" in out


def test_noop_when_no_collision(tmp_path):
    p = _proj(Path(tmp_path))
    route = p / "app" / "routes" / "habit_routes.py"
    clean = (
        "from fastapi import APIRouter, Depends\n"
        "from app.utils.auth import get_current_user\n\n"
        "habit_router = APIRouter()\n\n"
        "@habit_router.get('/habits/me')\n"
        "def get_my_habits(current_user=Depends(get_current_user)):\n"
        "    return []\n"
    )
    route.write_text(clean, encoding="utf-8")
    n = _patch_get_current_user_name_collision(p)
    assert n == 0
    assert route.read_text(encoding="utf-8") == clean


def test_idempotent(tmp_path):
    p = _proj(Path(tmp_path))
    route = p / "app" / "routes" / "user_routes.py"
    route.write_text(NO_IMPORT_COLLISION, encoding="utf-8")
    _patch_get_current_user_name_collision(p)
    once = route.read_text(encoding="utf-8")
    n2 = _patch_get_current_user_name_collision(p)
    assert n2 == 0
    assert route.read_text(encoding="utf-8") == once


if __name__ == "__main__":
    import traceback
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        tmp = tempfile.mkdtemp(prefix="get_current_user_test_")
        try:
            t(tmp)
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception:
            failed += 1
            print(f"ERROR: {t.__name__}:")
            traceback.print_exc()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
