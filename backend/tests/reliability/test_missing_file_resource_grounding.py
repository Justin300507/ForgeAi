"""
Regression tests for missing_file_prompt.py's _find_resource_model_and_schema.

MissingEndpoint is the single most common real failure category in
failure_memory/patterns.json as of 2026-07-24 (356 occurrences, more than
the next two categories combined). The missing-file agent that regenerates
a missing app/routes/<resource>_routes.py file previously had no grounding
in what fields that resource's REAL model/schema actually declare -- only
a same-directory "reference sibling" file (a DIFFERENT resource, useful
for style/conventions only) and the generic FASTAPI_CONTRACT. This is the
same root cause already found and fixed for architecture-repair's
existing_symbols={} gap: a real anti-hallucination signal (the actual
model/schema fields) existed on disk and simply wasn't being read here.

Run directly: python tests/reliability/test_missing_file_resource_grounding.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.prompts.missing_file_prompt import _find_resource_model_and_schema


def _make_project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="missingfiletest_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def _cleanup(root: Path):
    shutil.rmtree(root, ignore_errors=True)


_CLASS_MODEL = (
    "from sqlalchemy import Column, Integer, String\n"
    "from app.database import Base\n\n"
    "class Class(Base):\n"
    "    __tablename__ = 'classes'\n"
    "    id = Column(Integer, primary_key=True)\n"
    "    name = Column(String, nullable=False)\n"
    "    room = Column(String, nullable=True)\n"
)

_CLASS_SCHEMA = (
    "from pydantic import BaseModel\n\n"
    "class ClassCreate(BaseModel):\n"
    "    name: str\n"
    "    room: str\n\n"
    "class ClassResponse(BaseModel):\n"
    "    id: int\n"
    "    name: str\n"
    "    room: str\n"
)


def test_finds_real_model_and_schema_fields_for_plural_route_stem():
    # "class_routes.py" -> resource "Class" -- but the route file itself is
    # named after the plural concept; the model class is singular.
    root = _make_project({
        "app/models/class_.py": _CLASS_MODEL,
        "app/schemas/class_.py": _CLASS_SCHEMA,
    })
    grounding = _find_resource_model_and_schema(str(root), "app/routes/class_routes.py")
    _cleanup(root)
    assert grounding is not None
    assert "Class (app/models/class_.py): id, name, room" in grounding
    assert "ClassCreate" in grounding
    assert "ClassResponse" in grounding


def test_matches_singular_model_for_plural_resource_name():
    # "classes_routes.py" -> resource_name "Classes" -- the real model is
    # "Class" (singular). Must still match via the singular fallback.
    root = _make_project({"app/models/class_.py": _CLASS_MODEL})
    grounding = _find_resource_model_and_schema(str(root), "app/routes/classes_routes.py")
    _cleanup(root)
    assert grounding is not None
    assert "Class" in grounding


def test_returns_none_when_nothing_matches():
    root = _make_project({"app/models/habit.py": (
        "from sqlalchemy import Column, Integer\nfrom app.database import Base\n\n"
        "class Habit(Base):\n    __tablename__ = 'habits'\n    id = Column(Integer, primary_key=True)\n"
    )})
    grounding = _find_resource_model_and_schema(str(root), "app/routes/class_routes.py")
    _cleanup(root)
    assert grounding is None


def test_does_not_match_unrelated_class_containing_resource_name_as_substring():
    # A bare substring match would wrongly pull in "SubclassRegistry" for
    # a "class" resource -- must require prefix match on the resource name.
    root = _make_project({"app/models/other.py": (
        "from sqlalchemy import Column, Integer\nfrom app.database import Base\n\n"
        "class SubclassRegistry(Base):\n    __tablename__ = 'x'\n    id = Column(Integer, primary_key=True)\n"
    )})
    grounding = _find_resource_model_and_schema(str(root), "app/routes/class_routes.py")
    _cleanup(root)
    assert grounding is None


def test_noop_for_non_route_files():
    root = _make_project({"app/models/class_.py": _CLASS_MODEL})
    grounding = _find_resource_model_and_schema(str(root), "app/schemas/class_.py")
    _cleanup(root)
    assert grounding is None


def test_noop_without_project_path():
    grounding = _find_resource_model_and_schema(None, "app/routes/class_routes.py")
    assert grounding is None


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
