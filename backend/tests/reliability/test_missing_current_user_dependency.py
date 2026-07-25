"""
Regression tests for _patch_missing_current_user_dependency_for_ownership_insert
(app/services/deterministic_patcher.py) -- the companion to Exp092's
_patch_missing_ownership_assignment that covers the case where the
current_user dependency is missing from the handler signature entirely,
not just the body assignment.

Root cause (habit_tracker, 2026-07-25): an "ARCHITECTURE REPAIR" LLM
rewrite of habit_routes.py dropped Depends(get_current_user) from
create_habit() entirely -- even though the Tech Lead's own review had
flagged missing JWT auth on user-data endpoints as CRITICAL -- while
Habit.user_id is a NOT NULL FK. db.add()/db.commit() raised an
IntegrityError and the live CRUD journey's "Create entity" step 500'd on
every run. _patch_missing_ownership_assignment's own precondition (an
existing current_user parameter) meant it could never reach this shape.

Run directly: python tests/reliability/test_missing_current_user_dependency.py
"""
import ast
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import (
    _patch_missing_current_user_dependency_for_ownership_insert as _patch,
)


def _make_project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="curuserdep_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


_HABIT_MODEL = '''\
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Habit(Base):
    __tablename__ = "habits"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    frequency = Column(String(50), nullable=False)
'''

_NO_OWNERSHIP_MODEL = '''\
from sqlalchemy import Column, Integer, String
from app.database import Base

class Tag(Base):
    __tablename__ = "tags"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
'''


def test_injects_dependency_and_assignment_when_both_missing():
    # Exact live shape: habit_routes.py's create_habit() with no auth
    # dependency at all.
    route = '''\
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.habits import Habit
from app.schemas.habit import HabitCreate, HabitResponse

habit_router = APIRouter()

@habit_router.post("/habits", response_model=HabitResponse)
def create_habit(habit_in: HabitCreate, db: Session = Depends(get_db)):
    habit = Habit(**{k: v for k, v in habit_in.dict().items() if k in Habit.__table__.columns.keys()})
    db.add(habit)
    db.commit()
    db.refresh(habit)
    return habit
'''
    root = _make_project({"app/models/habits.py": _HABIT_MODEL, "app/routes/habit_routes.py": route})
    n = _patch(root)
    out = (root / "app" / "routes" / "habit_routes.py").read_text(encoding="utf-8")
    assert n == 1
    ast.parse(out)
    assert "current_user=Depends(get_current_user)" in out
    assert "habit.user_id = current_user.id" in out
    assert "from app.utils.auth import get_current_user" in out
    # Assignment must land before db.add(habit), not after.
    assert out.index("habit.user_id = current_user.id") < out.index("db.add(habit)")


def test_multiline_signature_still_patched_correctly():
    route = '''\
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.habits import Habit
from app.schemas.habit import HabitCreate

habit_router = APIRouter()

@habit_router.post("/habits")
def create_habit(
    habit_in: HabitCreate,
    db: Session = Depends(get_db),
):
    habit = Habit(**habit_in.dict())
    db.add(habit)
    db.commit()
    return habit
'''
    root = _make_project({"app/models/habits.py": _HABIT_MODEL, "app/routes/habit_routes.py": route})
    n = _patch(root)
    out = (root / "app" / "routes" / "habit_routes.py").read_text(encoding="utf-8")
    assert n == 1
    ast.parse(out)
    assert "current_user=Depends(get_current_user)" in out
    assert "habit.user_id = current_user.id" in out


def test_noop_when_current_user_already_present():
    # Owned entirely by the Exp092 companion patcher -- must not double-inject.
    route = '''\
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.habits import Habit
from app.schemas.habit import HabitCreate
from app.utils.auth import get_current_user

habit_router = APIRouter()

@habit_router.post("/habits")
def create_habit(habit_in: HabitCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    habit = Habit(**habit_in.dict())
    db.add(habit)
    db.commit()
    return habit
'''
    root = _make_project({"app/models/habits.py": _HABIT_MODEL, "app/routes/habit_routes.py": route})
    n = _patch(root)
    out = (root / "app" / "routes" / "habit_routes.py").read_text(encoding="utf-8")
    assert n == 0
    assert out == route


