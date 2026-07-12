"""
Experiment 064: regression tests for the narrow semantic write-time
consistency guard (app/services/fix_writer_service.py's
_check_request_field_consistency), added to write_fix() right after the
existing syntax guard (_is_safe_to_write).

Root cause (Exp063, docs/EXP063_PYDANTIC_ROOT_CAUSE.md): a repaired
auth_routes.py was perfectly valid Python (passed the syntax guard) but
internally self-inconsistent -- `req.username` was read in the signup()
handler while the SAME file's `SignupRequest(BaseModel)` class never
declared a `username` field. This guard catches exactly that shape:
"every attribute access on a locally-typed Pydantic request parameter
resolves to a field that class actually declares, in the same file."

This is NOT a generalized semantic analyzer -- no cross-file
resolution, no type inference beyond a bare `Name` or `Name | None` /
`Optional[Name]` parameter annotation.

Run directly: python tests/reliability/test_semantic_write_validation.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.fix_writer_service import (
    _check_request_field_consistency,
    write_fix,
)

_CORRECT_REQUEST = '''\
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter()


class SignupRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)
    display_name: str = ""


@router.post("/auth/signup")
def signup(req: SignupRequest):
    return {"email": req.email, "name": req.display_name}
'''

_INCORRECT_REQUEST = '''\
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter()


class SignupRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)
    display_name: str = ""


@router.post("/auth/signup")
def signup(req: SignupRequest):
    return {"email": req.email, "name": req.username}
'''


# ── Task's own explicit categories ────────────────────────────────────────

def test_correct_request_passes():
    ok, reason = _check_request_field_consistency("app/routes/auth_routes.py", _CORRECT_REQUEST)
    assert ok is True
    assert reason is None


def test_incorrect_request_rejected():
    ok, reason = _check_request_field_consistency("app/routes/auth_routes.py", _INCORRECT_REQUEST)
    assert ok is False
    assert "username" in reason
    assert "SignupRequest" in reason
    assert "signup" in reason


def test_multiple_request_models_only_flags_the_actual_mismatch():
    src = '''\
from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class SignupRequest(BaseModel):
    email: str
    password: str
    display_name: str = ""


def login(req: LoginRequest):
    return req.email, req.password  # both fine


def signup(req: SignupRequest):
    return req.email, req.username  # username is NOT declared
'''
    ok, reason = _check_request_field_consistency("app/routes/x.py", src)
    assert ok is False
    assert "username" in reason
    assert "signup" in reason


def test_multiple_request_models_all_correct_passes():
    src = '''\
from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class SignupRequest(BaseModel):
    email: str
    password: str
    display_name: str = ""


def login(req: LoginRequest):
    return req.email, req.password


def signup(req: SignupRequest):
    return req.email, req.password, req.display_name
'''
    ok, reason = _check_request_field_consistency("app/routes/x.py", src)
    assert ok is True


def test_nested_handler_inherits_outer_param_via_closure():
    # A nested helper that does NOT redeclare `req` refers to the SAME
    # outer object via closure -- an access to a field the outer req's
    # class doesn't have must still be caught.
    src = '''\
from pydantic import BaseModel


class SignupRequest(BaseModel):
    email: str
    password: str


def signup(req: SignupRequest):
    def _log():
        print(req.username)  # closes over outer `req` -- still SignupRequest
    _log()
    return req.email
'''
    ok, reason = _check_request_field_consistency("app/routes/x.py", src)
    assert ok is False
    assert "username" in reason


def test_nested_handler_with_shadowed_param_is_independent():
    # The nested function re-declares its OWN `req` parameter (a
    # different, undeclared-here type) -- must NOT be checked against
    # the outer SignupRequest at all (would be a false positive).
    src = '''\
from pydantic import BaseModel


class SignupRequest(BaseModel):
    email: str
    password: str


def signup(req: SignupRequest):
    def _inner(req):
        return req.anything_at_all  # different `req`, untyped -- not our concern
    return req.email, _inner(None)
'''
    ok, reason = _check_request_field_consistency("app/routes/x.py", src)
    assert ok is True


def test_existing_syntax_failure_is_not_this_checks_job():
    # A file that doesn't even parse must return (True, None) from THIS
    # check -- _is_safe_to_write (the syntax guard, which runs first in
    # write_fix) is what rejects it; this check must not also try to
    # analyze broken syntax or raise.
    broken = "def signup(req: SignupRequest\n    return req.username"
    ok, reason = _check_request_field_consistency("app/routes/x.py", broken)
    assert ok is True
    assert reason is None


def test_false_positive_protection_property_access():
    src = '''\
from pydantic import BaseModel


class SignupRequest(BaseModel):
    email: str

    @property
    def normalized_email(self):
        return self.email.lower()


def signup(req: SignupRequest):
    return req.normalized_email  # a real @property, not a plain field
'''
    ok, reason = _check_request_field_consistency("app/routes/x.py", src)
    assert ok is True


def test_false_positive_protection_pydantic_reserved_attrs():
    src = '''\
from pydantic import BaseModel


class SignupRequest(BaseModel):
    model_config = {"from_attributes": True}
    email: str


def signup(req: SignupRequest):
    return req.model_dump(), req.model_config, req.email
'''
    ok, reason = _check_request_field_consistency("app/routes/x.py", src)
    assert ok is True


def test_false_positive_protection_non_pydantic_class_ignored():
    src = '''\
class PlainHelper:
    def __init__(self):
        self.whatever = 1


def do_thing(h: PlainHelper):
    return h.something_not_declared_anywhere  # not a Pydantic class -- out of scope
'''
    ok, reason = _check_request_field_consistency("app/routes/x.py", src)
    assert ok is True


def test_false_positive_protection_optional_annotation():
    src = '''\
from typing import Optional
from pydantic import BaseModel


class SignupRequest(BaseModel):
    email: str
    display_name: str = ""


def signup(req: Optional[SignupRequest] = None):
    return req.email, req.display_name
'''
    ok, reason = _check_request_field_consistency("app/routes/x.py", src)
    assert ok is True


def test_optional_annotation_still_catches_mismatch():
    src = '''\
from typing import Optional
from pydantic import BaseModel


class SignupRequest(BaseModel):
    email: str


def signup(req: Optional[SignupRequest] = None):
    return req.username
'''
    ok, reason = _check_request_field_consistency("app/routes/x.py", src)
    assert ok is False


def test_pipe_none_annotation_catches_mismatch():
    src = '''\
from pydantic import BaseModel


class SignupRequest(BaseModel):
    email: str


def signup(req: SignupRequest | None):
    return req.username
'''
    ok, reason = _check_request_field_consistency("app/routes/x.py", src)
    assert ok is False


def test_local_inheritance_resolved():
    src = '''\
from pydantic import BaseModel


class BaseRequest(BaseModel):
    request_id: str


class SignupRequest(BaseRequest):
    email: str


def signup(req: SignupRequest):
    return req.request_id, req.email  # request_id inherited from BaseRequest
'''
    ok, reason = _check_request_field_consistency("app/routes/x.py", src)
    assert ok is True


def test_local_inheritance_still_catches_mismatch():
    src = '''\
from pydantic import BaseModel


class BaseRequest(BaseModel):
    request_id: str


class SignupRequest(BaseRequest):
    email: str


def signup(req: SignupRequest):
    return req.request_id, req.username
'''
    ok, reason = _check_request_field_consistency("app/routes/x.py", src)
    assert ok is False


def test_non_python_file_always_passes():
    ok, reason = _check_request_field_consistency("src/pages/LoginPage.jsx", "garbage { not python at all")
    assert ok is True


def test_no_pydantic_classes_at_all_is_a_noop():
    src = "def add(a, b):\n    return a + b\n"
    ok, reason = _check_request_field_consistency("app/routes/x.py", src)
    assert ok is True


# ── Replay: the exact Exp063 corruption, against the real files ─────────────

def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_replay_exact_todo_corruption_is_rejected():
    repo_root = os.path.join(os.path.dirname(__file__), "..", "..", "..")
    path = os.path.join(repo_root, "generated_projects", "todo_list_app", "app", "routes", "auth_routes.py")
    if not os.path.exists(path):
        print("SKIP (fixture not present in this environment): test_replay_exact_todo_corruption_is_rejected")
        return
    content = _read(path)
    ok, reason = _check_request_field_consistency("app/routes/auth_routes.py", content)
    assert ok is False, "must reject the exact confirmed Exp063 corruption"
    assert "username" in reason
    assert "SignupRequest" in reason


def test_replay_exact_blog_cms_corruption_is_rejected():
    repo_root = os.path.join(os.path.dirname(__file__), "..", "..", "..")
    path = os.path.join(repo_root, "generated_projects", "forge_blog_cms", "app", "routes", "auth_routes.py")
    if not os.path.exists(path):
        print("SKIP (fixture not present in this environment): test_replay_exact_blog_cms_corruption_is_rejected")
        return
    content = _read(path)
    ok, reason = _check_request_field_consistency("app/routes/auth_routes.py", content)
    assert ok is False
    assert "username" in reason


def test_replay_crm_pristine_template_passes():
    repo_root = os.path.join(os.path.dirname(__file__), "..", "..", "..")
    path = os.path.join(repo_root, "generated_projects", "simple_crm", "app", "routes", "auth_routes.py")
    if not os.path.exists(path):
        print("SKIP (fixture not present in this environment): test_replay_crm_pristine_template_passes")
        return
    content = _read(path)
    ok, reason = _check_request_field_consistency("app/routes/auth_routes.py", content)
    assert ok is True, f"crm's pristine template must pass cleanly, got: {reason}"


def test_replay_inventory_unaffected():
    repo_root = os.path.join(os.path.dirname(__file__), "..", "..", "..")
    proj_dir = os.path.join(repo_root, "generated_projects", "inventory_manager")
    if not os.path.isdir(proj_dir):
        print("SKIP (fixture not present in this environment): test_replay_inventory_unaffected")
        return
    flagged = []
    for root, _dirs, files in os.walk(os.path.join(proj_dir, "app")):
        for f in files:
            if not f.endswith(".py"):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, proj_dir).replace(os.sep, "/")
            ok, reason = _check_request_field_consistency(rel, _read(full))
            if not ok:
                flagged.append((rel, reason))
    assert flagged == [], f"inventory_manager must be entirely unaffected, got: {flagged}"


# ── write_fix() end-to-end: the rejection actually blocks the write ────────

def test_write_fix_rejects_and_does_not_write():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        result = write_fix(td, {"path": "app/routes/auth_routes.py", "content": _INCORRECT_REQUEST})
        assert result is False
        assert not os.path.exists(os.path.join(td, "app", "routes", "auth_routes.py")), \
            "a semantically inconsistent file must never be written to disk"


def test_write_fix_accepts_correct_request():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        result = write_fix(td, {"path": "app/routes/auth_routes.py", "content": _CORRECT_REQUEST})
        assert result is True
        assert os.path.exists(os.path.join(td, "app", "routes", "auth_routes.py"))


def test_write_fix_still_rejects_syntax_errors_first():
    # Confirms the two guards compose correctly -- a syntactically broken
    # file is still caught by the pre-existing guard, not silently passed
    # through to the new one.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        broken = "def signup(req: SignupRequest\n    return req.username"
        result = write_fix(td, {"path": "app/routes/auth_routes.py", "content": broken})
        assert result is False
        assert not os.path.exists(os.path.join(td, "app", "routes", "auth_routes.py"))


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
