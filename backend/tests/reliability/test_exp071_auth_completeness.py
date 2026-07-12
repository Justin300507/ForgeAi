"""
Experiment 071: regression tests for deterministic auth-route
completeness (app/repair/auth_completeness.py).

Covers Part 5's required scenarios (missing router, missing endpoint,
missing include_router, missing import, duplicate registration,
partial auth, template drift, false positives) plus a replay section
reconstructing the exact failure shape recorded in Experiment 068's
14 forensic bundles (backend/failure_memory/bundles/*.json).

"Replay" here means: no live server, no LLM call (this experiment's
own "no API usage" rule) -- the bundles contain request/response
telemetry, not full generated source, so this replays the *observable
symptom* (a backend where POST /auth/register would 404 because the
route was never registered) as a synthetic fixture, and confirms
ensure_auth_completeness() would have prevented it. This is stated
explicitly rather than implied, per this session's own "Unknown means
Unknown" discipline about what "replay" can and can't mean without a
live process.

Run directly: python tests/reliability/test_exp071_auth_completeness.py
"""
import json
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.repair.auth_completeness import check_auth_completeness, ensure_auth_completeness
import app.repair.auth_completeness as auth_completeness_module


@contextmanager
def _tmp_project():
    td = tempfile.mkdtemp(prefix="exp071_test_")
    try:
        os.makedirs(os.path.join(td, "app", "routes"))
        os.makedirs(os.path.join(td, "app", "models"))
        os.makedirs(os.path.join(td, "app", "utils"))
        _write(td, "app/database.py", (
            "from sqlalchemy import create_engine\n"
            "from sqlalchemy.orm import sessionmaker, declarative_base\n\n"
            "engine = create_engine('sqlite:///./test.db')\n"
            "SessionLocal = sessionmaker(bind=engine)\n"
            "Base = declarative_base()\n\n"
            "def get_db():\n"
            "    db = SessionLocal()\n"
            "    try:\n"
            "        yield db\n"
            "    finally:\n"
            "        db.close()\n"
        ))
        _write(td, "app/models/user.py", (
            "from sqlalchemy import Column, Integer, String, Boolean\n"
            "from app.database import Base\n\n"
            "class User(Base):\n"
            "    __tablename__ = 'users'\n"
            "    id = Column(Integer, primary_key=True)\n"
            "    email = Column(String, unique=True)\n"
            "    hashed_password = Column(String)\n"
            "    display_name = Column(String, default='')\n"
            "    is_active = Column(Boolean, default=True)\n"
        ))
        yield td
    finally:
        shutil.rmtree(td, ignore_errors=True)


