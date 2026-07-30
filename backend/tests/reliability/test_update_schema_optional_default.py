"""
Regression tests for _patch_update_schema_optional_field_missing_default
(app/services/deterministic_patcher.py).

Root cause (habit_tracker, 2026-07-25): HabitUpdate declared
`name: Optional[str] = Field(min_length=1)` -- Optional-typed, but the
Field() call has no positional default and no default=/default_factory=
kwarg, so Pydantic v2 still treats the field as REQUIRED (Optional[...]
only widens the accepted type to include None; it does not by itself
supply a default). The live CRUD journey's partial-update PATCH omits
untouched fields exactly as a real client would, and every "Edit entity"
step 422'd with "field required" -- an Update schema where every field
was unusable for a partial update, even after the sibling fix for
Create-schema requiredness (Exp: preflight.py's _fix_model_schema_notnull_gap)
was already in place, since this is a distinct schema (not the model).

Run directly: python tests/reliability/test_update_schema_optional_default.py
"""
import ast
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import (
    _patch_update_schema_optional_field_missing_default as _patch,
)


def _make_project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="updateopt_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def test_field_call_with_no_default_gets_none_injected():
    # Exact live shape.
    schema = '''\
from pydantic import BaseModel, Field
from typing import Optional

class HabitCreate(BaseModel):
    name: str = Field(min_length=1)
    frequency: str = Field(min_length=1)
    streak: Optional[int] = None

class HabitUpdate(BaseModel):
    name: Optional[str] = Field(min_length=1)
    frequency: Optional[str] = Field(min_length=1)
    streak: Optional[int] = None

class HabitResponse(BaseModel):
    id: int
    name: Optional[str] = None
'''
    root = _make_project({"app/schemas/habit.py": schema})
    n = _patch(root)
    out = (root / "app" / "schemas" / "habit.py").read_text(encoding="utf-8")
    assert n == 1
    ast.parse(out)
    assert "name: Optional[str] = Field(None, min_length=1)" in out
    assert "frequency: Optional[str] = Field(None, min_length=1)" in out
    # HabitCreate's genuinely-required fields must be untouched.
    assert "name: str = Field(min_length=1)" in out
    assert "frequency: str = Field(min_length=1)" in out
    # Already-fine and Response-class fields untouched.
    assert out.count("streak: Optional[int] = None") == 2
    assert "id: int" in out


def test_bare_optional_with_no_default_at_all_gets_none():
    schema = '''\
from pydantic import BaseModel
from typing import Optional

class ItemUpdate(BaseModel):
    name: Optional[str]
    count: Optional[int]
'''
    root = _make_project({"app/schemas/item.py": schema})
    n = _patch(root)
    out = (root / "app" / "schemas" / "item.py").read_text(encoding="utf-8")
    assert n == 1
    ast.parse(out)
    assert "name: Optional[str] = None" in out
    assert "count: Optional[int] = None" in out


def test_bare_ellipsis_default_converted_to_none():
    schema = '''\
from pydantic import BaseModel
from typing import Optional

class ItemUpdate(BaseModel):
    name: Optional[str] = ...
'''
    root = _make_project({"app/schemas/item.py": schema})
    n = _patch(root)
    out = (root / "app" / "schemas" / "item.py").read_text(encoding="utf-8")
    assert n == 1
    ast.parse(out)
    assert "name: Optional[str] = None" in out


def test_field_ellipsis_default_swapped_to_none():
    schema = '''\
from pydantic import BaseModel, Field
from typing import Optional

class ItemUpdate(BaseModel):
    name: Optional[str] = Field(..., min_length=1)
'''
    root = _make_project({"app/schemas/item.py": schema})
    n = _patch(root)
    out = (root / "app" / "schemas" / "item.py").read_text(encoding="utf-8")
    assert n == 1
    ast.parse(out)
    assert "name: Optional[str] = Field(None, min_length=1)" in out


def test_already_has_real_default_untouched():
    schema = '''\
from pydantic import BaseModel, Field
from typing import Optional

class ItemUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    tag: Optional[str] = "default-tag"
    note: Optional[str] = None
'''
    root = _make_project({"app/schemas/item.py": schema})
    n = _patch(root)
    out = (root / "app" / "schemas" / "item.py").read_text(encoding="utf-8")
    assert n == 0
    assert out == schema


