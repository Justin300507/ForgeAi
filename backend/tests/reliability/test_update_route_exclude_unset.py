"""
Exp151 follow-up: a PUT/PATCH route handler applying `.dict()` (without
`exclude_unset=True`) to an *Update-schema variable nulls out every field
the client didn't touch, once that schema's fields are all Optional (see
_patch_update_schema_optional_field_missing_default) -- IntegrityError on
the first NOT NULL column not supplied, a bare 500.

Confirmed live (habit_tracker, 2026-07-31): masked until the schema fix
made the request reach the handler body at all (previously 422'd at the
Pydantic-validation layer first).

Run directly: python tests/reliability/test_update_route_exclude_unset.py
"""
import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import _patch_update_route_missing_exclude_unset as _patch


def _project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="exclude_unset_test_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def test_adds_exclude_unset_to_update_handler_dict_call():
    root = _project({
        "app/routes/habit_routes.py": (
            "from fastapi import APIRouter, Depends\n"
            "from app.schemas.habit import HabitUpdate\n\n"
            "habit_router = APIRouter()\n\n"
            "@habit_router.put('/habits/{id}')\n"
            "def update_habit(id: int, habit_in: HabitUpdate, db=Depends(get_db)):\n"
            "    habit = db.query(Habit).filter(Habit.id == id).first()\n"
            "    for key, value in habit_in.dict().items():\n"
            "        setattr(habit, key, value)\n"
            "    db.commit()\n"
            "    return habit\n"
        ),
    })
    try:
        assert _patch(root) == 1
        out = (root / "app/routes/habit_routes.py").read_text(encoding="utf-8")
        ast.parse(out)
        assert "habit_in.dict(exclude_unset=True).items()" in out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_create_handler_dict_call_never_touched():
    root = _project({
        "app/routes/habit_routes.py": (
            "from fastapi import APIRouter, Depends\n"
            "from app.schemas.habit import HabitCreate\n\n"
            "habit_router = APIRouter()\n\n"
            "@habit_router.post('/habits')\n"
            "def create_habit(habit_in: HabitCreate, db=Depends(get_db)):\n"
            "    habit = Habit(**habit_in.dict())\n"
            "    db.add(habit)\n"
            "    db.commit()\n"
            "    return habit\n"
        ),
    })
    try:
        assert _patch(root) == 0
        out = (root / "app/routes/habit_routes.py").read_text(encoding="utf-8")
        assert "habit_in.dict()" in out
        assert "exclude_unset" not in out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_only_the_update_typed_variable_is_touched_not_a_same_name_var_elsewhere():
    root = _project({
        "app/routes/habit_routes.py": (
            "from fastapi import APIRouter, Depends\n"
            "from app.schemas.habit import HabitCreate, HabitUpdate\n\n"
            "habit_router = APIRouter()\n\n"
            "@habit_router.post('/habits')\n"
            "def create_habit(payload: HabitCreate, db=Depends(get_db)):\n"
            "    habit = Habit(**payload.dict())\n"
            "    db.commit()\n"
            "    return habit\n\n"
            "@habit_router.put('/habits/{id}')\n"
            "def update_habit(id: int, payload: HabitUpdate, db=Depends(get_db)):\n"
            "    habit = db.query(Habit).filter(Habit.id == id).first()\n"
            "    for key, value in payload.dict().items():\n"
            "        setattr(habit, key, value)\n"
            "    db.commit()\n"
            "    return habit\n"
        ),
    })
    try:
        assert _patch(root) == 1
        out = (root / "app/routes/habit_routes.py").read_text(encoding="utf-8")
        ast.parse(out)
        create_body = out.split("def create_habit")[1].split("def update_habit")[0]
        update_body = out.split("def update_habit")[1]
        assert "payload.dict()" in create_body and "exclude_unset" not in create_body
        assert "payload.dict(exclude_unset=True)" in update_body
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_already_has_exclude_unset_is_noop():
    root = _project({
        "app/routes/habit_routes.py": (
            "from fastapi import APIRouter, Depends\n"
            "from app.schemas.habit import HabitUpdate\n\n"
            "habit_router = APIRouter()\n\n"
            "@habit_router.put('/habits/{id}')\n"
            "def update_habit(id: int, habit_in: HabitUpdate, db=Depends(get_db)):\n"
            "    for key, value in habit_in.dict(exclude_unset=True).items():\n"
            "        pass\n"
        ),
    })
    try:
        before = (root / "app/routes/habit_routes.py").read_text(encoding="utf-8")
        assert _patch(root) == 0
        assert (root / "app/routes/habit_routes.py").read_text(encoding="utf-8") == before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_clean_project_untouched():
    root = _project({
        "app/routes/task_routes.py": (
            "from fastapi import APIRouter\n\n"
            "task_router = APIRouter()\n\n"
            "@task_router.get('/tasks')\n"
            "def get_tasks():\n"
            "    return []\n"
        ),
    })
    try:
        assert _patch(root) == 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


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
