"""
Experiment 085: regression tests for cross-file request-field validation.

Exp084 root-caused why the SignupRequest.username AttributeError class
(53% of recent failures, Exp083) never self-heals: Architecture Repair
can regenerate auth_routes.py referencing an imported (not inline)
request schema with a missing field, `skip_protected_injections=True`
disables the one mechanism that would normally re-fix it, and neither
existing safety net catches the gap -- `ensure_auth_completeness()`
only checks endpoint existence/wiring, and Exp064's own
`_check_request_field_consistency` is deliberately same-file-only.

This experiment extends `_check_request_field_consistency`
(fix_writer_service.py) with optional cross-file resolution (a new
`project_path` parameter, default None) and wires it into
`check_auth_completeness()` (auth_completeness.py), scoped to only the
files that actually define a required/recommended auth endpoint.

Run directly: python tests/reliability/test_exp085_cross_file_auth_validation.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.fix_writer_service import _check_request_field_consistency
from app.repair.auth_completeness import check_auth_completeness


# ---------------------------------------------------------------------------
# _check_request_field_consistency: same-file behavior unchanged (Task 4)
# ---------------------------------------------------------------------------

_SAME_FILE_CORRECT = '''\
from pydantic import BaseModel

class SignupRequest(BaseModel):
    email: str
    password: str
    display_name: str = ""

def signup(req: SignupRequest):
    return {"email": req.email, "name": req.display_name}
'''

_SAME_FILE_MISMATCH = '''\
from pydantic import BaseModel

class SignupRequest(BaseModel):
    email: str
    password: str
    display_name: str = ""

def signup(req: SignupRequest):
    return {"email": req.email, "name": req.username}
'''


def test_same_file_matching_fields_still_passes_with_project_path_none():
    ok, reason = _check_request_field_consistency("app/routes/auth_routes.py", _SAME_FILE_CORRECT)
    assert ok is True and reason is None


def test_same_file_mismatch_still_rejected_with_project_path_none():
    ok, reason = _check_request_field_consistency("app/routes/auth_routes.py", _SAME_FILE_MISMATCH)
    assert ok is False
    assert "defined in this same file" in reason
    assert "username" in reason


def test_same_file_behavior_identical_whether_or_not_project_path_given():
    # A same-file definition always takes precedence -- passing a
    # (nonexistent, irrelevant) project_path must not change the outcome
    # for a class that's already resolvable locally.
    with tempfile.TemporaryDirectory() as td:
        ok1, reason1 = _check_request_field_consistency("app/routes/auth_routes.py", _SAME_FILE_MISMATCH)
        ok2, reason2 = _check_request_field_consistency(
            "app/routes/auth_routes.py", _SAME_FILE_MISMATCH, project_path=td
        )
        assert (ok1, reason1) == (ok2, reason2)


# ---------------------------------------------------------------------------
# Cross-file resolution (Task 1/2)
# ---------------------------------------------------------------------------

_SCHEMA_FILE = '''\
from pydantic import BaseModel

class SignupRequest(BaseModel):
    email: str
    password: str
    display_name: str = ""
'''

_ROUTE_FILE_IMPORTED_MATCHING = '''\
from fastapi import APIRouter
from app.schemas.auth import SignupRequest

router = APIRouter()

@router.post("/auth/signup")
def signup(req: SignupRequest):
    return {"email": req.email, "name": req.display_name}
'''

_ROUTE_FILE_IMPORTED_MISMATCH = '''\
from fastapi import APIRouter
from app.schemas.auth import SignupRequest

router = APIRouter()

@router.post("/auth/signup")
def signup(req: SignupRequest):
    return {"email": req.email, "name": req.username}
'''

_ROUTE_FILE_UNRELATED_IMPORT = '''\
from fastapi import APIRouter
from app.schemas.auth import SignupRequest
from app.utils.auth import hash_password, verify_password

router = APIRouter()

@router.post("/auth/signup")
def signup(req: SignupRequest):
    hash_password(req.password)
    return {"email": req.email, "name": req.display_name}
'''


def _make_project(tmpdir: str, route_content: str) -> Path:
    root = Path(tmpdir)
    (root / "app" / "schemas").mkdir(parents=True, exist_ok=True)
    (root / "app" / "routes").mkdir(parents=True, exist_ok=True)
    (root / "app" / "schemas" / "auth.py").write_text(_SCHEMA_FILE, encoding="utf-8")
    (root / "app" / "routes" / "auth_routes.py").write_text(route_content, encoding="utf-8")
    return root


def test_imported_schema_matching_fields_passes():
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(td, _ROUTE_FILE_IMPORTED_MATCHING)
        ok, reason = _check_request_field_consistency(
            "app/routes/auth_routes.py", _ROUTE_FILE_IMPORTED_MATCHING, project_path=str(root)
        )
        assert ok is True and reason is None


def test_imported_schema_mismatch_is_now_caught():
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(td, _ROUTE_FILE_IMPORTED_MISMATCH)
        ok, reason = _check_request_field_consistency(
            "app/routes/auth_routes.py", _ROUTE_FILE_IMPORTED_MISMATCH, project_path=str(root)
        )
        assert ok is False, "this is the exact Exp084-confirmed gap -- must now be caught"
        assert "imported into this file" in reason
        assert "username" in reason
        assert "email" in reason and "password" in reason and "display_name" in reason


def test_imported_schema_mismatch_not_caught_without_project_path():
    # Characterizes the pre-Exp085 gap directly: omitting project_path
    # (write_fix()'s own existing call) still can't see a cross-file class.
    with tempfile.TemporaryDirectory() as td:
        _make_project(td, _ROUTE_FILE_IMPORTED_MISMATCH)
        ok, reason = _check_request_field_consistency(
            "app/routes/auth_routes.py", _ROUTE_FILE_IMPORTED_MISMATCH
        )
        assert ok is True, "without project_path, the class is unresolvable and correctly a no-op"


def test_unrelated_import_does_not_interfere():
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(td, _ROUTE_FILE_UNRELATED_IMPORT)
        (root / "app" / "utils").mkdir(parents=True, exist_ok=True)
        (root / "app" / "utils" / "auth.py").write_text(
            "def hash_password(pw): return pw\ndef verify_password(pw, h): return True\n",
            encoding="utf-8",
        )
        ok, reason = _check_request_field_consistency(
            "app/routes/auth_routes.py", _ROUTE_FILE_UNRELATED_IMPORT, project_path=str(root)
        )
        assert ok is True and reason is None, (
            "a plain-function import unrelated to any BaseModel must not be flagged or crash resolution"
        )


def test_unresolvable_import_is_conservatively_ignored():
    # Import target doesn't exist on disk at all -- must not crash or
    # false-positive; only ADDS information, never guesses.
    route = '''\
from fastapi import APIRouter
from app.schemas.nonexistent import SignupRequest

router = APIRouter()

@router.post("/auth/signup")
def signup(req: SignupRequest):
    return {"name": req.username}
'''
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "app" / "routes").mkdir(parents=True, exist_ok=True)
        (root / "app" / "routes" / "auth_routes.py").write_text(route, encoding="utf-8")
        ok, reason = _check_request_field_consistency(
            "app/routes/auth_routes.py", route, project_path=str(root)
        )
        assert ok is True, "an unresolvable import must never be flagged as a false-positive mismatch"


def test_external_package_import_ignored():
    # from pydantic import BaseModel itself, or any non-"app."-rooted
    # import, must never be treated as a project-local schema file.
    route = '''\
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

class SignupRequest(BaseModel):
    email: str

@router.post("/auth/signup")
def signup(req: SignupRequest):
    return {"email": req.email}
'''
    with tempfile.TemporaryDirectory() as td:
        ok, reason = _check_request_field_consistency(
            "app/routes/auth_routes.py", route, project_path=td
        )
        assert ok is True and reason is None


# ---------------------------------------------------------------------------
# check_auth_completeness() integration (Task 3)
# ---------------------------------------------------------------------------

def _full_project(tmpdir: str, route_content: str, wired: bool = True) -> Path:
    root = Path(tmpdir)
    (root / "app" / "schemas").mkdir(parents=True, exist_ok=True)
    (root / "app" / "routes").mkdir(parents=True, exist_ok=True)
    (root / "app" / "schemas" / "auth.py").write_text(_SCHEMA_FILE, encoding="utf-8")
    (root / "app" / "routes" / "auth_routes.py").write_text(route_content, encoding="utf-8")
    main_content = (
        "from fastapi import FastAPI\n"
        "from app.routes.auth_routes import router\n"
        "app = FastAPI()\n"
        "app.include_router(router)\n"
        if wired else
        "from fastapi import FastAPI\napp = FastAPI()\n"
    )
    (root / "app" / "main.py").write_text(main_content, encoding="utf-8")
    return root


_ROUTE_WITH_LOGIN_MATCHING = '''\
from fastapi import APIRouter
from app.schemas.auth import SignupRequest

router = APIRouter()

@router.post("/auth/register")
def signup(req: SignupRequest):
    return {"email": req.email, "name": req.display_name}

@router.post("/auth/login")
def login(req: SignupRequest):
    return {"email": req.email}
'''

_ROUTE_WITH_LOGIN_MISMATCH = '''\
from fastapi import APIRouter
from app.schemas.auth import SignupRequest

router = APIRouter()

@router.post("/auth/register")
def signup(req: SignupRequest):
    return {"email": req.email, "name": req.username}

@router.post("/auth/login")
def login(req: SignupRequest):
    return {"email": req.email}
'''


def test_check_auth_completeness_reports_incomplete_on_verified_mismatch():
    with tempfile.TemporaryDirectory() as td:
        root = _full_project(td, _ROUTE_WITH_LOGIN_MISMATCH)
        result = check_auth_completeness(str(root))
        assert result.complete is False
        assert result.field_mismatches, "the mismatch must surface in field_mismatches"
        assert "username" in result.reason


def test_check_auth_completeness_still_complete_when_fields_match():
    with tempfile.TemporaryDirectory() as td:
        root = _full_project(td, _ROUTE_WITH_LOGIN_MATCHING)
        result = check_auth_completeness(str(root))
        assert result.complete is True
        assert result.field_mismatches == []
        assert result.reason == "complete"


def test_check_auth_completeness_endpoint_gap_reported_before_field_check():
    # When endpoints/wiring are already broken, that failure is reported
    # first -- the field check only ever adds an ADDITIONAL reason to
    # reject, never masks an existing, more fundamental one.
    with tempfile.TemporaryDirectory() as td:
        root = _full_project(td, _ROUTE_WITH_LOGIN_MISMATCH, wired=False)
        result = check_auth_completeness(str(root))
        assert result.complete is False
        assert "wired into main.py" in result.reason
        assert result.field_mismatches == [], (
            "field-consistency check should not even run before wiring is confirmed"
        )


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