def test_non_optional_fields_now_wrapped_optional():
    """Exp151 follow-up (habit_tracker, 2026-07-31): the dominant real
    shape isn't an Optional field missing a default -- it's a field
    never made Optional at all, e.g. `streak: int` verbatim-copied from
    the sibling *Create schema into *Update. That makes the WHOLE update
    request all-or-nothing: a real partial-update body (or the journey
    runner's) omitting untouched fields 422s. Confirmed live: `PUT
    /habits/{id}` failing exactly this way. A non-Optional field on an
    Update schema is now wrapped like any other -- the id-must-be-
    supplied hypothesis this test used to guard doesn't match how these
    apps are actually generated (the id always comes from the URL path
    parameter, e.g. `update_habit(id: int, habit_in: HabitUpdate, ...)`,
    never from the Update body itself)."""
    schema = '''\
from pydantic import BaseModel, Field

class ItemUpdate(BaseModel):
    id: int
    name: str = Field(min_length=1)
'''
    root = _make_project({"app/schemas/item.py": schema})
    n = _patch(root)
    out = (root / "app" / "schemas" / "item.py").read_text(encoding="utf-8")
    assert n == 1
    ast.parse(out)
    assert "from typing import Optional" in out
    assert "id: Optional[int] = None" in out
    assert "name: Optional[str] = Field(None, min_length=1)" in out


def test_create_and_response_schemas_never_touched():
    # Only *Update classes are in scope -- Create schemas legitimately
    # require their fields, and Response schemas are a different concern.
    schema = '''\
from pydantic import BaseModel, Field
from typing import Optional

class ItemCreate(BaseModel):
    name: Optional[str] = Field(min_length=1)

class ItemResponse(BaseModel):
    name: Optional[str] = Field(min_length=1)
'''
    root = _make_project({"app/schemas/item.py": schema})
    n = _patch(root)
    out = (root / "app" / "schemas" / "item.py").read_text(encoding="utf-8")
    assert n == 0
    assert out == schema


def test_multiple_update_classes_in_one_file_both_fixed():
    schema = '''\
from pydantic import BaseModel, Field
from typing import Optional

class HabitUpdate(BaseModel):
    name: Optional[str] = Field(min_length=1)

class UserUpdate(BaseModel):
    username: Optional[str] = Field(min_length=1)
'''
    root = _make_project({"app/schemas/combined.py": schema})
    n = _patch(root)
    out = (root / "app" / "schemas" / "combined.py").read_text(encoding="utf-8")
    assert n == 1
    ast.parse(out)
    assert "name: Optional[str] = Field(None, min_length=1)" in out
    assert "username: Optional[str] = Field(None, min_length=1)" in out


def test_idempotent_second_pass_is_noop():
    schema = '''\
from pydantic import BaseModel, Field
from typing import Optional

class ItemUpdate(BaseModel):
    name: Optional[str] = Field(min_length=1)
'''
    root = _make_project({"app/schemas/item.py": schema})
    _patch(root)
    first = (root / "app" / "schemas" / "item.py").read_text(encoding="utf-8")
    n2 = _patch(root)
    second = (root / "app" / "schemas" / "item.py").read_text(encoding="utf-8")
    assert n2 == 0
    assert first == second


def test_no_op_when_no_schemas_dir():
    root = Path(tempfile.mkdtemp(prefix="updateopt_"))
    assert _patch(root) == 0


def test_last_field_no_default_does_not_swallow_blank_line_to_next_class():
    """The exact live shape (habit.py, Exp151 follow-up): a non-Optional,
    no-`=` field as the LAST field in *Update, immediately followed by a
    blank line and the next class. _CLASS_FIELD_LINE_RE's `\\s*(=.*)?$`
    can match `\\n` (it's in `\\s`), so a naive replacement swallowed the
    blank line and concatenated `None` directly onto `class Habit
    Response(BaseModel):` with no newline at all -- a SyntaxError."""
    schema = '''\
from typing import Optional
from pydantic import BaseModel, Field

class HabitUpdate(BaseModel):
    name: str = Field(min_length=1)
    streak: int

class HabitResponse(BaseModel):
    id: int
'''
    root = _make_project({"app/schemas/habit.py": schema})
    n = _patch(root)
    out = (root / "app" / "schemas" / "habit.py").read_text(encoding="utf-8")
    assert n == 1
    ast.parse(out)  # would raise SyntaxError on the swallowed-newline bug
    assert "streak: Optional[int] = None\n\nclass HabitResponse" in out


def test_imports_optional_from_typing_when_not_already_imported():
    schema = '''\
from pydantic import BaseModel, Field

class ItemUpdate(BaseModel):
    title: str = Field(min_length=1)
    price: float
'''
    root = _make_project({"app/schemas/item.py": schema})
    n = _patch(root)
    out = (root / "app" / "schemas" / "item.py").read_text(encoding="utf-8")
    assert n == 1
    ast.parse(out)
    assert "from typing import Optional" in out
    assert "title: Optional[str] = Field(None, min_length=1)" in out
    assert "price: Optional[float] = None" in out


def test_extends_existing_typing_import_instead_of_duplicating():
    schema = '''\
from typing import List
from pydantic import BaseModel

class ItemUpdate(BaseModel):
    tags: List[str]
'''
    root = _make_project({"app/schemas/item.py": schema})
    n = _patch(root)
    out = (root / "app" / "schemas" / "item.py").read_text(encoding="utf-8")
    assert n == 1
    ast.parse(out)
    assert out.count("from typing import") == 1
    assert "List" in out and "Optional" in out


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
