"""
Exp052 (Deterministic Repair Test Coverage Initiative), Priority 1/3:
regression tests for the SQL/constructor-kwarg cleanup family and the
auth-injection family in deterministic_patcher.py.

Real bugs reproduced, quoted directly from the functions' own docstrings
(these are documented live incidents, not invented scenarios):
- _patch_star_dict_extra_fields / _patch_unsafe_model_hasattr_filter:
  forge_expense_tracker's POST /expenses 500'd on every request because
  a read-only `category` @property passed hasattr() but has no setter.
- _patch_star_dict_extra_fields's kwarg-collision case: habit_forge's
  POST /habits 500'd because HabitCreate.user_id (client-suppliable)
  collided with the route's own explicit user_id=current_user.id kwarg.
- patch_ensure_auth_pages: a documented total-outage infinite redirect
  loop when no LoginPage.jsx/RegisterPage.jsx were ever generated.

Run directly: python tests/reliability/test_sql_constructor_and_auth_repairs.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import (
    _patch_star_dict_extra_fields,
    _patch_filtered_ctor_kwarg_collision,
    _patch_unsafe_model_hasattr_filter,
    _patch_attr_access_mismatches,
    _patch_auth_utils,
    _patch_auth_routes,
    patch_ensure_auth_pages,
    _patch_seed_robustness,
)


def _proj(tmp_path):
    p = Path(tmp_path)
    (p / "app" / "routes").mkdir(parents=True, exist_ok=True)
    (p / "app" / "models").mkdir(parents=True, exist_ok=True)
    (p / "app" / "utils").mkdir(parents=True, exist_ok=True)
    (p / "app" / "schemas").mkdir(parents=True, exist_ok=True)
    return p


# ── _patch_star_dict_extra_fields ───────────────────────────────────────────

MODEL_EXPENSE = '''\
from app.database import Base
from sqlalchemy import Column, Integer, String, Float

class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True)
    amount = Column(Float)
    user_id = Column(Integer)

    @property
    def category(self):
        return self.category_rel.name if self.category_rel else None
'''

ROUTE_STAR_DICT = '''\
from app.models.expense import Expense

@router.post("/expenses")
def create_expense(exp_in: ExpenseCreate, db: Session = Depends(get_db)):
    exp = Expense(**exp_in.dict(), user_id=current_user.id)
    db.add(exp)
    db.commit()
    return exp
'''


def test_star_dict_extra_fields_filters_against_table_columns_not_hasattr(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "models" / "expense.py").write_text(MODEL_EXPENSE, encoding="utf-8")
    route = p / "app" / "routes" / "expense_routes.py"
    route.write_text(ROUTE_STAR_DICT, encoding="utf-8")

    n = _patch_star_dict_extra_fields(p)
    assert n == 1
    out = route.read_text(encoding="utf-8")
    assert "Expense.__table__.columns.keys()" in out
    # the explicit trailing kwarg must be excluded from the filtered dict
    assert "and k not in {'user_id'}" in out
    assert "Expense(**exp_in.dict()" not in out  # original unfiltered call gone


def test_star_dict_extra_fields_noop_when_no_orm_class_referenced(tmp_path):
    p = _proj(tmp_path)
    route = p / "app" / "routes" / "misc.py"
    src = "def f():\n    return SomeNonOrmClass(**data.dict())\n"
    route.write_text(src, encoding="utf-8")
    n = _patch_star_dict_extra_fields(p)
    assert n == 0
    assert route.read_text(encoding="utf-8") == src


def test_star_dict_extra_fields_idempotent(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "models" / "expense.py").write_text(MODEL_EXPENSE, encoding="utf-8")
    route = p / "app" / "routes" / "expense_routes.py"
    route.write_text(ROUTE_STAR_DICT, encoding="utf-8")
    _patch_star_dict_extra_fields(p)
    once = route.read_text(encoding="utf-8")
    n2 = _patch_star_dict_extra_fields(p)
    assert n2 == 0
    assert route.read_text(encoding="utf-8") == once


def test_star_dict_extra_fields_multiple_occurrences_in_one_file(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "models" / "expense.py").write_text(MODEL_EXPENSE, encoding="utf-8")
    route = p / "app" / "routes" / "expense_routes.py"
    two_calls = ROUTE_STAR_DICT + '''
@router.post("/expenses/bulk")
def create_expense2(exp_in: ExpenseCreate, db: Session = Depends(get_db)):
    exp = Expense(**exp_in.dict(), user_id=current_user.id)
    db.add(exp)
    return exp
'''
    route.write_text(two_calls, encoding="utf-8")
    n = _patch_star_dict_extra_fields(p)
    assert n == 1  # counts FILES patched, not occurrences
    out = route.read_text(encoding="utf-8")
    assert out.count("__table__.columns.keys()") == 2


# ── _patch_filtered_ctor_kwarg_collision ────────────────────────────────────

ALREADY_FILTERED_MISSING_EXCLUSION = '''\
@router.post("/habits")
def create_habit(habit_in: HabitCreate, db: Session = Depends(get_db)):
    habit = Habit(**{k: v for k, v in habit_in.dict().items() if k in Habit.__table__.columns.keys()}, user_id=current_user.id)
    db.add(habit)
    db.commit()
    return habit
'''


def test_filtered_ctor_kwarg_collision_adds_missing_exclusion(tmp_path):
    p = _proj(tmp_path)
    route = p / "app" / "routes" / "habit_routes.py"
    route.write_text(ALREADY_FILTERED_MISSING_EXCLUSION, encoding="utf-8")
    n = _patch_filtered_ctor_kwarg_collision(p)
    assert n == 1
    out = route.read_text(encoding="utf-8")
    assert "and k not in {'user_id'}" in out


def test_filtered_ctor_kwarg_collision_noop_when_already_has_exclusion(tmp_path):
    p = _proj(tmp_path)
    route = p / "app" / "routes" / "habit_routes.py"
    already_fixed = ALREADY_FILTERED_MISSING_EXCLUSION.replace(
        "if k in Habit.__table__.columns.keys()}",
        "if k in Habit.__table__.columns.keys() and k not in {'user_id'}}",
    )
    route.write_text(already_fixed, encoding="utf-8")
    n = _patch_filtered_ctor_kwarg_collision(p)
    assert n == 0
    assert route.read_text(encoding="utf-8") == already_fixed


def test_filtered_ctor_kwarg_collision_idempotent(tmp_path):
    p = _proj(tmp_path)
    route = p / "app" / "routes" / "habit_routes.py"
    route.write_text(ALREADY_FILTERED_MISSING_EXCLUSION, encoding="utf-8")
    _patch_filtered_ctor_kwarg_collision(p)
    once = route.read_text(encoding="utf-8")
    n2 = _patch_filtered_ctor_kwarg_collision(p)
    assert n2 == 0
    assert route.read_text(encoding="utf-8") == once


# ForgeBench v1.0, employee_directory, 2026-07-28: a trailing kwarg whose
# value is itself a call with its own parens (`date.today()`) made the old
# `((?:[^)]*)?)\)`-style tail match stop at the FIRST close-paren it saw --
# `active=False` (after `date.today()`) was silently never reached, so
# `active` never got added to the exclusion set and POST /employees 500'd
# on every request with "got multiple values for keyword argument 'active'".
NESTED_PAREN_KWARG_COLLISION = '''\
@router.post("/employees")
def create_employee(employee_in: EmployeeCreate, db: Session = Depends(get_db)):
    employee = Employee(**{k: v for k, v in employee_in.dict().items() if k in Employee.__table__.columns.keys() and k not in {'hire_date'}}, hire_date=date.today(), active=False)
    db.add(employee)
    db.commit()
    return employee
'''


def test_filtered_ctor_kwarg_collision_handles_trailing_kwarg_with_nested_parens(tmp_path):
    p = _proj(tmp_path)
    route = p / "app" / "routes" / "employee_routes.py"
    route.write_text(NESTED_PAREN_KWARG_COLLISION, encoding="utf-8")
    n = _patch_filtered_ctor_kwarg_collision(p)
    assert n == 1
    out = route.read_text(encoding="utf-8")
    assert "'active'" in out and "'hire_date'" in out
    assert "date.today()" in out  # the nested call itself must survive intact
    ctor_line = next(l for l in out.splitlines() if l.strip().startswith("employee = Employee("))
    assert ctor_line.rstrip().endswith("active=False)")  # trailing kwargs preserved verbatim, call properly closed


def test_filtered_ctor_kwarg_collision_nested_parens_idempotent(tmp_path):
    p = _proj(tmp_path)
    route = p / "app" / "routes" / "employee_routes.py"
    route.write_text(NESTED_PAREN_KWARG_COLLISION, encoding="utf-8")
    _patch_filtered_ctor_kwarg_collision(p)
    once = route.read_text(encoding="utf-8")
    n2 = _patch_filtered_ctor_kwarg_collision(p)
    assert n2 == 0
    assert route.read_text(encoding="utf-8") == once


# ── _patch_unsafe_model_hasattr_filter ──────────────────────────────────────

UNSAFE_HASATTR_ROUTE = '''\
@router.post("/expenses")
def create_expense(exp_in: ExpenseCreate, db: Session = Depends(get_db)):
    exp = Expense(**{k: v for k, v in exp_in.dict().items() if hasattr(Expense, k)})
    db.add(exp)
    return exp
'''


def test_unsafe_hasattr_filter_rewritten_to_table_columns(tmp_path):
    p = _proj(tmp_path)
    route = p / "app" / "routes" / "expense_routes.py"
    route.write_text(UNSAFE_HASATTR_ROUTE, encoding="utf-8")
    n = _patch_unsafe_model_hasattr_filter(p)
    assert n == 1
    out = route.read_text(encoding="utf-8")
    assert "hasattr(Expense, k)" not in out
    assert "k in Expense.__table__.columns.keys()" in out


def test_unsafe_hasattr_filter_noop_on_unrelated_hasattr_usage(tmp_path):
    p = _proj(tmp_path)
    route = p / "app" / "routes" / "misc.py"
    src = "def f(obj):\n    return hasattr(obj, 'foo')\n"  # not `hasattr(Class, k)` shape
    route.write_text(src, encoding="utf-8")
    n = _patch_unsafe_model_hasattr_filter(p)
    assert n == 0
    assert route.read_text(encoding="utf-8") == src


def test_unsafe_hasattr_filter_idempotent(tmp_path):
    p = _proj(tmp_path)
    route = p / "app" / "routes" / "expense_routes.py"
    route.write_text(UNSAFE_HASATTR_ROUTE, encoding="utf-8")
    _patch_unsafe_model_hasattr_filter(p)
    once = route.read_text(encoding="utf-8")
    n2 = _patch_unsafe_model_hasattr_filter(p)
    assert n2 == 0
    assert route.read_text(encoding="utf-8") == once


def test_unsafe_hasattr_filter_multiple_occurrences(tmp_path):
    p = _proj(tmp_path)
    route = p / "app" / "routes" / "expense_routes.py"
    two = UNSAFE_HASATTR_ROUTE + "\n" + UNSAFE_HASATTR_ROUTE.replace("Expense", "Budget")
    route.write_text(two, encoding="utf-8")
    n = _patch_unsafe_model_hasattr_filter(p)
    assert n == 1  # one file
    out = route.read_text(encoding="utf-8")
    assert "hasattr(" not in out
    assert out.count("__table__.columns.keys()") == 2


# ── _patch_attr_access_mismatches ───────────────────────────────────────────

MODEL_USER_EMAIL_ONLY = '''\
from app.database import Base
from sqlalchemy import Column, Integer, String

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String)
'''

ROUTE_BAD_USERNAME_ACCESS = '''\
from app.models.user import User

@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {"name": current_user.username}
'''


def test_attr_access_mismatch_rewrites_to_synonym_that_exists_on_model(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "models" / "user.py").write_text(MODEL_USER_EMAIL_ONLY, encoding="utf-8")
    route = p / "app" / "routes" / "user_routes.py"
    route.write_text(ROUTE_BAD_USERNAME_ACCESS, encoding="utf-8")
    n = _patch_attr_access_mismatches(p)
    assert n == 1
    out = route.read_text(encoding="utf-8")
    assert ".username" not in out
    assert ".email" in out


def test_attr_access_mismatch_noop_when_attr_exists_on_model(tmp_path):
    p = _proj(tmp_path)
    model = MODEL_USER_EMAIL_ONLY.replace(
        "email = Column(String)", "email = Column(String)\n    username = Column(String)"
    )
    (p / "app" / "models" / "user.py").write_text(model, encoding="utf-8")
    route = p / "app" / "routes" / "user_routes.py"
    route.write_text(ROUTE_BAD_USERNAME_ACCESS, encoding="utf-8")
    n = _patch_attr_access_mismatches(p)
    assert n == 0
    assert route.read_text(encoding="utf-8") == ROUTE_BAD_USERNAME_ACCESS


def test_attr_access_mismatch_idempotent(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "models" / "user.py").write_text(MODEL_USER_EMAIL_ONLY, encoding="utf-8")
    route = p / "app" / "routes" / "user_routes.py"
    route.write_text(ROUTE_BAD_USERNAME_ACCESS, encoding="utf-8")
    _patch_attr_access_mismatches(p)
    once = route.read_text(encoding="utf-8")
    n2 = _patch_attr_access_mismatches(p)
    assert n2 == 0
    assert route.read_text(encoding="utf-8") == once


def test_attr_access_mismatch_scoped_to_typed_object_not_file_wide(tmp_path):
    """
    Exp073: was documented in docs/REPAIR_DEBT.md (Exp051) and confirmed
    live via Exp072 (the `req.display_name` -> `req.username` auth-template
    corruption) as a whole-FILE regex, not class-qualified -- if a route
    file mentioned the class name ANYWHERE and also had a bare `.username`
    access belonging to a DIFFERENT, untyped object in the same file, it
    got rewritten too. Now AST-scoped: only `current_user` (typed `User` by
    its own parameter annotation) is rewritten; `other_obj` (no type
    annotation, so provably NOT known to be a `User` instance) is left
    completely alone.
    """
    p = _proj(tmp_path)
    (p / "app" / "models" / "user.py").write_text(MODEL_USER_EMAIL_ONLY, encoding="utf-8")
    route = p / "app" / "routes" / "user_routes.py"
    src = (
        "from app.models.user import User\n\n"
        "@router.get('/me')\n"
        "def get_me(current_user: User = Depends(get_current_user), other_obj=None):\n"
        "    print(other_obj.username)\n"
        "    return {'name': current_user.username}\n"
    )
    route.write_text(src, encoding="utf-8")
    _patch_attr_access_mismatches(p)
    out = route.read_text(encoding="utf-8")
    assert out.count(".email") == 1
    assert "current_user.email" in out
    assert "other_obj.username" in out


# ── _patch_auth_utils ────────────────────────────────────────────────────────

def test_auth_utils_injects_when_missing(tmp_path):
    p = _proj(tmp_path)
    auth_file = p / "app" / "utils" / "auth.py"
    assert not auth_file.exists()
    _patch_auth_utils(p)
    assert auth_file.exists()
    content = auth_file.read_text(encoding="utf-8")
    assert "get_current_user" in content
    assert "verify_password" in content


def test_auth_utils_injects_when_uses_passlib(tmp_path):
    p = _proj(tmp_path)
    auth_file = p / "app" / "utils" / "auth.py"
    auth_file.write_text("from passlib.context import CryptContext\n", encoding="utf-8")
    _patch_auth_utils(p)
    content = auth_file.read_text(encoding="utf-8")
    assert "passlib" not in content


def test_auth_utils_noop_when_already_good(tmp_path):
    p = _proj(tmp_path)
    auth_file = p / "app" / "utils" / "auth.py"
    _patch_auth_utils(p)  # first call writes the known-good template
    good = auth_file.read_text(encoding="utf-8")
    _patch_auth_utils(p)  # second call should be a true no-op
    assert auth_file.read_text(encoding="utf-8") == good


# ── _patch_auth_routes ────────────────────────────────────────────────────────

def test_auth_routes_skips_when_no_user_model(tmp_path):
    p = _proj(tmp_path)
    main_py = p / "app" / "main.py"
    main_py.write_text("app = FastAPI()\n", encoding="utf-8")
    _patch_auth_routes(p)
    assert not (p / "app" / "routes" / "auth_routes.py").exists()


def test_auth_routes_injects_when_user_model_present_and_missing(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "main.py").write_text("app = FastAPI()\n", encoding="utf-8")
    (p / "app" / "models" / "user.py").write_text(MODEL_USER_EMAIL_ONLY, encoding="utf-8")
    _patch_auth_routes(p)
    auth_routes = p / "app" / "routes" / "auth_routes.py"
    assert auth_routes.exists()
    assert "_read_password" in auth_routes.read_text(encoding="utf-8")


def test_auth_routes_reinjects_when_sentinel_missing_from_existing_file(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "main.py").write_text("app = FastAPI()\n", encoding="utf-8")
    (p / "app" / "models" / "user.py").write_text(MODEL_USER_EMAIL_ONLY, encoding="utf-8")
    routes_dir = p / "app" / "routes"
    (routes_dir / "auth_routes.py").write_text(
        "# LLM-authored auth routes, uses user.password directly\n", encoding="utf-8"
    )
    _patch_auth_routes(p)
    assert "_read_password" in (routes_dir / "auth_routes.py").read_text(encoding="utf-8")


def test_auth_routes_idempotent(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "main.py").write_text("app = FastAPI()\n", encoding="utf-8")
    (p / "app" / "models" / "user.py").write_text(MODEL_USER_EMAIL_ONLY, encoding="utf-8")
    _patch_auth_routes(p)
    once = (p / "app" / "routes" / "auth_routes.py").read_text(encoding="utf-8")
    _patch_auth_routes(p)
    assert (p / "app" / "routes" / "auth_routes.py").read_text(encoding="utf-8") == once


# ── patch_ensure_auth_pages ──────────────────────────────────────────────────

APP_JSX_WITH_LOGIN_NO_PAGES = '''\
import React from "react";
import { Routes, Route } from "react-router-dom";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/login" />} />
    </Routes>
  );
}
'''


def test_ensure_auth_pages_synthesizes_login_and_register_when_missing(tmp_path):
    p = _proj(tmp_path)
    (p / "src" / "pages").mkdir(parents=True)
    app_jsx = p / "src" / "App.jsx"
    app_jsx.write_text(APP_JSX_WITH_LOGIN_NO_PAGES, encoding="utf-8")
    n = patch_ensure_auth_pages(p)
    assert n == 2  # both pages added
    assert (p / "src" / "pages" / "LoginPage.jsx").exists()
    assert (p / "src" / "pages" / "RegisterPage.jsx").exists()
    updated = app_jsx.read_text(encoding="utf-8")
    assert "import LoginPage" in updated
    assert "import RegisterPage" in updated


def test_ensure_auth_pages_noop_when_no_login_reference(tmp_path):
    p = _proj(tmp_path)
    (p / "src" / "pages").mkdir(parents=True)
    app_jsx = p / "src" / "App.jsx"
    src = "export default function App() { return <div>hi</div>; }\n"
    app_jsx.write_text(src, encoding="utf-8")
    n = patch_ensure_auth_pages(p)
    assert n == 0
    assert app_jsx.read_text(encoding="utf-8") == src


def test_ensure_auth_pages_idempotent_confirmed_double_invocation_is_safe(tmp_path):
    """
    Exp051's REPAIR_DEBT.md Risk documented that this function is called
    TWICE per real pipeline run (once directly, once again inside
    run_frontend_patches) and inferred it was "likely harmless" without
    reading the body. This test actually proves it: call it twice back to
    back and confirm the second call is a true no-op (n == 0, byte-
    identical files), resolving that inferred risk with certainty.
    """
    p = _proj(tmp_path)
    (p / "src" / "pages").mkdir(parents=True)
    app_jsx = p / "src" / "App.jsx"
    app_jsx.write_text(APP_JSX_WITH_LOGIN_NO_PAGES, encoding="utf-8")

    n1 = patch_ensure_auth_pages(p)
    assert n1 == 2
    app_jsx_once = app_jsx.read_text(encoding="utf-8")
    login_once = (p / "src" / "pages" / "LoginPage.jsx").read_text(encoding="utf-8")

    n2 = patch_ensure_auth_pages(p)
    assert n2 == 0, "second call must add nothing new"
    assert app_jsx.read_text(encoding="utf-8") == app_jsx_once
    assert (p / "src" / "pages" / "LoginPage.jsx").read_text(encoding="utf-8") == login_once


def test_ensure_auth_pages_register_synthesized_even_if_not_referenced_yet(tmp_path):
    # Per the function's own comment: if Login is being synthesized from
    # scratch, Register must exist too because the Login template's own
    # "Sign up" link points to /register, regardless of whether the
    # ORIGINAL App.jsx ever mentioned /register.
    p = _proj(tmp_path)
    (p / "src" / "pages").mkdir(parents=True)
    app_jsx = p / "src" / "App.jsx"
    assert "/register" not in APP_JSX_WITH_LOGIN_NO_PAGES
    app_jsx.write_text(APP_JSX_WITH_LOGIN_NO_PAGES, encoding="utf-8")
    patch_ensure_auth_pages(p)
    assert (p / "src" / "pages" / "RegisterPage.jsx").exists()


# ── _patch_seed_robustness ───────────────────────────────────────────────────

SEED_ROUTES_UNWRAPPED = '''\
def _create_user(db, email):
    user = User(email=email)
    db.add(user)
    db.commit()
    return user


def _create_task(db, user, title):
    task = Task(title=title, user_id=user.id)
    db.add(task)
    db.commit()
    return task
'''


def test_seed_robustness_wraps_create_helpers_in_try_except(tmp_path):
    p = _proj(tmp_path)
    seed_file = p / "app" / "routes" / "seed_routes.py"
    seed_file.write_text(SEED_ROUTES_UNWRAPPED, encoding="utf-8")
    n = _patch_seed_robustness(p)
    assert n == 1
    out = seed_file.read_text(encoding="utf-8")
    assert out.count("try:") == 2
    assert out.count("db.rollback()") == 2


def test_seed_robustness_noop_when_already_wrapped(tmp_path):
    p = _proj(tmp_path)
    seed_file = p / "app" / "routes" / "seed_routes.py"
    seed_file.write_text(SEED_ROUTES_UNWRAPPED, encoding="utf-8")
    _patch_seed_robustness(p)
    once = seed_file.read_text(encoding="utf-8")
    n2 = _patch_seed_robustness(p)
    assert n2 == 0
    assert seed_file.read_text(encoding="utf-8") == once


def test_seed_robustness_guards_bare_index_zero_access(tmp_path):
    p = _proj(tmp_path)
    seed_file = p / "app" / "routes" / "seed_routes.py"
    src = (
        "def seed(db):\n"
        "    users = db.query(User).all()\n"
        "    first = users[0]\n"
        "    return first\n"
    )
    seed_file.write_text(src, encoding="utf-8")
    n = _patch_seed_robustness(p)
    assert n == 1
    out = seed_file.read_text(encoding="utf-8")
    assert "if not users:" in out


def test_seed_robustness_missing_file_is_safe_noop(tmp_path):
    p = _proj(tmp_path)
    n = _patch_seed_robustness(p)
    assert n == 0


if __name__ == "__main__":
    import tempfile as _tempfile
    import traceback

    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            with _tempfile.TemporaryDirectory() as td:
                t(Path(td))
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
