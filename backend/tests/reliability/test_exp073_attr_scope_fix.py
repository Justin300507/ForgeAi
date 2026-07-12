"""
Exp073 (Deterministic Attribute Rewrite Scope Fix): regression tests for
the AST-scoped rewrite of `_patch_attr_access_mismatches`
(app/services/deterministic_patcher.py).

Root cause (Exp072, confirmed live 2026-07-12): the function detected a
field mismatch per-model-class correctly, but applied its fix via a
file-wide `re.sub()` on every `.bad_attr` occurrence in the route file --
with no check that the specific attribute access actually belonged to an
instance of the mismatched class. Since the synonym-map keys are common
English words (display_name, status, title, description, ...), any OTHER,
genuinely correct object in the same file that happened to use the same
attribute name got silently corrupted alongside the real fix. Confirmed
live twice, independently, in the SAME canary run: a correctly-injected
`auth_routes.py`'s `req.display_name` (a `SignupRequest` field, valid) was
rewritten to `req.username` because the file's *User* model was separately
missing a `display_name` column -- `req` was never a `User` instance.

Fix: only rewrite an attribute access when the object is PROVABLY an
instance of the mismatched model class within its own function scope
(constructor call, ORM query result, typed parameter, or bare
`ClassName.attr`), via AST -- never a blanket file-wide substitution.

Run directly: python tests/reliability/test_exp073_attr_scope_fix.py
"""
import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import (
    _patch_attr_access_mismatches,
    _build_auth_routes_template,
)


def _proj(tmp_path):
    p = Path(tmp_path)
    (p / "app" / "routes").mkdir(parents=True, exist_ok=True)
    (p / "app" / "models").mkdir(parents=True, exist_ok=True)
    return p


USER_MODEL_EMAIL_ONLY = '''\
from app.database import Base
from sqlalchemy import Column, Integer, String

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String)
'''

USER_MODEL_USERNAME_ONLY = '''\
from app.database import Base
from sqlalchemy import Column, Integer, String

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String)
    password_hash = Column(String)
'''


# ── 1. Correct mismatch is still fixed ──────────────────────────────────────

def test_correct_mismatch_via_typed_param_is_still_fixed(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "models" / "user.py").write_text(USER_MODEL_EMAIL_ONLY, encoding="utf-8")
    route = p / "app" / "routes" / "user_routes.py"
    route.write_text(
        "from app.models.user import User\n\n"
        "@router.get('/me')\n"
        "def get_me(current_user: User = Depends(get_current_user)):\n"
        "    return {'name': current_user.username}\n",
        encoding="utf-8",
    )
    n = _patch_attr_access_mismatches(p)
    out = route.read_text(encoding="utf-8")
    assert n == 1
    assert ".username" not in out
    assert "current_user.email" in out
    ast.parse(out)


def test_correct_mismatch_via_orm_query_result_is_fixed(tmp_path):
    """Object typed by inference from `db.query(User)...first()`, not a
    function-parameter annotation -- the other primary real-world shape."""
    p = _proj(tmp_path)
    (p / "app" / "models" / "user.py").write_text(USER_MODEL_EMAIL_ONLY, encoding="utf-8")
    route = p / "app" / "routes" / "user_routes.py"
    route.write_text(
        "from app.models.user import User\n\n"
        "def get_profile(db, user_id: int):\n"
        "    target = db.query(User).filter(User.id == user_id).first()\n"
        "    return {'name': target.username}\n",
        encoding="utf-8",
    )
    n = _patch_attr_access_mismatches(p)
    out = route.read_text(encoding="utf-8")
    assert n == 1
    assert "target.email" in out
    assert ".username" not in out
    ast.parse(out)


def test_correct_mismatch_via_constructor_call_is_fixed(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "models" / "user.py").write_text(USER_MODEL_EMAIL_ONLY, encoding="utf-8")
    route = p / "app" / "routes" / "user_routes.py"
    route.write_text(
        "from app.models.user import User\n\n"
        "def make(email):\n"
        "    u = User(email=email)\n"
        "    return {'name': u.username}\n",
        encoding="utf-8",
    )
    n = _patch_attr_access_mismatches(p)
    out = route.read_text(encoding="utf-8")
    assert n == 1
    assert "u.email" in out