def _write(root: str, rel_path: str, content: str) -> None:
    full = os.path.join(root, *rel_path.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


@contextmanager
def _isolated_telemetry_log():
    """Redirects the module's telemetry log to a temp file for the
    duration of a test, restoring the real path afterward -- keeps
    tests from polluting backend/failure_memory/auth_completeness_log.jsonl
    with synthetic data."""
    real_path = auth_completeness_module._TELEMETRY_LOG_PATH
    td = tempfile.mkdtemp(prefix="exp071_telemetry_")
    fake_path = Path(td) / "auth_completeness_log.jsonl"
    auth_completeness_module._TELEMETRY_LOG_PATH = fake_path
    try:
        yield fake_path
    finally:
        auth_completeness_module._TELEMETRY_LOG_PATH = real_path
        shutil.rmtree(td, ignore_errors=True)


_MAIN_NO_AUTH = "from fastapi import FastAPI\napp = FastAPI()\n"
_TASK_ROUTES = (
    "from fastapi import APIRouter\n\n"
    "task_router = APIRouter()\n\n"
    "@task_router.get('/tasks')\n"
    "def list_tasks():\n"
    "    return []\n"
)
_FULL_AUTH_ROUTES = (
    "from fastapi import APIRouter\n\n"
    "auth_router = APIRouter()\n\n"
    "@auth_router.post('/auth/register')\n"
    "def register():\n"
    "    return {}\n\n"
    "@auth_router.post('/auth/login')\n"
    "def login():\n"
    "    return {}\n"
)


# ---------------------------------------------------------------------------
# Part 5: missing router
# ---------------------------------------------------------------------------

def test_missing_router_no_auth_file_at_all():
    with _tmp_project() as td:
        _write(td, "app/main.py", _MAIN_NO_AUTH)
        _write(td, "app/routes/task_routes.py", _TASK_ROUTES)
        r = check_auth_completeness(td)
        assert not r.complete
        assert "POST /auth/register" in r.missing_required
        assert "POST /auth/login" in r.missing_required


# ---------------------------------------------------------------------------
# Part 5: missing endpoint (router exists, one endpoint missing)
# ---------------------------------------------------------------------------

def test_missing_endpoint_login_only():
    with _tmp_project() as td:
        _write(td, "app/main.py", (
            "from fastapi import FastAPI\n"
            "from app.routes.auth_routes import auth_router\n"
            "app = FastAPI()\n"
            "app.include_router(auth_router)\n"
        ))
        _write(td, "app/routes/auth_routes.py", (
            "from fastapi import APIRouter\n\n"
            "auth_router = APIRouter()\n\n"
            "@auth_router.post('/auth/login')\n"
            "def login():\n"
            "    return {}\n"
        ))
        r = check_auth_completeness(td)
        assert not r.complete
        assert r.missing_required == ["POST /auth/register"]


# ---------------------------------------------------------------------------
# Part 5: missing include_router
# ---------------------------------------------------------------------------

def test_missing_include_router():
    with _tmp_project() as td:
        _write(td, "app/main.py", (
            "from fastapi import FastAPI\n"
            "from app.routes.auth_routes import auth_router\n"
            "app = FastAPI()\n"
            # deliberately no app.include_router(auth_router)
        ))
        _write(td, "app/routes/auth_routes.py", _FULL_AUTH_ROUTES)
        r = check_auth_completeness(td)
        assert not r.complete
        assert r.router_import_present is True
        assert r.router_include_present is False
        assert "include_router" in r.reason


# ---------------------------------------------------------------------------
# Part 5: missing import (include_router called but never imported --
# this would be a NameError at real runtime; the check must still catch
# it statically without needing to execute the file)
# ---------------------------------------------------------------------------

def test_missing_import():
    with _tmp_project() as td:
        _write(td, "app/main.py", (
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
            "app.include_router(auth_router)\n"  # never imported
        ))
        _write(td, "app/routes/auth_routes.py", _FULL_AUTH_ROUTES)
        r = check_auth_completeness(td)
        assert not r.complete
        assert r.router_import_present is False
        assert "import" in r.reason


# ---------------------------------------------------------------------------
# Part 5: duplicate registration
# ---------------------------------------------------------------------------

def test_duplicate_registration_detected_but_not_fatal_if_one_is_wired():
    # Mirrors _patch_forward_role_to_duplicate_registrars's own
    # confirmed-live finding: a second, unwired registrar existing
    # alongside a correctly-wired one is a real drift signal worth
    # surfacing, but is not itself what makes the endpoint unreachable.
    with _tmp_project() as td:
        _write(td, "app/main.py", (
            "from fastapi import FastAPI\n"
            "from app.routes.auth_routes import auth_router\n"
            "app = FastAPI()\n"
            "app.include_router(auth_router)\n"
        ))
        _write(td, "app/routes/auth_routes.py", _FULL_AUTH_ROUTES)
        _write(td, "app/routes/api_routes.py", (
            "from fastapi import APIRouter\n\n"
            "api_router = APIRouter()\n\n"
            "@api_router.post('/auth/register')\n"  # duplicate path, different (unwired) router
            "def api_register():\n"
            "    return {}\n"
        ))
        r = check_auth_completeness(td)
        assert r.complete, "one correctly-wired registrar is enough"
        assert any("/auth/register" in d for d in r.duplicate_registrations)


# ---------------------------------------------------------------------------
# Part 5: partial auth (required present, recommended missing -- must
# NOT block completeness, per Part 2's "if architecture requires it")
# ---------------------------------------------------------------------------

def test_partial_auth_missing_recommended_endpoints_still_complete():
    with _tmp_project() as td:
        _write(td, "app/main.py", (
            "from fastapi import FastAPI\n"
            "from app.routes.auth_routes import auth_router\n"
            "app = FastAPI()\n"
            "app.include_router(auth_router)\n"
        ))
        _write(td, "app/routes/auth_routes.py", _FULL_AUTH_ROUTES)  # no /auth/me, no /auth/logout
        r = check_auth_completeness(td)
        assert r.complete, "missing /auth/me and /auth/logout must not block completeness"
        assert "GET /auth/me" in r.missing_recommended
        assert "POST /auth/logout" in r.missing_recommended


# ---------------------------------------------------------------------------
# Part 5: template drift (a working but non-canonical implementation
# must be recognized as complete, per Part 3's "do not overwrite
# application logic")
# ---------------------------------------------------------------------------

def test_template_drift_recognized_as_complete_not_overwritten():
    with _tmp_project() as td:
        _write(td, "app/main.py", (
            "from fastapi import FastAPI\n"
            "from app.routes.auth_routes import auth_router\n"
            "app = FastAPI()\n"
            "app.include_router(auth_router)\n"
        ))
        drifted = (
            "from fastapi import APIRouter\n\n"
            "auth_router = APIRouter()\n\n"
            "# A completely different (but working) implementation shape --\n"
            "# no _read_password sentinel, different variable names, a\n"
            "# custom in-memory user store instead of SQLAlchemy.\n"
            "_users = {}\n\n"
            "@auth_router.post('/auth/register')\n"
            "def custom_signup(email: str, password: str):\n"
            "    _users[email] = password\n"
            "    return {'ok': True}\n\n"
            "@auth_router.post('/auth/login')\n"
            "def custom_login(email: str, password: str):\n"
            "    return {'ok': _users.get(email) == password}\n"
        )
        _write(td, "app/routes/auth_routes.py", drifted)
        r = check_auth_completeness(td)
        assert r.complete
        result = ensure_auth_completeness(td, "exp071_drift_test")
        assert result["status"] == "complete", "a working drifted implementation must not trigger repair"
        # Confirm the file was genuinely untouched (Part 3: "do not overwrite application logic")
        after_content = Path(td, "app", "routes", "auth_routes.py").read_text(encoding="utf-8")
        assert "_users" in after_content, "template injection must not have overwritten the drifted file"


# ---------------------------------------------------------------------------
# Part 5: false positives
# ---------------------------------------------------------------------------

def test_false_positive_prefixed_router_still_detected():
    # APIRouter(prefix="/auth") + @auth_router.post("/register") must
    # resolve to the same effective path as a bare "/auth/register" --
    # a naive string-match-on-decorator-literal check would false-negative here.
    with _tmp_project() as td:
        _write(td, "app/main.py", (
            "from fastapi import FastAPI\n"
            "from app.routes.auth_routes import auth_router\n"
            "app = FastAPI()\n"
            "app.include_router(auth_router)\n"
        ))
        _write(td, "app/routes/auth_routes.py", (
            "from fastapi import APIRouter\n\n"
            "auth_router = APIRouter(prefix='/auth')\n\n"
            "@auth_router.post('/register')\n"
            "def register():\n"
            "    return {}\n\n"
            "@auth_router.post('/login')\n"
            "def login():\n"
            "    return {}\n"
        ))
        r = check_auth_completeness(td)
        assert r.complete, f"prefixed router should resolve correctly, got: {r.reason}"


def test_false_positive_route_mentioned_only_in_comment_not_detected():
    # A route path appearing only in a comment/docstring must NOT count
    # as a real registration -- confirms the AST-based scan (not a
    # naive text search) is actually being used.
    with _tmp_project() as td:
        _write(td, "app/main.py", _MAIN_NO_AUTH)
        _write(td, "app/routes/task_routes.py", (
            "from fastapi import APIRouter\n\n"
            "task_router = APIRouter()\n\n"
            "# TODO: someday add @auth_router.post(\"/auth/register\") here\n"
            "\"\"\"This app should eventually support POST /auth/login too.\"\"\"\n\n"
            "@task_router.get('/tasks')\n"
            "def list_tasks():\n"
            "    return []\n"
        ))
        r = check_auth_completeness(td)
        assert not r.complete, "a route mentioned only in a comment/docstring must not count as registered"


def test_false_positive_trailing_slash_normalized():
    with _tmp_project() as td:
        _write(td, "app/main.py", (
            "from fastapi import FastAPI\n"
            "from app.routes.auth_routes import auth_router\n"
            "app = FastAPI()\n"
            "app.include_router(auth_router)\n"
        ))
        _write(td, "app/routes/auth_routes.py", (
            "from fastapi import APIRouter\n\n"
            "auth_router = APIRouter()\n\n"
            "@auth_router.post('/auth/register/')\n"
            "def register():\n"
            "    return {}\n\n"
            "@auth_router.post('/auth/login/')\n"
            "def login():\n"
            "    return {}\n"
        ))
        r = check_auth_completeness(td)
        assert r.complete, f"trailing-slash paths should normalize to match, got: {r.reason}"


def test_false_positive_malformed_route_file_does_not_crash():
    with _tmp_project() as td:
        _write(td, "app/main.py", _MAIN_NO_AUTH)
        _write(td, "app/routes/broken_routes.py", "def broken(:\n    this is not valid python\n")
        r = check_auth_completeness(td)
        assert not r.complete
        assert r.parse_errors == [] or "broken_routes.py" not in str(r.parse_errors)
        # malformed file is skipped, not fatal -- no exception propagated above


# ---------------------------------------------------------------------------
# Repair scenarios
# ---------------------------------------------------------------------------

def test_ensure_auth_completeness_repairs_missing_router():
    with _tmp_project() as td, _isolated_telemetry_log() as log_path:
        _write(td, "app/main.py", _MAIN_NO_AUTH)
        _write(td, "app/routes/task_routes.py", _TASK_ROUTES)

        before = check_auth_completeness(td)
        assert not before.complete

        result = ensure_auth_completeness(td, "exp071_repair_test")
        assert result["status"] == "repaired"
        assert result["after"].complete

        # Re-verify independently (not trusting the function's own report)
        reverify = check_auth_completeness(td)
        assert reverify.complete
        assert reverify.router_module == "app.routes.auth_routes"

        # Telemetry was actually written
        assert log_path.exists()
        records = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines()]
        assert records[-1]["status"] == "repaired"
        assert records[-1]["project_name"] == "exp071_repair_test"


