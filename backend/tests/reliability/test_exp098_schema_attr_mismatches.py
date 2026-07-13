"""
Verifies Experiment 098: extending _patch_attr_access_mismatches() to
also resolve Pydantic schema-typed parameters (not just SQLAlchemy
models), plus the curated "name"/"password" credential-field additions
to _FIELD_SYNONYMS_PATCHER.

Root cause (Exp097): seed_routes.py and route handlers construct
demo/real User rows using guessed field names ("name" instead of
"username"/"display_name", "password" instead of "password_hash"/
"hashed_password") with no visibility into what the model/schema
actually declared -- and the existing patcher could never catch the
schema-side case at all, since it only ever tracked SQLAlchemy models.

A first design (mechanical reciprocal scan of the whole
_FIELD_SYNONYMS_PATCHER dict) was tried and reverted after a
full-corpus replay against gym_tracker found it produces a wrong fix
(`tag_in.name` -> `tag_in.description` instead of the correct
`tag_in.title`, purely due to dict declaration order) -- replaced with
curated, explicit "name"/"password" keys scoped only to the
identity/credential synonyms they were meant to cover.

Run directly: python tests/reliability/test_exp098_schema_attr_mismatches.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import (
    _patch_attr_access_mismatches, _collect_schema_cols, _FIELD_SYNONYMS_PATCHER,
)


def _make_project(models_src: str, schemas_src: str, routes_src: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix="exp098_test_"))
    (root / "app" / "models").mkdir(parents=True)
    (root / "app" / "schemas").mkdir(parents=True)
    (root / "app" / "routes").mkdir(parents=True)
    (root / "app" / "models" / "users.py").write_text(models_src, encoding="utf-8")
    (root / "app" / "schemas" / "user.py").write_text(schemas_src, encoding="utf-8")
    (root / "app" / "routes" / "seed_routes.py").write_text(routes_src, encoding="utf-8")
    return root


_MODELS_USER_DISPLAY_NAME = """
from sqlalchemy import Column, Integer, String
from app.database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String)
    display_name = Column(String)
    hashed_password = Column(String)
"""

_SCHEMAS_USER_HASHED_PASSWORD = """
from pydantic import BaseModel

class UserCreate(BaseModel):
    email: str
    hashed_password: str
"""


# ── SQLAlchemy only (Exp097's confirmed User.name shape) ───────────────

def test_sqlalchemy_only_bare_class_attribute_fixed():
    routes = """
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.users import User

seed_router = APIRouter()

@seed_router.post('/seed')
def seed_data(db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.name == "demo").first()
    return {"seeded": True}
"""
    root = _make_project(_MODELS_USER_DISPLAY_NAME, _SCHEMAS_USER_HASHED_PASSWORD, routes)
    try:
        n = _patch_attr_access_mismatches(root)
        after = (root / "app" / "routes" / "seed_routes.py").read_text(encoding="utf-8")
        assert n == 1
        assert "User.display_name" in after
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── Pydantic only (Exp097's confirmed UserCreate.password shape) ──────

def test_pydantic_only_instance_attribute_fixed():
    routes = """
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas.user import UserCreate
from app.utils.auth import get_password_hash

seed_router = APIRouter()

@seed_router.post('/seed')
def seed_data(db: Session = Depends(get_db)):
    demo = UserCreate(email="demo@test.com", hashed_password="x")
    value = get_password_hash(demo.password)
    return {"seeded": True, "v": value}
"""
    root = _make_project(_MODELS_USER_DISPLAY_NAME, _SCHEMAS_USER_HASHED_PASSWORD, routes)
    try:
        n = _patch_attr_access_mismatches(root)
        after = (root / "app" / "routes" / "seed_routes.py").read_text(encoding="utf-8")
        assert n == 1
        assert "demo.hashed_password" in after
        assert "demo.password)" not in after
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── Mixed types: one function touches BOTH a model instance and a ─────
# ── schema instance -- each must resolve independently, no cross-talk ─

def test_mixed_model_and_schema_types_each_resolved_independently():
    routes = """
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.users import User
from app.schemas.user import UserCreate
from app.utils.auth import get_password_hash

