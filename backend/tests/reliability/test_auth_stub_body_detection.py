"""
Regression tests for stub-body detection in app/repair/auth_completeness.py.

Reproduced live (habit_tracker, 2026-07-25): Architecture Repair
regenerated auth_routes.py to add an unrelated PUT /auth/update endpoint,
and in the same response reduced POST /auth/signup and POST /auth/login
to bare `pass` bodies. Every existing structural check (right decorator,
right path, router imported and included in main.py) passed anyway --
check_auth_completeness() only ever checked endpoint *presence* and
*wiring*, never whether the handler body was a real implementation. A
stub is functionally identical to the endpoint not existing at all from
a caller's perspective (500 or a silent None), but was invisible to
every check that only looks at decorators.

Run directly: python tests/reliability/test_auth_stub_body_detection.py
"""
import ast
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.repair.auth_completeness import (
    check_auth_completeness,
    _is_stub_function_body,
    _find_stub_required_endpoints,
)


def test_is_stub_function_body_detects_bare_pass():
    tree = ast.parse("def f():\n    pass\n")
    assert _is_stub_function_body(tree.body[0]) is True


def test_is_stub_function_body_detects_docstring_plus_pass():
    tree = ast.parse('def f():\n    """Do the thing."""\n    pass\n')
    assert _is_stub_function_body(tree.body[0]) is True


def test_is_stub_function_body_detects_ellipsis():
    tree = ast.parse("def f():\n    ...\n")
    assert _is_stub_function_body(tree.body[0]) is True


def test_is_stub_function_body_detects_not_implemented():
    tree = ast.parse("def f():\n    raise NotImplementedError('todo')\n")
    assert _is_stub_function_body(tree.body[0]) is True


def test_is_stub_function_body_false_for_real_implementation():
    tree = ast.parse("def f(db, x):\n    db.add(x)\n    db.commit()\n    return x\n")
    assert _is_stub_function_body(tree.body[0]) is False


def test_is_stub_function_body_false_for_expression_with_side_effect():
    # A single non-constant expression statement (e.g. a call) is real
    # logic, not a stub, even though it's the only statement in the body.
    tree = ast.parse("def f(db):\n    db.commit()\n")
    assert _is_stub_function_body(tree.body[0]) is False


_SCHEMA_FILE = (
    "from pydantic import BaseModel\n\n"
    "class SignupRequest(BaseModel):\n"
    "    email: str\n"
    "    password: str\n"
)

_ROUTE_STUB_SIGNUP = (
    "from fastapi import APIRouter\n"
    "from app.schemas.auth import SignupRequest\n\n"
    "router = APIRouter()\n\n"
    "@router.post(\"/auth/signup\")\n"
    "def signup(req: SignupRequest):\n"
    "    # Implementation for user signup\n"
    "    pass\n\n"
    "@router.post(\"/auth/login\")\n"
    "def login(req: SignupRequest):\n"
    "    return {\"email\": req.email}\n"
)

_ROUTE_ELLIPSIS_STUB_SIGNUP = (
    "from fastapi import APIRouter\n"
    "from app.schemas.auth import SignupRequest\n\n"
    "router = APIRouter()\n\n"
    "@router.post(\"/auth/signup\")\n"
    "def signup(req: SignupRequest):\n"
    "    \"\"\"Create a new user.\"\"\"\n"
    "    ...\n\n"
    "@router.post(\"/auth/login\")\n"
    "def login(req: SignupRequest):\n"
    "    return {\"email\": req.email}\n"
)

_ROUTE_REAL_BODIES = (
    "from fastapi import APIRouter\n"
    "from app.schemas.auth import SignupRequest\n\n"
    "router = APIRouter()\n\n"
    "@router.post(\"/auth/signup\")\n"
    "def signup(req: SignupRequest):\n"
    "    return {\"email\": req.email}\n\n"
    "@router.post(\"/auth/login\")\n"
    "def login(req: SignupRequest):\n"
    "    return {\"email\": req.email}\n"
)


def _full_project(tmpdir: str, route_content: str) -> Path:
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
    )
    (root / "app" / "main.py").write_text(main_content, encoding="utf-8")
    return root


def test_check_auth_completeness_rejects_stub_signup():
    with tempfile.TemporaryDirectory() as td:
        root = _full_project(td, _ROUTE_STUB_SIGNUP)
        result = check_auth_completeness(str(root))
        assert result.complete is False
        assert result.stub_required == ["POST /auth/signup"]
        assert "stubbed" in result.reason


def test_check_auth_completeness_rejects_ellipsis_stub_with_docstring():
    with tempfile.TemporaryDirectory() as td:
        root = _full_project(td, _ROUTE_ELLIPSIS_STUB_SIGNUP)
        result = check_auth_completeness(str(root))
        assert result.complete is False
        assert result.stub_required == ["POST /auth/signup"]


def test_check_auth_completeness_still_complete_with_real_bodies():
    with tempfile.TemporaryDirectory() as td:
        root = _full_project(td, _ROUTE_REAL_BODIES)
        result = check_auth_completeness(str(root))
        assert result.complete is True
        assert result.stub_required == []
        assert result.reason == "complete"


def test_find_stub_required_endpoints_tolerates_a_real_impl_among_duplicates():
    # POST /auth/signup registered in two files -- one a stub, one real.
    # A real implementation anywhere is sufficient (mirrors the existing
    # any_wired tolerance for duplicate registrations elsewhere in this
    # module).
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "app" / "routes").mkdir(parents=True, exist_ok=True)
        (root / "app" / "routes" / "auth_routes.py").write_text(_ROUTE_STUB_SIGNUP, encoding="utf-8")
        (root / "app" / "routes" / "auth_routes_v2.py").write_text(_ROUTE_REAL_BODIES, encoding="utf-8")
        found = {
            ("POST", "/auth/signup"): [
                ("app/routes/auth_routes.py", "router"),
                ("app/routes/auth_routes_v2.py", "router"),
            ],
        }
        stubs = _find_stub_required_endpoints(root, found)
        assert stubs == []


def test_find_stub_required_endpoints_noop_when_endpoint_absent():
    # Not present at all -- already reported via missing_required, must
    # not also appear as a stub (would double-report the same gap).
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        stubs = _find_stub_required_endpoints(root, {})
        assert stubs == []


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