# ── 2. Unrelated object in the same file is left alone ─────────────────────

def test_unrelated_untyped_object_not_touched(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "models" / "user.py").write_text(USER_MODEL_EMAIL_ONLY, encoding="utf-8")
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
    assert "other_obj.username" in out  # left untouched -- never proven a User


def test_unrelated_object_typed_as_a_different_model_not_touched(tmp_path):
    """The Exp072 shape: `req` is a Pydantic schema instance (never typed as
    a model class at all), sitting in the same file as a User mismatch."""
    p = _proj(tmp_path)
    (p / "app" / "models" / "user.py").write_text(USER_MODEL_USERNAME_ONLY, encoding="utf-8")
    route = p / "app" / "routes" / "auth_routes.py"
    src = (
        "from app.models.user import User\n\n"
        "class SignupRequest:\n"
        "    email: str\n"
        "    display_name: str = ''\n\n"
        "def signup(req: SignupRequest, db):\n"
        "    user = User(username=req.email)\n"
        "    print(user.display_name)\n"  # bad_attr on a real User instance -> should rewrite
        "    return {'name': req.display_name}\n"  # req is SignupRequest, not User -> must NOT rewrite
    )
    route.write_text(src, encoding="utf-8")
    n = _patch_attr_access_mismatches(p)
    out = route.read_text(encoding="utf-8")
    assert n == 1
    assert "req.display_name" in out          # untouched
    assert "user.display_name" not in out     # rewritten to a valid synonym
    assert "user.username" in out


# ── 3. Multiple classes in one file, each independently scoped ─────────────

def test_multiple_classes_each_evaluated_independently(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "models" / "user.py").write_text(USER_MODEL_EMAIL_ONLY, encoding="utf-8")
    (p / "app" / "models" / "post.py").write_text(
        "from app.database import Base\n"
        "from sqlalchemy import Column, Integer, String\n\n"
        "class Post(Base):\n"
        "    __tablename__ = 'posts'\n"
        "    id = Column(Integer, primary_key=True)\n"
        "    body = Column(String)\n",
        encoding="utf-8",
    )
    route = p / "app" / "routes" / "combined_routes.py"
    route.write_text(
        "from app.models.user import User\n"
        "from app.models.post import Post\n\n"
        "def combined(db):\n"
        "    author: User = db.query(User).first()\n"
        "    entry: Post = db.query(Post).first()\n"
        "    return {'name': author.username, 'text': entry.content}\n",
        encoding="utf-8",
    )
    n = _patch_attr_access_mismatches(p)
    out = route.read_text(encoding="utf-8")
    assert n == 1
    assert "author.email" in out
    assert "entry.body" in out
    ast.parse(out)


# ── 4. Repeated attribute name on two instances of the SAME class ──────────

def test_repeated_attribute_name_on_two_instances_of_same_class_both_fixed(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "models" / "user.py").write_text(USER_MODEL_EMAIL_ONLY, encoding="utf-8")
    route = p / "app" / "routes" / "user_routes.py"
    route.write_text(
        "from app.models.user import User\n\n"
        "def compare(db, a_id, b_id):\n"
        "    a: User = db.query(User).filter(User.id == a_id).first()\n"
        "    b: User = db.query(User).filter(User.id == b_id).first()\n"
        "    return a.username == b.username\n",
        encoding="utf-8",
    )
    n = _patch_attr_access_mismatches(p)
    out = route.read_text(encoding="utf-8")
    assert n == 1
    assert out.count(".email") == 2
    assert ".username" not in out


# ── 5. Nested functions ─────────────────────────────────────────────────────