def test_noop_when_current_user_name_already_used_for_something_else():
    # Defense-in-depth: never shadow an existing, unrelated local name.
    route = '''\
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.habits import Habit
from app.schemas.habit import HabitCreate

habit_router = APIRouter()

@habit_router.post("/habits")
def create_habit(habit_in: HabitCreate, db: Session = Depends(get_db)):
    current_user = "unrelated local variable"
    habit = Habit(**habit_in.dict())
    db.add(habit)
    db.commit()
    return habit
'''
    root = _make_project({"app/models/habits.py": _HABIT_MODEL, "app/routes/habit_routes.py": route})
    n = _patch(root)
    out = (root / "app" / "routes" / "habit_routes.py").read_text(encoding="utf-8")
    assert n == 0
    assert out == route


def test_noop_when_ownership_already_assigned_via_kwarg():
    route = '''\
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.habits import Habit
from app.schemas.habit import HabitCreate

habit_router = APIRouter()

@habit_router.post("/habits")
def create_habit(habit_in: HabitCreate, user_id: int, db: Session = Depends(get_db)):
    habit = Habit(user_id=user_id, name=habit_in.name)
    db.add(habit)
    db.commit()
    return habit
'''
    root = _make_project({"app/models/habits.py": _HABIT_MODEL, "app/routes/habit_routes.py": route})
    n = _patch(root)
    out = (root / "app" / "routes" / "habit_routes.py").read_text(encoding="utf-8")
    assert n == 0
    assert out == route


def test_noop_when_no_db_add_call():
    route = '''\
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.habits import Habit
from app.schemas.habit import HabitCreate

habit_router = APIRouter()

@habit_router.post("/habits")
def create_habit(habit_in: HabitCreate, db: Session = Depends(get_db)):
    habit = Habit(name=habit_in.name)
    return habit
'''
    root = _make_project({"app/models/habits.py": _HABIT_MODEL, "app/routes/habit_routes.py": route})
    n = _patch(root)
    out = (root / "app" / "routes" / "habit_routes.py").read_text(encoding="utf-8")
    assert n == 0
    assert out == route


def test_noop_when_model_has_no_ownership_fk():
    route = '''\
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.tags import Tag
from app.schemas.tag import TagCreate

tag_router = APIRouter()

@tag_router.post("/tags")
def create_tag(tag_in: TagCreate, db: Session = Depends(get_db)):
    tag = Tag(name=tag_in.name)
    db.add(tag)
    db.commit()
    return tag
'''
    root = _make_project({"app/models/tags.py": _NO_OWNERSHIP_MODEL, "app/routes/tag_routes.py": route})
    n = _patch(root)
    out = (root / "app" / "routes" / "tag_routes.py").read_text(encoding="utf-8")
    assert n == 0
    assert out == route


def test_get_handler_not_touched():
    route = '''\
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.habits import Habit

habit_router = APIRouter()

@habit_router.get("/habits")
def list_habits(db: Session = Depends(get_db)):
    return db.query(Habit).all()
'''
    root = _make_project({"app/models/habits.py": _HABIT_MODEL, "app/routes/habit_routes.py": route})
    n = _patch(root)
    out = (root / "app" / "routes" / "habit_routes.py").read_text(encoding="utf-8")
    assert n == 0
    assert out == route


def test_idempotent_second_pass_is_noop():
    route = '''\
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.habits import Habit
from app.schemas.habit import HabitCreate

habit_router = APIRouter()

@habit_router.post("/habits")
def create_habit(habit_in: HabitCreate, db: Session = Depends(get_db)):
    habit = Habit(**habit_in.dict())
    db.add(habit)
    db.commit()
    return habit
'''
    root = _make_project({"app/models/habits.py": _HABIT_MODEL, "app/routes/habit_routes.py": route})
    _patch(root)
    first = (root / "app" / "routes" / "habit_routes.py").read_text(encoding="utf-8")
    n2 = _patch(root)
    second = (root / "app" / "routes" / "habit_routes.py").read_text(encoding="utf-8")
    assert n2 == 0
    assert first == second


def test_no_op_when_no_routes_or_models_dir():
    root = Path(tempfile.mkdtemp(prefix="curuserdep_"))
    assert _patch(root) == 0


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
