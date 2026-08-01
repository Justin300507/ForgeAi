"""
Exp052: regression tests for the "schema cleanup" group of deterministic
repairs (Priority 1) in app/services/deterministic_patcher.py --
8 functions previously untested: _patch_schemas_from_attributes,
_patch_pydantic_orm_mode, _patch_schema_nullable_required_mismatch,
_patch_response_schema_id_and_datetimes, _patch_missing_pydantic_imports,
_patch_create_missing_schemas, _patch_orm_type_in_route_schemas,
_patch_list_response_model_mismatch.

Fixtures are representative of real generated-project shapes (Pydantic
schema files paired with SQLAlchemy model files, the LLM's actual
`Column(...)` / `class X(BaseModel)` idioms) rather than minimal toy
strings, matching the shapes these functions were written against per
their own docstrings/comments.

Run directly: python tests/reliability/test_schema_cleanup_repairs.py
"""
import ast
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import (
    _patch_schemas_from_attributes,
    _patch_pydantic_orm_mode,
    _patch_bare_pydantic_from_attributes,
    _patch_pydantic_field_type_name_collisions,
    _patch_required_create_schema_model_nullability,
    _patch_schema_nullable_required_mismatch,
    _patch_response_schema_id_and_datetimes,
    _patch_missing_pydantic_imports,
    _patch_create_missing_schemas,
    _patch_orm_type_in_route_schemas,
    _patch_list_response_model_mismatch,
)