def test_ensure_auth_completeness_reports_failed_when_no_user_model():
    with _tmp_project() as td, _isolated_telemetry_log() as log_path:
        # Override the fixture's user.py to simulate no user model at all
        os.remove(os.path.join(td, "app", "models", "user.py"))
        _write(td, "app/main.py", _MAIN_NO_AUTH)
        _write(td, "app/routes/task_routes.py", _TASK_ROUTES)

        result = ensure_auth_completeness(td, "exp071_no_model_test")
        assert result["status"] == "failed"
        assert not result["after"].complete

        records = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines()]
        assert records[-1]["status"] == "failed"


def test_ensure_auth_completeness_is_noop_on_already_complete_project():
    with _tmp_project() as td, _isolated_telemetry_log():
        _write(td, "app/main.py", (
            "from fastapi import FastAPI\n"
            "from app.routes.auth_routes import auth_router\n"
            "app = FastAPI()\n"
            "app.include_router(auth_router)\n"
        ))
        _write(td, "app/routes/auth_routes.py", _FULL_AUTH_ROUTES)
        original_mtime = os.path.getmtime(os.path.join(td, "app", "routes", "auth_routes.py"))

        result = ensure_auth_completeness(td, "exp071_noop_test")
        assert result["status"] == "complete"

        # File must be untouched (no write at all, not just no visible diff)
        after_mtime = os.path.getmtime(os.path.join(td, "app", "routes", "auth_routes.py"))
        assert after_mtime == original_mtime


