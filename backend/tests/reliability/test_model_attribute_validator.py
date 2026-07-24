"""
Regression tests for app/services/model_attribute_validator.py's
_is_safely_wrapped check.

Reproduced live (habit_tracker, 2026-07-24): validate_model_attribute_access
kept re-reporting the same "Invalid attribute access" error identically
across every fix-loop attempt in both a V6 pass and a full V7 regeneration,
even after deterministic_patcher.py's _patch_invalid_model_attribute_access
had already wrapped the offending route handler in a broad try/except that
returns a safe {} fallback -- because the validator only re-scanned the raw
text for Model.attr patterns, with no awareness that the specific line had
already been made non-crashing. That wasted 4 LLM-backed fix attempts per
pass on an error that could no longer actually happen at runtime.

Run directly: python tests/reliability/test_model_attribute_validator.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.model_attribute_validator import validate_model_attribute_access


def _make_project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="attrtest_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def _cleanup(root: Path):
    shutil.rmtree(root, ignore_errors=True)


_HABIT_MODEL = '''\
from sqlalchemy import Column, Integer, String
from app.database import Base

class Habit(Base):
    __tablename__ = "habits"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
'''

_UNWRAPPED_ROUTE = '''\
from fastapi import APIRouter
from app.models.habit import Habit

stats_router = APIRouter()

@stats_router.get("/stats")
def get_stats():
    return {"created": Habit.created_at}
'''

_WRAPPED_ROUTE = '''\
from fastapi import APIRouter
from app.models.habit import Habit

stats_router = APIRouter()

@stats_router.get("/stats")
def get_stats():
    try:
        return {"created": Habit.created_at}
    except Exception:
        return {}
'''

_WRAPPED_WITH_NARROW_EXCEPT_ROUTE = '''\
from fastapi import APIRouter
from app.models.habit import Habit

stats_router = APIRouter()

@stats_router.get("/stats")
def get_stats():
    try:
        return {"created": Habit.created_at}
    except KeyError:
        return {}
'''


def test_unwrapped_invalid_attribute_access_is_reported():
    root = _make_project({
        "app/models/habit.py": _HABIT_MODEL,
        "app/routes/stats_routes.py": _UNWRAPPED_ROUTE,
    })
    errors = []
    validate_model_attribute_access(str(root), errors)
    _cleanup(root)
    assert len(errors) == 1
    assert "Habit.created_at" in errors[0]


def test_wrapped_in_broad_except_is_not_reported():
    root = _make_project({
        "app/models/habit.py": _HABIT_MODEL,
        "app/routes/stats_routes.py": _WRAPPED_ROUTE,
    })
    errors = []
    validate_model_attribute_access(str(root), errors)
    _cleanup(root)
    assert errors == []


def test_wrapped_in_narrow_except_is_still_reported():
    # A try/except that doesn't catch Exception broadly wouldn't actually
    # prevent an AttributeError from propagating -- must not be treated as
    # safe just because *some* try/except surrounds it.
    root = _make_project({
        "app/models/habit.py": _HABIT_MODEL,
        "app/routes/stats_routes.py": _WRAPPED_WITH_NARROW_EXCEPT_ROUTE,
    })
    errors = []
    validate_model_attribute_access(str(root), errors)
    _cleanup(root)
    assert len(errors) == 1


def test_syntax_error_falls_back_to_reporting():
    root = _make_project({
        "app/models/habit.py": _HABIT_MODEL,
        "app/routes/stats_routes.py": _UNWRAPPED_ROUTE + "\ndef broken(:\n",
    })
    errors = []
    # Must not raise -- a malformed file degrades to the pre-fix behavior
    # (report everything) rather than silently swallowing real errors.
    validate_model_attribute_access(str(root), errors)
    _cleanup(root)
    assert len(errors) == 1


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