def _make_project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="schematest_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def _cleanup(root: Path):
    shutil.rmtree(root, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════════
# _patch_schemas_from_attributes
# ══════════════════════════════════════════════════════════════════════════

_TASK_SCHEMA_NO_CONFIG = '''\
from pydantic import BaseModel

class TaskResponse(BaseModel):
    id: int
    title: str
'''


def test_schemas_from_attributes_injects_missing_config():
    root = _make_project({"app/schemas/task.py": _TASK_SCHEMA_NO_CONFIG})
    n = _patch_schemas_from_attributes(root)
    content = (root / "app/schemas/task.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n == 1
    ast.parse(content)
    assert 'model_config = {"from_attributes": True}' in content


def test_schemas_from_attributes_noop_when_already_present():
    schema = _TASK_SCHEMA_NO_CONFIG.replace(
        "    title: str\n", "    title: str\n    model_config = {'from_attributes': True}\n"
    )
    root = _make_project({"app/schemas/task.py": schema})
    n = _patch_schemas_from_attributes(root)
    content = (root / "app/schemas/task.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n == 0
    assert content.count("model_config") == 1


def test_schemas_from_attributes_idempotent():
    root = _make_project({"app/schemas/task.py": _TASK_SCHEMA_NO_CONFIG})
    _patch_schemas_from_attributes(root)
    first = (root / "app/schemas/task.py").read_text(encoding="utf-8")
    n2 = _patch_schemas_from_attributes(root)
    second = (root / "app/schemas/task.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n2 == 0
    assert first == second


def test_schemas_from_attributes_multiple_classes_in_one_file():
    schema = _TASK_SCHEMA_NO_CONFIG + '\nclass TaskCreate(BaseModel):\n    title: str\n'
    root = _make_project({"app/schemas/task.py": schema})
    _patch_schemas_from_attributes(root)
    content = (root / "app/schemas/task.py").read_text(encoding="utf-8")
    _cleanup(root)
    ast.parse(content)
    assert content.count("model_config") == 2


def test_schemas_from_attributes_no_app_dir_is_noop():
    root = Path(tempfile.mkdtemp(prefix="schematest_"))
    assert _patch_schemas_from_attributes(root) == 0
    _cleanup(root)


# ══════════════════════════════════════════════════════════════════════════
# _patch_pydantic_orm_mode  (content: str -> str, no project_path)
# ══════════════════════════════════════════════════════════════════════════

def test_orm_mode_class_config_only_setting_dropped():
    src = (
        "class UserResponse(BaseModel):\n"
        "    id: int\n"
        "    class Config:\n"
        "        orm_mode = True\n"
    )
    out = _patch_pydantic_orm_mode(src)
    assert "orm_mode" not in out
    assert "class Config" not in out
    assert "model_config = {'from_attributes': True}" in out


def test_orm_mode_class_config_with_other_settings_kept():
    src = (
        "class UserResponse(BaseModel):\n"
        "    id: int\n"
        "    class Config:\n"
        "        anystr_strip_whitespace = True\n"
        "        orm_mode = True\n"
    )
    out = _patch_pydantic_orm_mode(src)
    assert "orm_mode" not in out
    assert "anystr_strip_whitespace = True" in out
    assert "model_config = {'from_attributes': True}" in out


def test_orm_mode_bare_assignment_outside_class_config():
    src = "class UserResponse(BaseModel):\n    id: int\n    orm_mode = True\n"
    out = _patch_pydantic_orm_mode(src)
    assert out == "class UserResponse(BaseModel):\n    id: int\n    model_config = {'from_attributes': True}\n"


def test_orm_mode_noop_when_absent():
    src = "class UserResponse(BaseModel):\n    id: int\n"
    assert _patch_pydantic_orm_mode(src) == src


def test_orm_mode_idempotent():
    src = "class UserResponse(BaseModel):\n    class Config:\n        orm_mode = True\n"
    once = _patch_pydantic_orm_mode(src)
    twice = _patch_pydantic_orm_mode(once)
    assert once == twice


def test_orm_mode_multiple_classes():
    src = (
        "class AResponse(BaseModel):\n    class Config:\n        orm_mode = True\n\n"
        "class BResponse(BaseModel):\n    class Config:\n        orm_mode = True\n"
    )
    out = _patch_pydantic_orm_mode(src)
    assert out.count("orm_mode") == 0
    assert out.count("model_config = {'from_attributes': True}") == 2


# ══════════════════════════════════════════════════════════════════════════
# _patch_schema_nullable_required_mismatch  (uses real ast)
# ══════════════════════════════════════════════════════════════════════════

_HABIT_MODEL = '''\
from sqlalchemy import Column, Integer, String, DateTime
from app.database import Base

class Habit(Base):
    __tablename__ = "habits"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    notes = Column(String, nullable=True)
'''

_HABIT_SCHEMA_REQUIRED_NULLABLE = '''\
from pydantic import BaseModel

class HabitResponse(BaseModel):
    id: int
    name: str
    notes: str
'''


def test_nullable_mismatch_fixes_required_field_on_nullable_column():
    root = _make_project({
        "app/models/habit.py": _HABIT_MODEL,
        "app/schemas/habit.py": _HABIT_SCHEMA_REQUIRED_NULLABLE,
    })
    n = _patch_schema_nullable_required_mismatch(root)
    content = (root / "app/schemas/habit.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n == 1
    ast.parse(content)
    assert "notes: Optional[str] = None" in content
    # name maps to a NOT NULL column -- must be left required
    assert "name: str\n" in content


def test_required_create_schema_makes_nullable_model_column_not_null():
    root = _make_project({
        "app/models/habit.py": _HABIT_MODEL,
        "app/schemas/habit.py": "from pydantic import BaseModel\n\nclass HabitCreate(BaseModel):\n    notes: str\n",
    })
    n = _patch_required_create_schema_model_nullability(root)
    content = (root / "app/models/habit.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n == 1
    assert "notes = Column(String, nullable=False)" in content


def test_required_create_schema_fixes_multiline_column_declaration():
    # Regression test: a real generation run left `HabitCreate.name`/
    # `.frequency` required against a nullable model column, unfixed, across
    # every fix attempt in both the initial pass and a full V7 regeneration
    # (2026-07-24). Root cause -- this patcher required the whole
    # `Column(...)` call to be on one line before touching it, but the LLM
    # commonly formats one kwarg per line, so the validator (a plain AST
    # walk, no such restriction) kept reporting the mismatch while this
    # patcher silently no-opped every time.
    model = (
        "from sqlalchemy import Column, Integer, String\n"
        "from app.database import Base\n\n"
        "class Habit(Base):\n"
        "    __tablename__ = \"habits\"\n"
        "    id = Column(Integer, primary_key=True)\n"
        "    name = Column(\n"
        "        String,\n"
        "        nullable=True,\n"
        "    )\n"
    )
    root = _make_project({
        "app/models/habit.py": model,
        "app/schemas/habit.py": "from pydantic import BaseModel\n\nclass HabitCreate(BaseModel):\n    name: str\n",
    })
    n = _patch_required_create_schema_model_nullability(root)
    content = (root / "app/models/habit.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n == 1
    ast.parse(content)
    assert "nullable=False" in content
    assert "nullable=True" not in content


def test_bare_pydantic_from_attributes_is_repaired_but_config_is_preserved():
    root = _make_project({
        "app/schemas/task.py": (
            "from pydantic import BaseModel\n\n"
            "class TaskOut(BaseModel):\n"
            "    from_attributes = True\n"
            "    title: str\n\n"
            "class LegacyOut(BaseModel):\n"
            "    class Config:\n"
            "        from_attributes = True\n"
        ),
    })
    n = _patch_bare_pydantic_from_attributes(root)
    content = (root / "app/schemas/task.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n == 1
    assert 'model_config = {"from_attributes": True}' in content
    assert "class Config:\n        from_attributes = True" in content


def test_pydantic_date_field_type_collision_uses_module_alias():
    root = _make_project({
        "app/schemas/event.py": (
            "from datetime import date\nfrom pydantic import BaseModel\n\n"
            "class EventCreate(BaseModel):\n    date: date\n"
        ),
    })
    n = _patch_pydantic_field_type_name_collisions(root)
    content = (root / "app/schemas/event.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n == 1
    assert "import datetime as _datetime" in content
    assert "date: _datetime.date" in content


def test_pydantic_optional_date_field_type_collision_uses_module_alias():
    """Regression test (freelance_portfolio_manager, 2026-08-01): the
    bare-`date: date` check missed the far more common `Optional[date]`
    shape (an ast.Subscript, not ast.Name) -- every *Update/*Response
    schema wraps fields Optional for partial-update support, so a field
    named `date` almost always appears as `Optional[date]` in practice,
    and that shape crashed with PydanticSchemaGenerationError exactly
    like the unwrapped one, just never got caught."""
    root = _make_project({
        "app/schemas/event.py": (
            "from datetime import date\nfrom pydantic import BaseModel, Field\n"
            "from typing import Optional\n\n"
            "class EventUpdate(BaseModel):\n"
            "    title: Optional[str] = None\n"
            "    date: Optional[date] = Field(default=None)\n\n"
            "class EventResponse(BaseModel):\n"
            "    id: int\n"
            "    date: Optional[date] = None\n"
        ),
    })
    n = _patch_pydantic_field_type_name_collisions(root)
    content = (root / "app/schemas/event.py").read_text(encoding="utf-8")
    assert n == 1
    assert "import datetime as _datetime" in content
    assert content.count("date: Optional[_datetime.date]") == 2
    # Untouched sibling field must survive verbatim.
    assert "title: Optional[str] = None" in content

    # Compile-execute for real -- this is a runtime PydanticSchemaGenerationError,
    # not a SyntaxError, so a parse-only check wouldn't catch a regression.
    import subprocess
    import sys as _sys
    schema_path = root / "app" / "schemas" / "event.py"
    result = subprocess.run(
        [_sys.executable, "-c", f"exec(compile(open(r'{schema_path}').read(), 'event.py', 'exec'))"],
        capture_output=True, text=True,
    )
    _cleanup(root)
    assert result.returncode == 0, f"patched schema still fails to import: {result.stderr}"


def test_pydantic_time_field_type_collision_uses_module_alias():
    """Same collision mechanism as the `date` case, confirmed live with a
    different datetime-module name in the same 50-app batch (running_club,
    classroom_attendance_tracker, 2026-08-01): `time: time = Field(...)`
    raised PydanticUserError for the identical reason. The fix generalized
    from a hardcoded `date`-only check to _DATETIME_COLLISION_NAMES."""
    root = _make_project({
        "app/schemas/session.py": (
            "from datetime import time\nfrom pydantic import BaseModel, Field\n"
            "from typing import Optional\n\n"
            "class SessionCreate(BaseModel):\n"
            "    time: time = Field(...)\n\n"
            "class SessionUpdate(BaseModel):\n"
            "    time: Optional[time] = None\n"
        ),
    })
    n = _patch_pydantic_field_type_name_collisions(root)
    content = (root / "app/schemas/session.py").read_text(encoding="utf-8")
    assert n == 1
    assert "import datetime as _datetime" in content
    assert "time: _datetime.time = Field(...)" in content
    assert "time: Optional[_datetime.time] = None" in content

    import subprocess
    import sys as _sys
    schema_path = root / "app" / "schemas" / "session.py"
    result = subprocess.run(
        [_sys.executable, "-c", f"exec(compile(open(r'{schema_path}').read(), 'session.py', 'exec'))"],
        capture_output=True, text=True,
    )
    _cleanup(root)
    assert result.returncode == 0, f"patched schema still fails to import: {result.stderr}"


def test_nullable_mismatch_noop_when_column_not_nullable():
    schema = "from pydantic import BaseModel\n\nclass HabitResponse(BaseModel):\n    id: int\n    name: str\n"
    root = _make_project({"app/models/habit.py": _HABIT_MODEL, "app/schemas/habit.py": schema})
    n = _patch_schema_nullable_required_mismatch(root)
    _cleanup(root)
    assert n == 0


def test_nullable_mismatch_idempotent():
    root = _make_project({
        "app/models/habit.py": _HABIT_MODEL,
        "app/schemas/habit.py": _HABIT_SCHEMA_REQUIRED_NULLABLE,
    })
    _patch_schema_nullable_required_mismatch(root)
    first = (root / "app/schemas/habit.py").read_text(encoding="utf-8")
    n2 = _patch_schema_nullable_required_mismatch(root)
    second = (root / "app/schemas/habit.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n2 == 0
    assert first == second


def test_nullable_mismatch_malformed_schema_file_does_not_crash():
    root = _make_project({
        "app/models/habit.py": _HABIT_MODEL,
        "app/schemas/habit.py": "class Broken(BaseModel:\n    this is not python\n",
        "app/schemas/ok.py": _HABIT_SCHEMA_REQUIRED_NULLABLE,
    })
    n = _patch_schema_nullable_required_mismatch(root)
    _cleanup(root)
    # Must not raise. The valid file should still get fixed.
    assert n == 1


def test_nullable_mismatch_malformed_model_file_does_not_crash():
    root = _make_project({
        "app/models/broken.py": "class Broken(Base:\n  not python\n",
        "app/models/habit.py": _HABIT_MODEL,
        "app/schemas/habit.py": _HABIT_SCHEMA_REQUIRED_NULLABLE,
    })
    n = _patch_schema_nullable_required_mismatch(root)
    _cleanup(root)
    assert n == 1


def test_nullable_mismatch_multiline_annotation_skipped_not_crashed():
    schema = (
        "from pydantic import BaseModel\n\n"
        "class HabitResponse(BaseModel):\n"
        "    id: int\n"
        "    notes: (\n"
        "        str\n"
        "    )\n"
    )
    root = _make_project({"app/models/habit.py": _HABIT_MODEL, "app/schemas/habit.py": schema})
    n = _patch_schema_nullable_required_mismatch(root)
    _cleanup(root)
    # Docstring: multi-line annotations are "too risky to rewrite textually" -- skipped.
    assert n == 0


# ══════════════════════════════════════════════════════════════════════════
# _patch_response_schema_id_and_datetimes  (uses real ast)
# ══════════════════════════════════════════════════════════════════════════

_EVENT_MODEL = '''\
from sqlalchemy import Column, Integer, String, DateTime
from app.database import Base

class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    created_at = Column(DateTime)
'''


def test_id_and_datetimes_injects_missing_id():
    schema = "from pydantic import BaseModel\n\nclass EventResponse(BaseModel):\n    title: str\n"
    root = _make_project({"app/models/event.py": _EVENT_MODEL, "app/schemas/event.py": schema})
    n = _patch_response_schema_id_and_datetimes(root)
    content = (root / "app/schemas/event.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n == 1
    ast.parse(content)
    assert "id: Optional[int] = None" in content


def test_id_and_datetimes_retypes_wrong_datetime_family():
    schema = (
        "from datetime import date\nfrom pydantic import BaseModel\n\n"
        "class EventResponse(BaseModel):\n    id: int\n    created_at: date\n"
    )
    root = _make_project({"app/models/event.py": _EVENT_MODEL, "app/schemas/event.py": schema})
    n = _patch_response_schema_id_and_datetimes(root)
    content = (root / "app/schemas/event.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n == 1
    ast.parse(content)
    assert "created_at: datetime" in content


def test_id_and_datetimes_noop_when_already_correct():
    schema = (
        "from datetime import datetime\nfrom pydantic import BaseModel\n\n"
        "class EventResponse(BaseModel):\n    id: int\n    created_at: datetime\n"
    )
    root = _make_project({"app/models/event.py": _EVENT_MODEL, "app/schemas/event.py": schema})
    n = _patch_response_schema_id_and_datetimes(root)
    _cleanup(root)
    assert n == 0


def test_id_and_datetimes_idempotent():
    schema = "from pydantic import BaseModel\n\nclass EventResponse(BaseModel):\n    title: str\n"
    root = _make_project({"app/models/event.py": _EVENT_MODEL, "app/schemas/event.py": schema})
    _patch_response_schema_id_and_datetimes(root)
    first = (root / "app/schemas/event.py").read_text(encoding="utf-8")
    n2 = _patch_response_schema_id_and_datetimes(root)
    second = (root / "app/schemas/event.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n2 == 0
    assert first == second


def test_id_and_datetimes_malformed_schema_does_not_crash():
    root = _make_project({
        "app/models/event.py": _EVENT_MODEL,
        "app/schemas/broken.py": "not( valid python::\n",
    })
    n = _patch_response_schema_id_and_datetimes(root)
    _cleanup(root)
    assert n == 0  # nothing valid to fix, but must not raise


# ══════════════════════════════════════════════════════════════════════════
# _patch_missing_pydantic_imports
# ══════════════════════════════════════════════════════════════════════════

def test_missing_pydantic_imports_adds_missing_symbols():
    schema = "class TaskResponse(BaseModel):\n    id: Optional[int]\n    created_at: datetime\n"
    root = _make_project({"app/schemas/task.py": schema})
    n = _patch_missing_pydantic_imports(root)
    content = (root / "app/schemas/task.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n == 1
    assert "from pydantic import BaseModel" in content
    assert "from typing import Optional" in content
    assert "from datetime import datetime" in content
    ast.parse(content)


def test_missing_pydantic_imports_merges_into_existing_import_line():
    schema = "from typing import Optional\nfrom pydantic import BaseModel\n\nclass TaskResponse(BaseModel):\n    id: Optional[int]\n    tags: List[str]\n"
    root = _make_project({"app/schemas/task.py": schema})
    n = _patch_missing_pydantic_imports(root)
    content = (root / "app/schemas/task.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n == 1
    assert content.count("from typing import") == 1
    assert "List" in content.split("from typing import")[1].split("\n")[0]


def test_missing_pydantic_imports_noop_when_all_present():
    schema = "from pydantic import BaseModel\n\nclass TaskResponse(BaseModel):\n    id: int\n"
    root = _make_project({"app/schemas/task.py": schema})
    n = _patch_missing_pydantic_imports(root)
    _cleanup(root)
    assert n == 0


def test_missing_pydantic_imports_idempotent():
    schema = "class TaskResponse(BaseModel):\n    id: Optional[int]\n"
    root = _make_project({"app/schemas/task.py": schema})
    _patch_missing_pydantic_imports(root)
    first = (root / "app/schemas/task.py").read_text(encoding="utf-8")
    n2 = _patch_missing_pydantic_imports(root)
    second = (root / "app/schemas/task.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n2 == 0
    assert first == second


# ══════════════════════════════════════════════════════════════════════════
# _patch_create_missing_schemas
# ══════════════════════════════════════════════════════════════════════════

_STATS_ROUTE_IMPORTING_MISSING_SCHEMA = '''\
from fastapi import APIRouter
from app.schemas.stats import StatsResponse

stats_router = APIRouter()

@stats_router.get("/stats", response_model=StatsResponse)
def get_stats():
    return {"total": 5, "completed": 2}
'''

_TASK_MODEL_FOR_STUB = '''\
from sqlalchemy import Column, Integer, String
from app.database import Base

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
'''


def test_create_missing_schemas_creates_stub_file():
    root = _make_project({"app/routes/stats.py": _STATS_ROUTE_IMPORTING_MISSING_SCHEMA, "app/schemas/__init__.py": ""})
    n = _patch_create_missing_schemas(root)
    stub = root / "app/schemas/stats.py"
    assert n == 1
    assert stub.exists()
    content = stub.read_text(encoding="utf-8")
    _cleanup(root)
    ast.parse(content)
    assert "class StatsResponse(BaseModel):" in content
    # No matching model -- falls back to _infer_fields_from_route_return,
    # which should find total/completed from the dict return.
    assert "total" in content and "completed" in content


def test_create_missing_schemas_uses_model_columns_when_matching_model_exists():
    route = _STATS_ROUTE_IMPORTING_MISSING_SCHEMA.replace(
        "app.schemas.stats", "app.schemas.task"
    ).replace("StatsResponse", "TaskResponse").replace(
        'return {"total": 5, "completed": 2}', 'return {"id": 1, "title": "x"}'
    )
    root = _make_project({
        "app/routes/task.py": route,
        "app/models/task.py": _TASK_MODEL_FOR_STUB,
        "app/schemas/__init__.py": "",
    })
    n = _patch_create_missing_schemas(root)
    content = (root / "app/schemas/task.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n == 1
    ast.parse(content)
    assert "id: Optional[int] = None" in content
    assert "title: Optional[str] = None" in content


def test_create_missing_schemas_no_duplicate_when_class_defined_elsewhere():
    """A class already defined in a DIFFERENT schema file must never get a
    second, incompatible stub -- direct regression coverage for the
    duplicate-class bug this function's own docstring describes."""
    route = _STATS_ROUTE_IMPORTING_MISSING_SCHEMA.replace(
        "app.schemas.stats", "app.schemas.workout"
    ).replace("StatsResponse", "ExerciseCreate")
    root = _make_project({
        "app/routes/workout.py": route,
        "app/schemas/exercise.py": "from pydantic import BaseModel\n\nclass ExerciseCreate(BaseModel):\n    name: str\n",
    })
    n = _patch_create_missing_schemas(root)
    workout_stub = root / "app/schemas/workout.py"
    _cleanup(root)
    assert n == 0
    assert not workout_stub.exists()


def test_create_missing_schemas_appends_to_existing_file_without_duplicating():
    route = _STATS_ROUTE_IMPORTING_MISSING_SCHEMA.replace("StatsResponse", "StatsResponse, StatsDetail")
    root = _make_project({
        "app/routes/stats.py": route,
        "app/schemas/stats.py": "from pydantic import BaseModel\n\nclass StatsResponse(BaseModel):\n    total: int\n",
    })
    n = _patch_create_missing_schemas(root)
    content = (root / "app/schemas/stats.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n == 1
    ast.parse(content)
    assert content.count("class StatsResponse") == 1
    assert "class StatsDetail" in content


def test_create_missing_schemas_idempotent():
    root = _make_project({"app/routes/stats.py": _STATS_ROUTE_IMPORTING_MISSING_SCHEMA, "app/schemas/__init__.py": ""})
    _patch_create_missing_schemas(root)
    first = (root / "app/schemas/stats.py").read_text(encoding="utf-8")
    n2 = _patch_create_missing_schemas(root)
    second = (root / "app/schemas/stats.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n2 == 0
    assert first == second


def test_create_missing_schemas_noop_without_routes_or_schemas_dir():
    root = Path(tempfile.mkdtemp(prefix="schematest_"))
    assert _patch_create_missing_schemas(root) == 0
    _cleanup(root)


# ══════════════════════════════════════════════════════════════════════════
# _patch_orm_type_in_route_schemas
# ══════════════════════════════════════════════════════════════════════════

_LABEL_MODEL = "from sqlalchemy import Column, Integer, String\nfrom app.database import Base\n\nclass Label(Base):\n    __tablename__ = 'labels'\n    id = Column(Integer, primary_key=True)\n"

_ROUTE_WITH_INLINE_ORM_TYPE = '''\
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List
from app.models.label import Label

task_router = APIRouter()

class TaskOut(BaseModel):
    id: int
    labels: List[Label]
'''


def test_orm_type_in_route_schemas_rewrites_list_field():
    root = _make_project({
        "app/models/label.py": _LABEL_MODEL,
        "app/routes/task.py": _ROUTE_WITH_INLINE_ORM_TYPE,
    })
    n = _patch_orm_type_in_route_schemas(root)
    content = (root / "app/routes/task.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n == 1
    ast.parse(content)
    assert "List[Any]" in content
    assert "List[Label]" not in content
    assert "from typing import Any" in content or ", Any" in content


def test_orm_type_in_route_schemas_rewrites_optional_and_bare_field():
    route = (
        "from fastapi import APIRouter\nfrom pydantic import BaseModel\nfrom typing import Optional\n"
        "from app.models.label import Label\n\ntask_router = APIRouter()\n\n"
        "class TaskOut(BaseModel):\n    id: int\n    primary_label: Optional[Label]\n    owner_label: Label\n"
    )
    root = _make_project({"app/models/label.py": _LABEL_MODEL, "app/routes/task.py": route})
    n = _patch_orm_type_in_route_schemas(root)
    content = (root / "app/routes/task.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n == 1
    assert "Optional[Any]" in content
    assert re.search(r"owner_label\s*:\s*Any", content)


def test_orm_type_in_route_schemas_noop_when_no_pydantic_class():
    route = "from fastapi import APIRouter\nfrom app.models.label import Label\n\ntask_router = APIRouter()\n\n@task_router.get('/x')\ndef x():\n    return []\n"
    root = _make_project({"app/models/label.py": _LABEL_MODEL, "app/routes/task.py": route})
    n = _patch_orm_type_in_route_schemas(root)
    _cleanup(root)
    assert n == 0


def test_orm_type_in_route_schemas_replaces_orm_list_response_model_without_local_schema():
    route = (
        "from fastapi import APIRouter\n"
        "from app.models.label import Label\n"
        "from app.schemas.label import LabelResponse\n\n"
        "router = APIRouter()\n\n"
        "@router.get('/labels', response_model=list[Label])\n"
        "def labels():\n    return []\n"
    )
    root = _make_project({"app/models/label.py": _LABEL_MODEL, "app/routes/label.py": route})
    n = _patch_orm_type_in_route_schemas(root)
    content = (root / "app/routes/label.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n == 1
    assert "response_model=list[LabelResponse]" in content
    assert "response_model=list[Label]" not in content


def test_orm_type_in_route_schemas_idempotent():
    root = _make_project({
        "app/models/label.py": _LABEL_MODEL,
        "app/routes/task.py": _ROUTE_WITH_INLINE_ORM_TYPE,
    })
    _patch_orm_type_in_route_schemas(root)
    first = (root / "app/routes/task.py").read_text(encoding="utf-8")
    n2 = _patch_orm_type_in_route_schemas(root)
    second = (root / "app/routes/task.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n2 == 0
    assert first == second


# ══════════════════════════════════════════════════════════════════════════
# _patch_list_response_model_mismatch
# ══════════════════════════════════════════════════════════════════════════

_ROUTE_WITH_LIST_ITEMS_MISMATCH = '''\
from fastapi import APIRouter
from typing import List
from app.schemas.task import TaskResponse

task_router = APIRouter()

@task_router.get("/tasks", response_model=List[TaskResponse])
def list_tasks():
    items = []
    return {"items": items, "total": 0}
'''


def test_list_response_model_mismatch_strips_response_model():
    root = _make_project({"app/routes/task.py": _ROUTE_WITH_LIST_ITEMS_MISMATCH})
    n = _patch_list_response_model_mismatch(root)
    content = (root / "app/routes/task.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n == 1
    assert "response_model=List[" not in content
    assert "@task_router.get(\"/tasks\")" in content or "@task_router.get(\"/tasks\" )" in content


def test_list_response_model_mismatch_noop_when_returning_a_real_list():
    route = _ROUTE_WITH_LIST_ITEMS_MISMATCH.replace('return {"items": items, "total": 0}', "return items")
    root = _make_project({"app/routes/task.py": route})
    n = _patch_list_response_model_mismatch(root)
    _cleanup(root)
    assert n == 0


def test_list_response_model_mismatch_idempotent():
    root = _make_project({"app/routes/task.py": _ROUTE_WITH_LIST_ITEMS_MISMATCH})
    _patch_list_response_model_mismatch(root)
    first = (root / "app/routes/task.py").read_text(encoding="utf-8")
    n2 = _patch_list_response_model_mismatch(root)
    second = (root / "app/routes/task.py").read_text(encoding="utf-8")
    _cleanup(root)
    assert n2 == 0
    assert first == second


def test_list_response_model_mismatch_noop_no_routes_dir():
    root = Path(tempfile.mkdtemp(prefix="schematest_"))
    assert _patch_list_response_model_mismatch(root) == 0
    _cleanup(root)


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
