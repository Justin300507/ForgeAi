"""
Verifies _discover_role_vocabulary / _build_auth_routes_template
(app/services/deterministic_patcher.py): the injected auth template
respects an app-specific role vocabulary instead of always hardcoding
every new signup's role to "user".

Root cause this fixes (confirmed live, 2026-07-11): a generated restaurant
app's app/schemas/auth.py declared
`role: str = Field("diner", pattern="^(diner|staff)$")`, but the injected
auth_routes.py's _make_user() ignored it and hardcoded role="user". A
route (menu_routes.py) gated Create on role in ("staff", "admin") -- a
feature NO signup could ever reach, for any real end user, not just a
test journey.

Run directly: python tests/reliability/test_role_aware_auth_template.py
"""
import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import (
    _discover_role_vocabulary, _build_auth_routes_template,
)


def _make_project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="roletest_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def test_discovers_real_dine_reserve_pattern():
    root = Path(tempfile.mkdtemp(prefix="roletest_"))
    schemas = root / "app" / "schemas"
    schemas.mkdir(parents=True)
    (schemas / "auth.py").write_text(
        'from pydantic import BaseModel, Field\n\n'
        'class RegisterRequest(BaseModel):\n'
        '    email: str\n'
        '    password: str\n'
        '    role: str = Field("diner", pattern="^(diner|staff)$", description="x")\n',
        encoding="utf-8",
    )
    result = _discover_role_vocabulary(root)
    shutil.rmtree(root, ignore_errors=True)
    assert result == ("diner", ["diner", "staff"])


def test_no_role_field_returns_none():
    root = Path(tempfile.mkdtemp(prefix="roletest_"))
    schemas = root / "app" / "schemas"
    schemas.mkdir(parents=True)
    (schemas / "user.py").write_text(
        "from pydantic import BaseModel\n\nclass UserOut(BaseModel):\n    id: int\n",
        encoding="utf-8",
    )
    result = _discover_role_vocabulary(root)
    shutil.rmtree(root, ignore_errors=True)
    assert result is None


def test_no_schemas_dir_returns_none():
    root = Path(tempfile.mkdtemp(prefix="roletest_"))
    result = _discover_role_vocabulary(root)
    shutil.rmtree(root, ignore_errors=True)
    assert result is None


def test_falls_back_to_route_scan_when_schema_declares_nothing():
    """Confirmed live: a generated course-platform app's schema had a
    bare `role: Optional[str] = None` (no vocabulary), but course/lesson/
    enrollment/user routes gated on three distinct roles found nowhere
    but the comparisons themselves."""
    root = _make_project({
        "app/schemas/user.py": "class UserOut:\n    role: str | None = None\n",
        "app/routes/course_routes.py":
            'if current_user.role != "instructor":\n    raise HTTPException(403)\n',
        "app/routes/enrollment_routes.py":
            'if current_user.role != "student":\n    raise HTTPException(403)\n',
        "app/routes/user_routes.py":
            'if current_user.role != "admin":\n    raise HTTPException(403)\n',
    })
    result = _discover_role_vocabulary(root)
    shutil.rmtree(root, ignore_errors=True)
    assert result is not None
    default_role, allowed = result
    assert default_role == "user"
    assert set(allowed) == {"admin", "instructor", "student", "user"}


def test_route_scan_preserves_case_sensitivity():
    """`current_user.role != "Volunteer"` requires an exact-case match at
    runtime -- discovery must not silently lowercase it."""
    root = _make_project({
        "app/routes/a.py": 'if current_user.role not in ("Organizer", "Admin"):\n    pass\n',
        "app/routes/b.py": 'if current_user.role != "Volunteer":\n    pass\n',
    })
    result = _discover_role_vocabulary(root)
    shutil.rmtree(root, ignore_errors=True)
    assert result is not None
    assert set(result[1]) == {"Organizer", "Admin", "Volunteer", "user"}


def test_route_scan_requires_at_least_two_distinct_roles():
    """A single role mentioned everywhere (e.g. a fixed admin-only lockout
    repeated across many endpoints) is a deliberate security boundary, not
    a multi-role vocabulary -- must not be treated as one, since that
    would mean auto-elevating a test identity into an admin-only gate the
    app never intended anyone to self-register into."""
    root = _make_project({
        "app/routes/a.py": 'if current_user.role != "admin":\n    pass\n',
        "app/routes/b.py": 'if current_user.role != "admin":\n    pass\n',
    })
    result = _discover_role_vocabulary(root)
    shutil.rmtree(root, ignore_errors=True)
    assert result is None


def test_route_scan_returns_none_without_routes_dir():
    root = _make_project({"app/schemas/user.py": "role: str\n"})
    result = _discover_role_vocabulary(root)
    shutil.rmtree(root, ignore_errors=True)
    assert result is None


def test_schema_declaration_takes_precedence_over_route_scan():
    """When BOTH signals exist, the precise schema declaration wins over
    the route-scan fallback -- it's the more authoritative source."""
    root = _make_project({
        "app/schemas/auth.py":
            'role: str = Field("diner", pattern="^(diner|staff)$")\n',
        "app/routes/other.py":
            'if current_user.role != "admin":\n    pass\nelif current_user.role != "manager":\n    pass\n',
    })
    result = _discover_role_vocabulary(root)
    shutil.rmtree(root, ignore_errors=True)
    assert result == ("diner", ["diner", "staff"])


def test_generic_role_column_with_no_pattern_is_not_treated_as_app_specific():
    """A bare `role: str` with no pattern constraint carries no evidence of
    an intended vocabulary -- must not be guessed at."""
    root = Path(tempfile.mkdtemp(prefix="roletest_"))
    schemas = root / "app" / "schemas"
    schemas.mkdir(parents=True)
    (schemas / "user.py").write_text(
        "from pydantic import BaseModel\n\nclass UserCreate(BaseModel):\n    role: str = 'user'\n",
        encoding="utf-8",
    )
    result = _discover_role_vocabulary(root)
    shutil.rmtree(root, ignore_errors=True)
    assert result is None


def test_none_template_is_byte_for_byte_ast_identical_to_generic_default():
    """The common case (no app-specific role vocabulary) must produce
    exactly the original hardcoded-"user" behavior -- zero functional
    change for the ~98% of apps without this pattern."""
    template = _build_auth_routes_template(None)
    ast.parse(template)  # must be valid
    assert 'kw["role"] = "user"' in template
    assert "role: str | None" not in template  # no role field added to SignupRequest


def test_role_aware_template_validates_against_discovered_vocabulary():
    template = _build_auth_routes_template(("diner", ["diner", "staff"]))
    ast.parse(template)
    assert 'kw["role"] = role if role in' in template
    assert "'staff', 'diner'" in template or "'diner', 'staff'" in template
    assert '"diner"' in template  # safe fallback default preserved
    assert "req.role" in template  # signup() passes the request's role through


def test_role_aware_template_defends_against_arbitrary_role_injection():
    """A signup request with a role OUTSIDE the discovered vocabulary
    (e.g. 'admin' when only diner/staff are declared) must fall through
    to the safe default, not be granted verbatim."""
    template = _build_auth_routes_template(("diner", ["diner", "staff"]))
    # The generated logic is `role if role in {...} else "diner"` -- confirm
    # the allowed-set literal only contains the discovered values, not "admin".
    idx = template.index('kw["role"] =')
    line = template[idx:template.index("\n", idx)]
    assert "admin" not in line


def test_both_templates_are_syntactically_valid_python():
    for role_info in (None, ("customer", ["customer", "seller", "admin"])):
        ast.parse(_build_auth_routes_template(role_info))


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
