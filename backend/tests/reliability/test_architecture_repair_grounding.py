"""
Regression tests for the architecture-repair grounding/safety fixes.

Reproduced live (habit_tracker, 2026-07-24): generate_architecture_fix()'s
prompt template supports an "EXISTING SYMBOLS -- REUSE THESE EXACT NAMES,
DO NOT INVENT NEW ONES" grounding block, but both call sites in
v6_orchestrator.py passed existing_symbols={} unconditionally -- the
anti-hallucination mechanism existed but was never wired to real project
state. In the same live run, the architecture-repair LLM call hallucinated
an entirely unrelated domain (Transaction/Class/gym-membership fields) for
a habit tracker's stats_routes.py, and there was no revert-on-worse for
this step (unlike the regular per-file fix loop, which already has one).

This file only covers _collect_existing_symbols's extension to scan
app/models (previously schemas/services only) -- the two call sites and
the revert-on-worse blocks live inside v6_orchestrator.py's large,
side-effecting generate_project_v6()/repair_project() functions and aren't
practically unit-testable in isolation; they were verified by code review
and by exactly mirroring the fix-loop's already-proven revert mechanism
(same snapshot/restore primitives, same file in the same codebase).

Run directly: python tests/reliability/test_architecture_repair_grounding.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.project_service import _collect_existing_symbols


def _make_project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="archgroundtest_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def _cleanup(root: Path):
    shutil.rmtree(root, ignore_errors=True)


def test_collects_model_class_names():
    # This is exactly what was missing: the LLM invented "Transaction" and
    # "Class" models that don't exist in the project, because nothing told
    # it the real model names were User/Habit/ProgressLog.
    root = _make_project({
        "app/models/habit.py": (
            "from sqlalchemy import Column, Integer, String\n"
            "from app.database import Base\n\n"
            "class Habit(Base):\n"
            "    __tablename__ = 'habits'\n"
            "    id = Column(Integer, primary_key=True)\n"
        ),
        "app/models/user.py": (
            "from sqlalchemy import Column, Integer, String\n"
            "from app.database import Base\n\n"
            "class User(Base):\n"
            "    __tablename__ = 'users'\n"
            "    id = Column(Integer, primary_key=True)\n"
        ),
    })
    existing = _collect_existing_symbols(str(root))
    _cleanup(root)
    assert existing.get("app/models/habit.py") == ["Habit"]
    assert existing.get("app/models/user.py") == ["User"]


def test_still_collects_schemas_and_services():
    root = _make_project({
        "app/schemas/habit.py": "from pydantic import BaseModel\n\nclass HabitResponse(BaseModel):\n    id: int\n",
        "app/services/habit_service.py": "def create_habit():\n    pass\n",
    })
    existing = _collect_existing_symbols(str(root))
    _cleanup(root)
    assert existing.get("app/schemas/habit.py") == ["HabitResponse"]
    assert existing.get("app/services/habit_service.py") == ["create_habit"]


def test_noop_without_relevant_dirs():
    root = Path(tempfile.mkdtemp(prefix="archgroundtest_"))
    existing = _collect_existing_symbols(str(root))
    _cleanup(root)
    assert existing == {}


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