# ---------------------------------------------------------------------------
# Replay: Experiment 068's forensic bundles (backend/failure_memory/bundles/)
#
# 9 of 14 real bundles (FR-000002 through FR-000005, FR-000007 through
# FR-000011 -- confirmed by reading the actual bundle files this cycle,
# not from memory) recorded the exact same observable symptom:
#   POST http://127.0.0.1:8001/auth/register -> 404 {"detail": "Not Found"}
# all against project "todo_list_app", spanning commits e7b1878, def583f,
# f1b636d on 2026-07-11. No live server or LLM call is used here (this
# experiment's own rules) -- this replays the shape (a project whose
# auth_routes.py was never generated/wired) and confirms
# ensure_auth_completeness() converts it to a working state.
# ---------------------------------------------------------------------------

def test_replay_exp068_bundle_missing_auth_register_404():
    with _tmp_project() as td, _isolated_telemetry_log():
        # Reconstructs the FR-000002..FR-000011 shape: a todo-app-like
        # backend with a User model and other routes, but no auth
        # surface at all -- exactly what a 404 on POST /auth/register
        # against a live instance of this project would mean.
        _write(td, "app/main.py", (
            "from fastapi import FastAPI\n"
            "from app.routes.task_routes import task_router\n"
            "app = FastAPI()\n"
            "app.include_router(task_router)\n"
        ))
        _write(td, "app/routes/task_routes.py", (
            "from fastapi import APIRouter\n\n"
            "task_router = APIRouter()\n\n"
            "@task_router.post('/tasks')\n"
            "def create_task():\n"
            "    return {}\n\n"
            "@task_router.get('/tasks')\n"
            "def list_tasks():\n"
            "    return []\n"
        ))

        before = check_auth_completeness(td)
        assert not before.complete
        assert "POST /auth/register" in before.missing_required, (
            "this is the exact endpoint 9/14 real Exp068 bundles recorded as 404"
        )

        result = ensure_auth_completeness(td, "todo_list_app_replay")
        assert result["status"] == "repaired"
        assert result["after"].complete
        assert "POST /auth/register" not in result["after"].missing_required


