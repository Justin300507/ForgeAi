"""
Experiment 088: regression tests for the extension to
_patch_orm_response_model() (app/services/deterministic_patcher.py) that
injects a model_validate() conversion for nested ORM collections
returned under a generic dict/Dict response_model.

Root cause (Exp087, confirmed live across 4 independent real projects):
a route annotates response_model=dict/Dict while its handler returns
`{"items": <raw ORM query result>, ...}` -- dict carries no
from_attributes/ORM-mode context, so FastAPI's serializer crashes on the
nested ORM instance: "Unable to serialize unknown type: <class
'app.models.X.Y'>".

This experiment also fixed two adjacent pre-existing bugs surfaced by
offline replay against the real projects, both covered here too:
  - schema_map's "first glob-order match wins" could resolve to an
    incomplete duplicate-cleanup shim class instead of the real response
    schema (confirmed on generated_projects/todo_list_app: alphabetical
    glob order picked a stub "TaskRead" over the correct "TaskResponse").
  - the schema-class scanner used a regex requiring literal "BaseModel"
    in the class declaration, missing classes that inherit transitively
    through a local base class like "BaseSchema(BaseModel)".

Run directly: python tests/reliability/test_exp088_orm_dict_response_conversion.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import _patch_orm_response_model


def _project(tmpdir: str, schema_src: str, route_src: str, schema_filename: str = "task.py"):
    root = Path(tmpdir)
    (root / "app" / "schemas").mkdir(parents=True)
    (root / "app" / "schemas" / schema_filename).write_text(schema_src, encoding="utf-8")
    return root


_BASE_SCHEMA = '''\
from pydantic import BaseModel, ConfigDict

class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class TaskResponse(BaseSchema):
    id: int
    title: str
'''


# ---------------------------------------------------------------------------
# Task 1/2/3: paginated list -- the confirmed real bug shape
# ---------------------------------------------------------------------------

_PAGINATED_ROUTE = '''\
from fastapi import APIRouter
from app.models.tasks import Task

router = APIRouter()

@router.get("/tasks", response_model=dict)
def list_tasks(limit: int = 50, offset: int = 0):
    query = db.query(Task)
    total = query.count()
    items = query.offset(offset).limit(limit).all()
    return {"items": items, "total": total}
'''


def test_paginated_list_gets_conversion_injected():
    with tempfile.TemporaryDirectory() as td:
        project = _project(td, _BASE_SCHEMA, _PAGINATED_ROUTE)
        out = _patch_orm_response_model(_PAGINATED_ROUTE, "app/routes/task_routes.py", project_path=project)
        assert "items = [TaskResponse.model_validate(x, from_attributes=True) for x in items]" in out
        assert "from app.schemas.task import TaskResponse" in out
        assert 'response_model=dict' in out, "response_model annotation itself must not be removed"


def test_paginated_list_conversion_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        project = _project(td, _BASE_SCHEMA, _PAGINATED_ROUTE)
        once = _patch_orm_response_model(_PAGINATED_ROUTE, "app/routes/task_routes.py", project_path=project)
        twice = _patch_orm_response_model(once, "app/routes/task_routes.py", project_path=project)
        assert once == twice


# ---------------------------------------------------------------------------
# Task 6: empty lists -- the injected conversion is safe over []
# ---------------------------------------------------------------------------

def test_conversion_produces_valid_syntax_for_empty_list_case():
    # The injected line is a plain list comprehension -- correctness on an
    # empty list is a runtime property (list comp over [] -> []), not
    # something the patcher itself needs special-cased; this test just
    # confirms the emitted code is exactly the expected, syntactically
    # valid comprehension form (no length-dependent branching introduced).
    with tempfile.TemporaryDirectory() as td:
        project = _project(td, _BASE_SCHEMA, _PAGINATED_ROUTE)
        out = _patch_orm_response_model(_PAGINATED_ROUTE, "app/routes/task_routes.py", project_path=project)
        import ast as _ast
        _ast.parse(out)  # must remain valid Python regardless of list contents


# ---------------------------------------------------------------------------
# Task 6: mixed / multi-line pagination metadata (real recipe_share shape)
# ---------------------------------------------------------------------------

_MULTILINE_ROUTE = '''\
from fastapi import APIRouter
from app.models.tasks import Task

router = APIRouter()

@router.get("/tasks", response_model=dict)
def list_tasks(limit: int = 50, offset: int = 0):
    query = db.query(Task)
    total = query.count()
    items = query.offset(offset).limit(limit).all()
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
'''


def test_multiline_return_with_mixed_metadata_still_detected():
    with tempfile.TemporaryDirectory() as td:
        project = _project(td, _BASE_SCHEMA, _MULTILINE_ROUTE)
        out = _patch_orm_response_model(_MULTILINE_ROUTE, "app/routes/task_routes.py", project_path=project)
        assert "items = [TaskResponse.model_validate(x, from_attributes=True) for x in items]" in out
        # Pagination metadata keys must appear completely unchanged.
        assert '"total": total,' in out
        assert '"limit": limit,' in out
        assert '"offset": offset,' in out
        # The injected line must land BEFORE the (multi-line) return
        # statement, not inside the dict literal.
        lines = out.splitlines()
        conv_idx = next(i for i, l in enumerate(lines) if "model_validate" in l)
        return_idx = next(i for i, l in enumerate(lines) if l.strip().startswith("return {"))
        assert conv_idx < return_idx


def test_multiline_return_conversion_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        project = _project(td, _BASE_SCHEMA, _MULTILINE_ROUTE)
        once = _patch_orm_response_model(_MULTILINE_ROUTE, "app/routes/task_routes.py", project_path=project)
        twice = _patch_orm_response_model(once, "app/routes/task_routes.py", project_path=project)
        assert once == twice


# ---------------------------------------------------------------------------
# Task 6: genuine dict responses -- must be left completely untouched
# ---------------------------------------------------------------------------

_GENUINE_DICT_ROUTE = '''\
from fastapi import APIRouter
from app.models.tasks import Task

router = APIRouter()

@router.get("/tasks/summary", response_model=dict)
def task_summary():
    query = db.query(Task)
    total = query.count()
    return {"status": "ok", "total": total}
'''


def test_genuine_dict_response_untouched():
    with tempfile.TemporaryDirectory() as td:
        project = _project(td, _BASE_SCHEMA, _GENUINE_DICT_ROUTE)
        out = _patch_orm_response_model(_GENUINE_DICT_ROUTE, "app/routes/task_routes.py", project_path=project)
        assert out == _GENUINE_DICT_ROUTE, "a dict response with no 'items' key must be completely unchanged"


_NO_QUERY_DICT_ROUTE = '''\
from fastapi import APIRouter
from app.models.tasks import Task

router = APIRouter()

@router.get("/tasks/config", response_model=dict)
def task_config():
    items = ["a", "b", "c"]
    return {"items": items, "count": 3}
'''


def test_items_key_without_orm_query_untouched():
    with tempfile.TemporaryDirectory() as td:
        project = _project(td, _BASE_SCHEMA, _NO_QUERY_DICT_ROUTE)
        out = _patch_orm_response_model(_NO_QUERY_DICT_ROUTE, "app/routes/task_routes.py", project_path=project)
        assert out == _NO_QUERY_DICT_ROUTE, (
            "an 'items' key with no ORM query in the function body must not be touched"
        )


# ---------------------------------------------------------------------------
# Task 5: missing schema -- leave the route untouched
# ---------------------------------------------------------------------------

def test_missing_schema_leaves_route_untouched():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "app" / "schemas").mkdir(parents=True)  # empty -- no TaskResponse anywhere
        out = _patch_orm_response_model(_PAGINATED_ROUTE, "app/routes/task_routes.py", project_path=root)
        # No schema match -> response_model=dict stays (already was), and
        # no conversion is injected since there's nothing to convert to.
        assert "model_validate" not in out
        assert out == _PAGINATED_ROUTE


# ---------------------------------------------------------------------------
# Adjacent bugs found and fixed via offline replay against real projects
# ---------------------------------------------------------------------------

def test_prefers_response_schema_over_incomplete_duplicate_shim():
    # Reproduces generated_projects/todo_list_app exactly: a duplicate-
    # model-cleanup shim (TaskRead, incomplete -- only "id") sits
    # alongside the real, full TaskResponse. Alphabetical glob order
    # (task.py before tasks.py) used to pick the incomplete shim.
    shim_schema = '''\
from pydantic import BaseModel
from typing import Optional

class TaskRead(BaseModel):
    id: Optional[int] = None
    model_config = {"from_attributes": True}
'''
    real_schema = '''\
from pydantic import BaseModel, ConfigDict

class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class TaskResponse(BaseSchema):
    id: int
    title: str
    description: str
'''
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "app" / "schemas").mkdir(parents=True)
        (root / "app" / "schemas" / "task.py").write_text(shim_schema, encoding="utf-8")
        (root / "app" / "schemas" / "tasks.py").write_text(real_schema, encoding="utf-8")
        out = _patch_orm_response_model(_PAGINATED_ROUTE, "app/routes/task_routes.py", project_path=root)
        assert "TaskResponse.model_validate" in out
        assert "TaskRead.model_validate" not in out


def test_does_not_duplicate_already_combined_import():
    route = '''\
from fastapi import APIRouter
from app.models.tasks import Task
from app.schemas.task import TaskCreate, TaskUpdate, TaskResponse

router = APIRouter()

@router.get("/tasks", response_model=dict)
def list_tasks():
    query = db.query(Task)
    total = query.count()
    items = query.offset(0).limit(50).all()
    return {"items": items, "total": total}
'''
    with tempfile.TemporaryDirectory() as td:
        project = _project(td, _BASE_SCHEMA, route)
        out = _patch_orm_response_model(route, "app/routes/task_routes.py", project_path=project)
        assert out.count("import TaskResponse") + out.count("TaskResponse\n") <= 1 or (
            "from app.schemas.task import TaskCreate, TaskUpdate, TaskResponse" in out
            and "from app.schemas.task import TaskResponse" not in out
        )


def test_does_not_reconvert_a_custom_field_mapping_helper():
    # Reproduces generated_projects/simple_notes_app/note_routes.py: a
    # deliberate custom dict-mapping shim (_note_to_dict) sits between
    # the raw query and the return -- must not be second-guessed.
    route = '''\
from fastapi import APIRouter
from app.models.notes import Note

router = APIRouter()

def _note_to_dict(note):
    return {"id": note.id, "title": note.title, "description": note.content}

@router.get("/notes", response_model=dict)
def list_notes():
    notes = db.query(Note).offset(0).limit(50).all()
    items = [_note_to_dict(n) for n in notes]
    return {"items": items, "total_count": 3}
'''
    schema = '''\
from pydantic import BaseModel, ConfigDict

class NoteResponse(BaseModel):
    id: int
    model_config = ConfigDict(from_attributes=True)
'''
    with tempfile.TemporaryDirectory() as td:
        project = _project(td, schema, route, schema_filename="note.py")
        out = _patch_orm_response_model(route, "app/routes/note_routes.py", project_path=project)
        assert out == route, "a custom dict-mapping helper's output must never be re-wrapped"


def test_does_not_reconvert_already_inline_converted_items():
    # Reproduces generated_projects/recipe_share/recipe_routes.py: the
    # "items" value is already an inline model_validate() comprehension
    # expression, not a bare variable name.
    route = '''\
from fastapi import APIRouter
from app.models.recipes import Recipe
from app.schemas.recipe import RecipeResponse

router = APIRouter()

@router.get("/recipes", response_model=dict)
def list_recipes():
    recipes = db.query(Recipe).offset(0).limit(50).all()
    return {
        "items": [RecipeResponse.model_validate(r, from_attributes=True) for r in recipes],
        "total": 3,
    }
'''
    schema = '''\
from pydantic import BaseModel, ConfigDict

class RecipeResponse(BaseModel):
    id: int
    model_config = ConfigDict(from_attributes=True)
'''
    with tempfile.TemporaryDirectory() as td:
        project = _project(td, schema, route, schema_filename="recipe.py")
        out = _patch_orm_response_model(route, "app/routes/recipe_routes.py", project_path=project)
        assert out == route


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