seed_router = APIRouter()

@seed_router.post('/seed')
def seed_data(db: Session = Depends(get_db)):
    demo = UserCreate(email="demo@test.com", hashed_password="x")
    user = User(email=demo.email, hashed_password=get_password_hash(demo.password))
    db.add(user)
    db.commit()
    existing = db.query(User).filter(User.name == "demo").first()
    return {"seeded": True}
"""
    root = _make_project(_MODELS_USER_DISPLAY_NAME, _SCHEMAS_USER_HASHED_PASSWORD, routes)
    try:
        n = _patch_attr_access_mismatches(root)
        after = (root / "app" / "routes" / "seed_routes.py").read_text(encoding="utf-8")
        assert n == 1
        # Pydantic-typed `demo` fixed against schema_cols
        assert "demo.hashed_password" in after
        assert "demo.password)" not in after
        # SQLAlchemy-typed `User` fixed against model_cols, independently
        assert "User.display_name" in after
        assert "User.name ==" not in after
        # The already-correct constructor kwarg (User(..., hashed_password=...))
        # was never touched by either fix -- confirms no cross-contamination.
        assert "hashed_password=get_password_hash(" in after
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── Existing behavior: byte-identical to pre-Exp098 when no schema ────
# ── param is involved and the bad_attr isn't "name"/"password" ────────

def test_existing_username_synonym_behavior_unchanged():
    models = """
from sqlalchemy import Column, Integer, String
from app.database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String)
"""
    routes = """
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.users import User

seed_router = APIRouter()

@seed_router.post('/seed')
def seed_data(db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.username == "demo").first()
    return {"seeded": True}
"""
    root = _make_project(models, "", routes)
    try:
        n = _patch_attr_access_mismatches(root)
        after = (root / "app" / "routes" / "seed_routes.py").read_text(encoding="utf-8")
        assert n == 1
        # Pre-existing "username" -> "email" fallback, unchanged by Exp098
        assert "User.email" in after
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_curated_name_key_does_not_reach_unrelated_description_title_cluster():
    """
    Regression guard for the specific bug found and reverted during this
    experiment's own implementation: a mechanical reciprocal scan of
    _FIELD_SYNONYMS_PATCHER made "name" resolve through "description"
    before "title" (wrong -- confirmed live via gym_tracker's
    `tag_in.name` -> `tag_in.description` instead of `.title`). The
    curated "name" key must never include "description"/"title"/"label".
    """
    assert "description" not in _FIELD_SYNONYMS_PATCHER["name"]
    assert "title" not in _FIELD_SYNONYMS_PATCHER["name"]
    assert "label" not in _FIELD_SYNONYMS_PATCHER["name"]
    assert _FIELD_SYNONYMS_PATCHER["name"] == ["username", "full_name", "display_name"]


# ── Unrelated dictionaries: a plain dict variable must never be ───────
# ── mistaken for a typed model/schema instance ─────────────────────────

def test_unrelated_plain_dict_not_touched():
    routes = """
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.users import User

seed_router = APIRouter()

@seed_router.post('/seed')
def seed_data(db: Session = Depends(get_db)):
    config = {"name": "demo-app", "password": "unused"}
    label = config.get("name")
    existing = db.query(User).filter(User.name == "demo").first()
    return {"seeded": True, "label": label}
"""
    root = _make_project(_MODELS_USER_DISPLAY_NAME, "", routes)
    try:
        n = _patch_attr_access_mismatches(root)
        after = (root / "app" / "routes" / "seed_routes.py").read_text(encoding="utf-8")
        assert n == 1
        # The untyped dict literal and its .get("name") call are untouched --
        # config is never resolved to any known model/schema class.
        assert 'config = {"name": "demo-app", "password": "unused"}' in after
        assert 'config.get("name")' in after
        # Only the genuinely typed User.name access was fixed.
        assert "User.display_name" in after
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_schema_cols_empty_when_schemas_dir_missing():
    assert _collect_schema_cols(Path(tempfile.mkdtemp()) / "nonexistent") == {}


if __name__ == "__main__":
    import traceback
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            print(f"FAIL: {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR: {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