def test_nested_function_own_typed_variable_is_fixed(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "models" / "user.py").write_text(USER_MODEL_EMAIL_ONLY, encoding="utf-8")
    route = p / "app" / "routes" / "user_routes.py"
    route.write_text(
        "from app.models.user import User\n\n"
        "def outer(db, other_obj):\n"
        "    def inner(user_id):\n"
        "        u: User = db.query(User).filter(User.id == user_id).first()\n"
        "        return u.username\n"
        "    print(other_obj.username)\n"
        "    return inner\n",
        encoding="utf-8",
    )
    n = _patch_attr_access_mismatches(p)
    out = route.read_text(encoding="utf-8")
    assert n == 1
    assert "u.email" in out
    assert "other_obj.username" in out  # outer scope's untyped name untouched


# ── 6. Auth template regression (Exp072's exact corruption shape) ──────────

def test_auth_template_regression_req_display_name_survives_username_mismatch(tmp_path):
    """Reproduces Exp072 exactly: the REAL generated auth_routes.py template
    (via _build_auth_routes_template) alongside a User model that has
    `username` but no `display_name` column. Before Exp073 this rewrote the
    template's own `req.display_name` (a valid SignupRequest field) to
    `req.username`, breaking a correctly-injected file. Must now survive
    untouched -- `req` is never provably typed as `User`."""
    p = _proj(tmp_path)
    (p / "app" / "models" / "user.py").write_text(USER_MODEL_USERNAME_ONLY, encoding="utf-8")
    route = p / "app" / "routes" / "auth_routes.py"
    route.write_text(_build_auth_routes_template(None), encoding="utf-8")

    before = route.read_text(encoding="utf-8")
    assert "req.display_name" in before  # sanity: template really has this

    _patch_attr_access_mismatches(p)
    after = route.read_text(encoding="utf-8")
    assert "req.display_name" in after
    assert "req.username" not in after
    ast.parse(after)


# ── 7. Idempotence / no-op cases (regression replay) ────────────────────────

def test_idempotent_on_already_fixed_file(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "models" / "user.py").write_text(USER_MODEL_EMAIL_ONLY, encoding="utf-8")
    route = p / "app" / "routes" / "user_routes.py"
    route.write_text(
        "from app.models.user import User\n\n"
        "def get_me(current_user: User = Depends(get_current_user)):\n"
        "    return {'name': current_user.username}\n",
        encoding="utf-8",
    )
    _patch_attr_access_mismatches(p)
    once = route.read_text(encoding="utf-8")
    n2 = _patch_attr_access_mismatches(p)
    assert n2 == 0
    assert route.read_text(encoding="utf-8") == once


def test_noop_when_attribute_already_valid_on_model(tmp_path):
    p = _proj(tmp_path)
    model = USER_MODEL_EMAIL_ONLY.replace(
        "email = Column(String)", "email = Column(String)\n    username = Column(String)"
    )
    (p / "app" / "models" / "user.py").write_text(model, encoding="utf-8")
    route = p / "app" / "routes" / "user_routes.py"
    src = (
        "from app.models.user import User\n\n"
        "def get_me(current_user: User = Depends(get_current_user)):\n"
        "    return {'name': current_user.username}\n"
    )
    route.write_text(src, encoding="utf-8")
    n = _patch_attr_access_mismatches(p)
    assert n == 0
    assert route.read_text(encoding="utf-8") == src


def test_untyped_variable_with_no_evidence_is_never_rewritten(tmp_path):
    """No constructor call, no ORM query, no annotation -- there's simply no
    proof `obj` is a User instance, so it must never be touched even though
    the file mentions the class name elsewhere."""
    p = _proj(tmp_path)
    (p / "app" / "models" / "user.py").write_text(USER_MODEL_EMAIL_ONLY, encoding="utf-8")
    route = p / "app" / "routes" / "user_routes.py"
    src = (
        "from app.models.user import User\n\n"
        "def handler(obj):\n"
        "    return obj.username\n"
    )
    route.write_text(src, encoding="utf-8")
    n = _patch_attr_access_mismatches(p)
    assert n == 0
    assert route.read_text(encoding="utf-8") == src


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