def test_replay_exp068_bundles_non_auth_symptoms_correctly_ignored():
    # The other 5/14 bundles are NOT auth-related (FR-000001: seed FK
    # issue on POST /tasks; FR-000006: journey-runner hit the wrong
    # endpoint /stats/summary; FR-000012-000014: PUT /products/{id}
    # edit-endpoint 405s on inventory_manager) -- confirms this
    # completeness check does not misfire on an already-complete auth
    # surface just because OTHER unrelated endpoints are broken.
    with _tmp_project() as td, _isolated_telemetry_log():
        _write(td, "app/main.py", (
            "from fastapi import FastAPI\n"
            "from app.routes.auth_routes import auth_router\n"
            "from app.routes.product_routes import product_router\n"
            "app = FastAPI()\n"
            "app.include_router(auth_router)\n"
            "app.include_router(product_router)\n"
        ))
        _write(td, "app/routes/auth_routes.py", _FULL_AUTH_ROUTES)
        _write(td, "app/routes/product_routes.py", (
            "from fastapi import APIRouter\n\n"
            "product_router = APIRouter()\n\n"
            "@product_router.get('/products/{product_id}')\n"
            "def get_product(product_id: int):\n"
            "    return {}\n"
            "# NOTE: no PUT handler -- this is the FR-000012..14 bundle shape\n"
            "# (405 on PUT /products/6), but it is NOT an auth-completeness\n"
            "# concern and must not be reported as one.\n"
        ))
        r = check_auth_completeness(td)
        assert r.complete, "a broken non-auth endpoint must not affect the auth-completeness verdict"


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
