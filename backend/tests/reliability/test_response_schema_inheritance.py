"""
Verifies _patch_response_schema_inherited_required_fields
(app/services/deterministic_patcher.py): fixes required fields a
*Response class INHERITS from a shared *Base class but never re-declares
itself -- a gap the existing _patch_response_schemas_optional misses
entirely, since it only scans a class's own body text.

Root cause this fixes (confirmed live, 2026-07-11): a generated course-
platform app's CourseResponse(CourseBase) inherited price/duration_hours/
difficulty as REQUIRED from CourseBase, but the Course SQLAlchemy model
has no such columns at all. FastAPI's response-model serialization tries
to read them off the returned ORM object, finds nothing, and the request
crashes -- UNREACHABLE by any test until this cycle's role-aware
validation fix made it possible to reach an authorized "instructor"
identity for the first time.

Run directly: python tests/reliability/test_response_schema_inheritance.py
"""
import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import (
    _patch_response_schema_inherited_required_fields, _field_rhs_has_real_default,
)


def _make_project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="inherittest_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


_COURSE_SCHEMA = '''\
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict

class CourseBase(BaseModel):
    title: str = Field(min_length=1)
    price: float = Field(ge=0.0)

class CourseCreate(CourseBase):
    pass

class CourseResponse(CourseBase):
    id: int

    model_config = ConfigDict(from_attributes=True)
'''


def test_field_rhs_default_detection():
    # Field(...) with only constraint kwargs, no default -- still required
    assert _field_rhs_has_real_default("Field(ge=0.0)") is False
    assert _field_rhs_has_real_default("Field(min_length=1)") is False
    # Explicit ellipsis -- still required
    assert _field_rhs_has_real_default("Field(...)") is False
    # Positional default value -- has a real default
    assert _field_rhs_has_real_default('Field("diner", pattern="^(diner|staff)$")') is True
    assert _field_rhs_has_real_default("Field(None, ge=0.0)") is True
    # default= / default_factory= kwarg -- has a real default
    assert _field_rhs_has_real_default("Field(default=5)") is True
    assert _field_rhs_has_real_default("Field(default_factory=list)") is True
    # Plain assignment, no Field() wrapper
    assert _field_rhs_has_real_default('"active"') is True
    assert _field_rhs_has_real_default("") is False


def test_adds_missing_inherited_fields_to_response_class():
    root = _make_project({"app/schemas/course.py": _COURSE_SCHEMA})
    n = _patch_response_schema_inherited_required_fields(root)
    content = (root / "app/schemas/course.py").read_text(encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    assert n == 1
    ast.parse(content)
    response_block = content[content.index("class CourseResponse"):]
    assert "title: Optional[Any] = None" in response_block
    assert "price: Optional[Any] = None" in response_block


def test_does_not_touch_create_schema():
    """CourseCreate must keep its REAL requiredness -- only Response-like
    classes get the safety override."""
    root = _make_project({"app/schemas/course.py": _COURSE_SCHEMA})
    _patch_response_schema_inherited_required_fields(root)
    content = (root / "app/schemas/course.py").read_text(encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    create_block = content[content.index("class CourseCreate"):content.index("class CourseResponse")]
    assert "Optional[Any]" not in create_block


def test_field_already_overridden_in_response_is_left_alone():
    schema = _COURSE_SCHEMA.replace(
        "class CourseResponse(CourseBase):\n    id: int\n",
        "class CourseResponse(CourseBase):\n    id: int\n    price: Optional[float] = None\n",
    )
    root = _make_project({"app/schemas/course.py": schema})
    n = _patch_response_schema_inherited_required_fields(root)
    content = (root / "app/schemas/course.py").read_text(encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    # title is still missing (needs a fix), price was already handled -- so
    # the file still gets touched, but price shouldn't be duplicated
    assert n == 1
    response_block = content[content.index("class CourseResponse"):]
    assert response_block.count("price:") == 1


def test_no_fix_needed_when_response_already_declares_everything():
    schema = '''\
from pydantic import BaseModel
class XBase(BaseModel):
    title: str
class XResponse(XBase):
    title: str
    id: int
'''
    root = _make_project({"app/schemas/x.py": schema})
    n = _patch_response_schema_inherited_required_fields(root)
    shutil.rmtree(root, ignore_errors=True)
    assert n == 0


def test_base_class_in_different_file_still_resolved():
    root = _make_project({
        "app/schemas/base.py": "from pydantic import BaseModel, Field\nclass ItemBase(BaseModel):\n    name: str = Field(min_length=1)\n",
        "app/schemas/item.py": "from app.schemas.base import ItemBase\nclass ItemResponse(ItemBase):\n    id: int\n",
    })
    n = _patch_response_schema_inherited_required_fields(root)
    content = (root / "app/schemas/item.py").read_text(encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    assert n == 1
    assert "name: Optional[Any] = None" in content


def test_no_schemas_dir_is_a_noop():
    root = Path(tempfile.mkdtemp(prefix="inherittest_"))
    assert _patch_response_schema_inherited_required_fields(root) == 0
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
