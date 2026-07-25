"""
Experiment 052: regression tests for database_patcher.py's 8 functions and
the relationship/FK family in deterministic_patcher.py (5 functions) --
Priority 1 (Critical) per the audit in docs/REPAIR_DEBT.md (Experiment 051),
which found these among the 106 of 114 repair functions with zero test
coverage.

Every test validates EXISTING behavior (read from source, not guessed).
No repair logic was modified to write these tests.

Note on _patch_relationship_string_aliases: confirmed in Experiment 051's
audit that in the LIVE pipeline (run_deterministic_patches), this function's
search target is unconditionally eliminated by _patch_strip_relationships
running earlier in the same call -- every relationship() call is stripped
before this function ever runs, so it never finds anything to fix in
practice. It is still tested here in isolation, because it is a real,
independently-correct function when called on its own (which is what a
unit test does) -- the dead-path issue is a pipeline-ordering property, not
a defect in the function itself.

Run directly: python tests/reliability/test_database_patcher_and_relationships.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services import database_patcher as dbp
from app.services.deterministic_patcher import (
    _patch_strip_back_populates,
    _patch_strip_relationships,
    _patch_dangling_foreign_keys,
    _patch_model_aliases,
    _patch_relationship_string_aliases,
    _patch_models_without_primary_key,
)


# ─── fixture helpers ──────────────────────────────────────────────────────

def _mk_project(tmp_path, models=None, routes=None, schemas=None, main_py=None):
    proj = Path(tmp_path)
    (proj / "app" / "models").mkdir(parents=True, exist_ok=True)
    (proj / "app" / "routes").mkdir(parents=True, exist_ok=True)
    (proj / "app" / "schemas").mkdir(parents=True, exist_ok=True)
    for name, content in (models or {}).items():
        (proj / "app" / "models" / name).write_text(content, encoding="utf-8")
    for name, content in (routes or {}).items():
        (proj / "app" / "routes" / name).write_text(content, encoding="utf-8")
    for name, content in (schemas or {}).items():
        (proj / "app" / "schemas" / name).write_text(content, encoding="utf-8")
    if main_py is not None:
        (proj / "app" / "main.py").write_text(main_py, encoding="utf-8")
    return proj


# ─── patch_database_py ────────────────────────────────────────────────────

def test_patch_database_py_writes_known_good_template():
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(td, main_py="from fastapi import FastAPI\napp = FastAPI()\n")
        ok = dbp.patch_database_py(str(proj))
        assert ok is True
        content = (proj / "app" / "database.py").read_text(encoding="utf-8")
        assert "def get_db" in content
        assert "create_tables" in content


def test_patch_database_py_false_when_app_dir_missing():
    with tempfile.TemporaryDirectory() as td:
        # No app/ dir created at all.
        ok = dbp.patch_database_py(td)
        assert ok is False


def test_patch_database_py_idempotent():
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(td, main_py="app = None\n")
        dbp.patch_database_py(str(proj))
        first = (proj / "app" / "database.py").read_text(encoding="utf-8")
        dbp.patch_database_py(str(proj))
        second = (proj / "app" / "database.py").read_text(encoding="utf-8")
        assert first == second


# ─── _patch_main_py_duplicate_engine ──────────────────────────────────────

def test_patch_main_py_duplicate_engine_strips_and_redirects():
    with tempfile.TemporaryDirectory() as td:
        main_src = (
            "from sqlalchemy import create_engine\n"
            "from sqlalchemy.orm import sessionmaker\n"
            "engine = create_engine(settings.DATABASE_URL)\n"
            "SessionLocal = sessionmaker(bind=engine)\n"
        )
        proj = _mk_project(td, main_py=main_src)
        dbp._patch_main_py_duplicate_engine(proj / "app")
        out = (proj / "app" / "main.py").read_text(encoding="utf-8")
        assert "from app.database import engine" in out
        assert "from app.database import SessionLocal" in out
        assert "create_engine(settings.DATABASE_URL)" not in out


def test_patch_main_py_duplicate_engine_noop_when_absent():
    with tempfile.TemporaryDirectory() as td:
        main_src = "from app.database import engine, SessionLocal\napp = None\n"
        proj = _mk_project(td, main_py=main_src)
        dbp._patch_main_py_duplicate_engine(proj / "app")
        assert (proj / "app" / "main.py").read_text(encoding="utf-8") == main_src


def test_patch_main_py_duplicate_engine_missing_file_safe():
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(td)  # no main.py written
        dbp._patch_main_py_duplicate_engine(proj / "app")  # must not raise


# ─── _patch_main_py_create_all ────────────────────────────────────────────

def test_patch_main_py_create_all_rewrites_call():
    with tempfile.TemporaryDirectory() as td:
        main_src = "from app.database import engine\nBase.metadata.create_all(bind=engine)\n"
        proj = _mk_project(td, main_py=main_src)
        dbp._patch_main_py_create_all(proj / "app")
        out = (proj / "app" / "main.py").read_text(encoding="utf-8")
        assert "create_tables()" in out
        assert "Base.metadata.create_all" not in out
        assert "create_tables" in out.splitlines()[0]  # merged into the existing import


def test_patch_main_py_create_all_idempotent():
    with tempfile.TemporaryDirectory() as td:
        main_src = "from app.database import engine\nBase.metadata.create_all(bind=engine)\n"
        proj = _mk_project(td, main_py=main_src)
        dbp._patch_main_py_create_all(proj / "app")
        first = (proj / "app" / "main.py").read_text(encoding="utf-8")
        dbp._patch_main_py_create_all(proj / "app")
        second = (proj / "app" / "main.py").read_text(encoding="utf-8")
        assert first == second


# ─── patch_model_field_mismatches ─────────────────────────────────────────

_TODO_MODEL = (
    "from app.database import Base\n"
    "from sqlalchemy import Column, Integer, String\n\n"
    "class Todo(Base):\n"
    "    __tablename__ = 'todos'\n"
    "    id = Column(Integer, primary_key=True)\n"
    "    name = Column(String)\n"
)

_TODO_ROUTE_WITH_MISMATCH = (
    "from app.models.todo import Todo\n\n"
    "def create_todo(todo_in):\n"
    "    todo = Todo(title=todo_in.title, description=todo_in.description)\n"
    "    return todo\n"
)


def test_patch_model_field_mismatches_renames_to_synonym():
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(
            td,
            models={"todo.py": _TODO_MODEL},
            routes={"todo_routes.py": _TODO_ROUTE_WITH_MISMATCH},
        )
        n = dbp.patch_model_field_mismatches(str(proj))
        assert n == 1
        out = (proj / "app" / "routes" / "todo_routes.py").read_text(encoding="utf-8")
        # "title" -> synonym "name" (real column); "description" has no
        # matching column on this model and no synonym present -> dropped
        # entirely (real behavior, not guessed -- verified by running this).
        assert "name=todo_in.title" in out


def test_patch_model_field_mismatches_noop_on_valid_fields():
    with tempfile.TemporaryDirectory() as td:
        route = "from app.models.todo import Todo\n\ndef f(x):\n    return Todo(name=x.name)\n"
        proj = _mk_project(td, models={"todo.py": _TODO_MODEL}, routes={"todo_routes.py": route})
        n = dbp.patch_model_field_mismatches(str(proj))
        assert n == 0
        assert (proj / "app" / "routes" / "todo_routes.py").read_text(encoding="utf-8") == route


def test_patch_model_field_mismatches_missing_dirs_returns_zero():
    with tempfile.TemporaryDirectory() as td:
        assert dbp.patch_model_field_mismatches(td) == 0


def test_patch_model_field_mismatches_class_attr_query_rename():
    with tempfile.TemporaryDirectory() as td:
        route = (
            "from app.models.todo import Todo\n\n"
            "def f(db):\n    return db.query(Todo).filter(Todo.title == 'x').all()\n"
        )
        proj = _mk_project(td, models={"todo.py": _TODO_MODEL}, routes={"todo_routes.py": route})
        dbp.patch_model_field_mismatches(str(proj))
        out = (proj / "app" / "routes" / "todo_routes.py").read_text(encoding="utf-8")
        assert "Todo.name" in out


# ─── patch_add_missing_model_columns ──────────────────────────────────────

def test_patch_add_missing_model_columns_adds_boolean_column():
    with tempfile.TemporaryDirectory() as td:
        route = "from app.models.todo import Todo\n\ndef f():\n    return Todo(name='x', completed=True)\n"
        proj = _mk_project(td, models={"todo.py": _TODO_MODEL}, routes={"todo_routes.py": route})
        n = dbp.patch_add_missing_model_columns(str(proj))
        assert n == 1
        model_out = (proj / "app" / "models" / "todo.py").read_text(encoding="utf-8")
        assert "completed" in model_out
        assert "Boolean" in model_out


def test_patch_add_missing_model_columns_ordering_after_field_mismatches():
    """Docstring: 'Must run AFTER patch_model_field_mismatches -- any field
    still not on the model at that point has no synonym and is a genuine
    gap, not a rename.' Verify running mismatches first correctly avoids
    inventing a redundant column for a field that actually has a synonym."""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(
            td,
            models={"todo.py": _TODO_MODEL},
            routes={"todo_routes.py": _TODO_ROUTE_WITH_MISMATCH},  # title=... (has synonym "name")
        )
        dbp.patch_model_field_mismatches(str(proj))  # run first, as documented
        n = dbp.patch_add_missing_model_columns(str(proj))
        model_out = (proj / "app" / "models" / "todo.py").read_text(encoding="utf-8")
        # "title" was already renamed to "name" by the prior pass, so no
        # bogus "title" column should be invented.
        assert "title = Column" not in model_out


def test_patch_add_missing_model_columns_noop_missing_dirs():
    with tempfile.TemporaryDirectory() as td:
        assert dbp.patch_add_missing_model_columns(td) == 0


# ─── patch_missing_required_constructor_kwargs ────────────────────────────

_HABIT_MODEL = (
    "from app.database import Base\n"
    "from sqlalchemy import Column, Integer, Date\n\n"
    "class HabitCompletion(Base):\n"
    "    __tablename__ = 'habit_completions'\n"
    "    id = Column(Integer, primary_key=True)\n"
    "    habit_id = Column(Integer)\n"
    "    completion_date = Column(Date, nullable=False)\n"
)

_HABIT_ROUTE_MISSING_REQUIRED = (
    "from app.models.habit_completion import HabitCompletion\n\n"
    "def complete_habit(habit, current_user):\n"
    "    hc = HabitCompletion(habit_id=habit.id, user_id=current_user.id)\n"
    "    return hc\n"
)


def test_patch_missing_required_constructor_kwargs_injects_date_default():
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(
            td, models={"habit_completion.py": _HABIT_MODEL},
            routes={"habit_routes.py": _HABIT_ROUTE_MISSING_REQUIRED},
        )
        n = dbp.patch_missing_required_constructor_kwargs(str(proj))
        assert n == 1
        out = (proj / "app" / "routes" / "habit_routes.py").read_text(encoding="utf-8")
        assert "completion_date=" in out


def test_patch_missing_required_constructor_kwargs_noop_when_already_present():
    with tempfile.TemporaryDirectory() as td:
        route = (
            "from app.models.habit_completion import HabitCompletion\n\n"
            "def f():\n    return HabitCompletion(habit_id=1, completion_date=today)\n"
        )
        proj = _mk_project(td, models={"habit_completion.py": _HABIT_MODEL}, routes={"habit_routes.py": route})
        n = dbp.patch_missing_required_constructor_kwargs(str(proj))
        assert n == 0


# ─── patch_string_date_literals_in_constructors ───────────────────────────

_PROGRESS_LOG_MODEL = (
    "from sqlalchemy import Column, Integer, Date, Boolean, ForeignKey\n"
    "from app.database import Base\n\n"
    "class ProgressLog(Base):\n"
    "    __tablename__ = 'progress_logs'\n"
    "    id = Column(Integer, primary_key=True, nullable=False)\n"
    "    date = Column(Date, nullable=False)\n"
    "    habit_id = Column(Integer, ForeignKey('habits.id'), nullable=False)\n"
    "    completed = Column(Boolean, nullable=False)\n"
)

_EVENT_MODEL = (
    "from sqlalchemy import Column, Integer, DateTime, String\n"
    "from app.database import Base\n\n"
    "class Event(Base):\n"
    "    __tablename__ = 'events'\n"
    "    id = Column(Integer, primary_key=True)\n"
    "    title = Column(String)\n"
    "    created_at = Column(DateTime, nullable=False)\n"
)


def test_patch_string_date_literals_converts_iso_string_to_date_object():
    # Exact live shape (habit_tracker, 2026-07-25): seed_routes.py did
    # ProgressLog(date="2023-10-01", completed=True) against a
    # Column(Date, nullable=False) -- SQLite's dialect raises a
    # StatementError (not IntegrityError) on the raw string, an uncaught
    # crash the constructor call's own try/except IntegrityError can't catch.
    route = (
        "from app.models.progress_logs import ProgressLog\n\n"
        "progress_logs = [\n"
        "    ProgressLog(date=\"2023-10-01\", completed=True),\n"
        "    ProgressLog(date=\"2023-10-02\", completed=False),\n"
        "]\n"
    )
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(td, models={"progress_logs.py": _PROGRESS_LOG_MODEL}, routes={"seed_routes.py": route})
        n = dbp.patch_string_date_literals_in_constructors(str(proj))
        out = (proj / "app" / "routes" / "seed_routes.py").read_text(encoding="utf-8")
        assert n == 1
        assert "date=date(2023, 10, 1)" in out
        assert "date=date(2023, 10, 2)" in out
        assert "from datetime import date" in out
        import ast
        ast.parse(out)


def test_patch_string_date_literals_handles_datetime_with_time_component():
    route = (
        "from app.models.events import Event\n\n"
        "events = [Event(title=\"x\", created_at=\"2023-10-01T14:30:00\")]\n"
    )
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(td, models={"events.py": _EVENT_MODEL}, routes={"seed_routes.py": route})
        n = dbp.patch_string_date_literals_in_constructors(str(proj))
        out = (proj / "app" / "routes" / "seed_routes.py").read_text(encoding="utf-8")
        assert n == 1
        assert "created_at=datetime(2023, 10, 1, 14, 30, 0)" in out
        assert "from datetime import datetime" in out


def test_patch_string_date_literals_noop_when_already_real_object():
    route = (
        "from datetime import datetime\n"
        "from app.models.events import Event\n\n"
        "events = [Event(title=\"x\", created_at=datetime(2023, 10, 1))]\n"
    )
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(td, models={"events.py": _EVENT_MODEL}, routes={"seed_routes.py": route})
        n = dbp.patch_string_date_literals_in_constructors(str(proj))
        out = (proj / "app" / "routes" / "seed_routes.py").read_text(encoding="utf-8")
        assert n == 0
        assert out == route


def test_patch_string_date_literals_never_touches_unrelated_string_field():
    # A date-shaped string in a plain String column (not the Date/DateTime
    # column) must never be rewritten -- only known Date/DateTime columns
    # are in scope.
    route = (
        "from app.models.events import Event\n\n"
        "events = [Event(title=\"2023-10-01\", created_at=None)]\n"
    )
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(td, models={"events.py": _EVENT_MODEL}, routes={"seed_routes.py": route})
        n = dbp.patch_string_date_literals_in_constructors(str(proj))
        out = (proj / "app" / "routes" / "seed_routes.py").read_text(encoding="utf-8")
        assert n == 0
        assert out == route


def test_patch_string_date_literals_no_op_without_models_or_routes_dir():
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        assert dbp.patch_string_date_literals_in_constructors(str(proj)) == 0


def test_patch_string_date_literals_idempotent():
    route = (
        "from app.models.progress_logs import ProgressLog\n\n"
        "progress_logs = [ProgressLog(date=\"2023-10-01\", completed=True)]\n"
    )
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(td, models={"progress_logs.py": _PROGRESS_LOG_MODEL}, routes={"seed_routes.py": route})
        dbp.patch_string_date_literals_in_constructors(str(proj))
        first = (proj / "app" / "routes" / "seed_routes.py").read_text(encoding="utf-8")
        n2 = dbp.patch_string_date_literals_in_constructors(str(proj))
        second = (proj / "app" / "routes" / "seed_routes.py").read_text(encoding="utf-8")
        assert n2 == 0
        assert first == second


# ─── patch_filter_dict_unpack_constructor_kwargs ──────────────────────────

def test_patch_filter_dict_unpack_wraps_bare_star_unpack():
    with tempfile.TemporaryDirectory() as td:
        route = (
            "from app.models.todo import Todo\n\n"
            "def seed(db, habit_data):\n"
            "    for row in habit_data:\n"
            "        t = Todo(**row)\n"
        )
        proj = _mk_project(td, models={"todo.py": _TODO_MODEL}, routes={"todo_routes.py": route})
        n = dbp.patch_filter_dict_unpack_constructor_kwargs(str(proj))
        assert n == 1
        out = (proj / "app" / "routes" / "todo_routes.py").read_text(encoding="utf-8")
        assert "__table__.columns.keys()" in out


def test_patch_filter_dict_unpack_idempotent_does_not_double_wrap():
    with tempfile.TemporaryDirectory() as td:
        route = "from app.models.todo import Todo\n\ndef f(row):\n    return Todo(**row)\n"
        proj = _mk_project(td, models={"todo.py": _TODO_MODEL}, routes={"todo_routes.py": route})
        dbp.patch_filter_dict_unpack_constructor_kwargs(str(proj))
        first = (proj / "app" / "routes" / "todo_routes.py").read_text(encoding="utf-8")
        n2 = dbp.patch_filter_dict_unpack_constructor_kwargs(str(proj))
        second = (proj / "app" / "routes" / "todo_routes.py").read_text(encoding="utf-8")
        # Docstring claims this is naturally idempotent: already-filtered
        # `**{k: v for ...}` doesn't match the bare-identifier regex.
        assert n2 == 0
        assert first == second


def test_patch_filter_dict_unpack_noop_on_dict_comprehension():
    with tempfile.TemporaryDirectory() as td:
        route = (
            "from app.models.todo import Todo\n\n"
            "def f(row):\n    return Todo(**{k: v for k, v in row.items() if k in Todo.__table__.columns.keys()})\n"
        )
        proj = _mk_project(td, models={"todo.py": _TODO_MODEL}, routes={"todo_routes.py": route})
        n = dbp.patch_filter_dict_unpack_constructor_kwargs(str(proj))
        assert n == 0


# ─── patch_add_missing_schema_fields ──────────────────────────────────────

_HABIT_SCHEMA = (
    "from pydantic import BaseModel\n\n"
    "class HabitCreate(BaseModel):\n"
    "    name: str\n"
)

_HABIT_SCHEMA_ROUTE = (
    "from app.schemas.habit import HabitCreate\n\n"
    "def f(habit_in: HabitCreate):\n"
    "    return habit_in.target_unit\n"
)


def test_patch_add_missing_schema_fields_adds_optional_field():
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(
            td, schemas={"habit.py": _HABIT_SCHEMA}, routes={"habit_routes.py": _HABIT_SCHEMA_ROUTE},
        )
        n = dbp.patch_add_missing_schema_fields(str(proj))
        assert n == 1
        out = (proj / "app" / "schemas" / "habit.py").read_text(encoding="utf-8")
        assert "target_unit" in out
        assert "Optional[" in out  # never introduces a new required field


def test_patch_add_missing_schema_fields_missing_dirs_returns_zero():
    with tempfile.TemporaryDirectory() as td:
        assert dbp.patch_add_missing_schema_fields(td) == 0


# ─── _patch_strip_back_populates ──────────────────────────────────────────

_MODEL_WITH_BACK_POPULATES = (
    "from app.database import Base\n"
    "from sqlalchemy import Column, Integer, relationship\n\n"
    "class Habit(Base):\n"
    "    __tablename__ = 'habits'\n"
    "    id = Column(Integer, primary_key=True)\n"
    "    completions = relationship('HabitCompletion', back_populates='habit')\n"
)


def test_patch_strip_back_populates_removes_kwarg_keeps_relationship():
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(td, models={"habit.py": _MODEL_WITH_BACK_POPULATES})
        n = _patch_strip_back_populates(proj)
        assert n == 1
        out = (proj / "app" / "models" / "habit.py").read_text(encoding="utf-8")
        assert "back_populates" not in out
        assert "relationship(" in out  # only the kwarg is stripped, per its own scope


def test_patch_strip_back_populates_noop_when_absent():
    with tempfile.TemporaryDirectory() as td:
        model = "from app.database import Base\nclass X(Base):\n    __tablename__ = 'x'\n"
        proj = _mk_project(td, models={"x.py": model})
        n = _patch_strip_back_populates(proj)
        assert n == 0


def test_patch_strip_back_populates_multiple_occurrences_in_one_file():
    with tempfile.TemporaryDirectory() as td:
        model = (
            "from app.database import Base\n"
            "from sqlalchemy import Column, Integer, relationship\n\n"
            "class A(Base):\n"
            "    __tablename__ = 'a'\n"
            "    id = Column(Integer, primary_key=True)\n"
            "    b = relationship('B', back_populates='a', backref='a_backref')\n"
            "    c = relationship('C', back_populates='a2')\n"
        )
        proj = _mk_project(td, models={"a.py": model})
        _patch_strip_back_populates(proj)
        out = (proj / "app" / "models" / "a.py").read_text(encoding="utf-8")
        assert "back_populates" not in out
        assert "backref" not in out


# ─── _patch_strip_relationships ───────────────────────────────────────────

_HABIT_MODEL_WITH_REL = (
    "from app.database import Base\n"
    "from sqlalchemy import Column, Integer, ForeignKey, relationship\n\n"
    "class Habit(Base):\n"
    "    __tablename__ = 'habits'\n"
    "    id = Column(Integer, primary_key=True)\n"
    "    completions = relationship('HabitCompletion')\n"
)

_HABIT_COMPLETION_MODEL = (
    "from app.database import Base\n"
    "from sqlalchemy import Column, Integer, ForeignKey\n\n"
    "class HabitCompletion(Base):\n"
    "    __tablename__ = 'habit_completions'\n"
    "    id = Column(Integer, primary_key=True)\n"
    "    habit_id = Column(Integer, ForeignKey('habits.id'))\n"
)


def test_patch_strip_relationships_removes_call_and_injects_property():
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(
            td, models={"habit.py": _HABIT_MODEL_WITH_REL, "habit_completion.py": _HABIT_COMPLETION_MODEL},
        )
        n = _patch_strip_relationships(proj)
        assert n == 1
        out = (proj / "app" / "models" / "habit.py").read_text(encoding="utf-8")
        assert "relationship(" not in out
        assert "@builtins.property" in out
        assert "def completions(self)" in out
        # one-to-many direction (target holds the FK back) -> query + filter
        assert ".filter(" in out


def test_patch_strip_relationships_idempotent():
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(
            td, models={"habit.py": _HABIT_MODEL_WITH_REL, "habit_completion.py": _HABIT_COMPLETION_MODEL},
        )
        _patch_strip_relationships(proj)
        first = (proj / "app" / "models" / "habit.py").read_text(encoding="utf-8")
        n2 = _patch_strip_relationships(proj)
        second = (proj / "app" / "models" / "habit.py").read_text(encoding="utf-8")
        assert n2 == 0  # no `relationship(` left to find
        assert first == second


def test_patch_strip_relationships_noop_on_file_without_relationship():
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(td, models={"habit_completion.py": _HABIT_COMPLETION_MODEL})
        n = _patch_strip_relationships(proj)
        assert n == 0


def test_patch_strip_relationships_unresolvable_target_degrades_to_empty_list():
    with tempfile.TemporaryDirectory() as td:
        model = (
            "from app.database import Base\n"
            "from sqlalchemy import Column, Integer, relationship\n\n"
            "class Orphan(Base):\n"
            "    __tablename__ = 'orphans'\n"
            "    id = Column(Integer, primary_key=True)\n"
            "    ghosts = relationship('NoSuchModel')\n"
        )
        proj = _mk_project(td, models={"orphan.py": model})
        _patch_strip_relationships(proj)
        out = (proj / "app" / "models" / "orphan.py").read_text(encoding="utf-8")
        assert "return []" in out  # documented fallback, never raises AttributeError


def test_patch_strip_relationships_handles_property_named_relation_before_another_relation():
    """A ``property`` relation must not shadow the decorator for the next one."""
    lease = (
        "from app.database import Base\n"
        "from sqlalchemy import Column, Integer, ForeignKey, relationship\n\n"
        "class Lease(Base):\n"
        "    __tablename__ = 'leases'\n"
        "    id = Column(Integer, primary_key=True)\n"
        "    property_id = Column(Integer, ForeignKey('properties.id'))\n"
        "    tenant_id = Column(Integer, ForeignKey('users.id'))\n"
        "    property = relationship('Property')\n"
        "    tenant = relationship('User')\n"
    )
    target = (
        "from app.database import Base\n"
        "from sqlalchemy import Column, Integer\n\n"
        "class Property(Base):\n"
        "    __tablename__ = 'properties'\n"
        "    id = Column(Integer, primary_key=True)\n\n"
        "class User(Base):\n"
        "    __tablename__ = 'users'\n"
        "    id = Column(Integer, primary_key=True)\n"
    )
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(td, models={"lease.py": lease, "targets.py": target})
        assert _patch_strip_relationships(proj) == 1
        out = (proj / "app" / "models" / "lease.py").read_text(encoding="utf-8")
        assert "@builtins.property\n    def property" in out
        compile(out, "lease.py", "exec")


def test_models_without_primary_key_get_a_surrogate_id():
    association = (
        "from sqlalchemy import Column, Integer, ForeignKey\n"
        "from app.database import Base\n\n"
        "class OrderProduct(Base):\n"
        "    __tablename__ = 'order_products'\n"
        "    order_id = Column(Integer, ForeignKey('orders.id'))\n"
        "    product_id = Column(Integer, ForeignKey('products.id'))\n"
    )
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(td, models={"order_product.py": association})
        assert _patch_models_without_primary_key(proj) == 1
        out = (proj / "app" / "models" / "order_product.py").read_text(encoding="utf-8")
        assert "id = Column(Integer, primary_key=True, autoincrement=True)" in out
        assert _patch_models_without_primary_key(proj) == 0


# ─── _patch_dangling_foreign_keys ─────────────────────────────────────────

def test_patch_dangling_foreign_keys_strips_unresolvable_fk():
    with tempfile.TemporaryDirectory() as td:
        model = (
            "from app.database import Base\n"
            "from sqlalchemy import Column, Integer, ForeignKey\n\n"
            "class Task(Base):\n"
            "    __tablename__ = 'tasks'\n"
            "    id = Column(Integer, primary_key=True)\n"
            "    project_id = Column(Integer, ForeignKey('projects.id'))\n"
        )
        proj = _mk_project(td, models={"task.py": model})  # no "projects" model exists
        n = _patch_dangling_foreign_keys(proj)
        assert n == 1
        out = (proj / "app" / "models" / "task.py").read_text(encoding="utf-8")
        # The ForeignKey(...) CALL is stripped from the Column() declaration;
        # the (now-unused) top-level import is left alone -- cleaning unused
        # imports is out of scope for this function, confirmed by reading
        # its docstring/body, not assumed.
        assert "ForeignKey(" not in out
        assert "project_id = Column(Integer)" in out  # column survives as plain Integer


def test_patch_dangling_foreign_keys_keeps_valid_fk():
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(
            td, models={"habit.py": _HABIT_MODEL_WITH_REL.replace("relationship('HabitCompletion')\n", ""),
                        "habit_completion.py": _HABIT_COMPLETION_MODEL},
        )
        n = _patch_dangling_foreign_keys(proj)
        assert n == 0
        out = (proj / "app" / "models" / "habit_completion.py").read_text(encoding="utf-8")
        assert "ForeignKey('habits.id')" in out


def test_patch_dangling_foreign_keys_noop_no_models_dir():
    with tempfile.TemporaryDirectory() as td:
        assert _patch_dangling_foreign_keys(Path(td)) == 0


# ─── _patch_model_aliases ─────────────────────────────────────────────────

def test_patch_model_aliases_adds_alias_for_plural_mismatch():
    with tempfile.TemporaryDirectory() as td:
        model = (
            "from app.database import Base\n"
            "from sqlalchemy import Column, Integer\n\n"
            "class Games(Base):\n"
            "    __tablename__ = 'games'\n"
            "    id = Column(Integer, primary_key=True)\n"
        )
        route = "from app.models.games import Game\n\ndef f():\n    return Game\n"
        proj = _mk_project(td, models={"games.py": model}, routes={"game_routes.py": route})
        _patch_model_aliases(proj)
        out = (proj / "app" / "models" / "games.py").read_text(encoding="utf-8")
        assert "Game = Games" in out


def test_patch_model_aliases_noop_when_name_already_matches():
    with tempfile.TemporaryDirectory() as td:
        route = "from app.models.todo import Todo\n\ndef f():\n    return Todo\n"
        proj = _mk_project(td, models={"todo.py": _TODO_MODEL}, routes={"todo_routes.py": route})
        _patch_model_aliases(proj)
        out = (proj / "app" / "models" / "todo.py").read_text(encoding="utf-8")
        assert out == _TODO_MODEL  # nothing needed adding


# ─── _patch_relationship_string_aliases ───────────────────────────────────
# Tested in isolation -- see module docstring re: this being structurally
# unreachable in the live pipeline order, but a real, correct function on
# its own.

def test_patch_relationship_string_aliases_fixes_wrong_class_name():
    with tempfile.TemporaryDirectory() as td:
        model_a = (
            "from app.database import Base\n"
            "from sqlalchemy import Column, Integer, relationship\n\n"
            "class Book(Base):\n"
            "    __tablename__ = 'books'\n"
            "    id = Column(Integer, primary_key=True)\n"
            "    genre = relationship('Genre')\n"  # wrong: real class is "Genres"
        )
        model_b = (
            "from app.database import Base\n"
            "from sqlalchemy import Column, Integer\n\n"
            "class Genres(Base):\n"
            "    __tablename__ = 'genres'\n"
            "    id = Column(Integer, primary_key=True)\n"
        )
        proj = _mk_project(td, models={"book.py": model_a, "genre.py": model_b})
        _patch_relationship_string_aliases(proj)
        out = (proj / "app" / "models" / "book.py").read_text(encoding="utf-8")
        assert "relationship('Genres')" in out


def test_patch_relationship_string_aliases_noop_when_class_name_correct():
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(
            td, models={"habit.py": _HABIT_MODEL_WITH_REL, "habit_completion.py": _HABIT_COMPLETION_MODEL},
        )
        before = (proj / "app" / "models" / "habit.py").read_text(encoding="utf-8")
        _patch_relationship_string_aliases(proj)
        after = (proj / "app" / "models" / "habit.py").read_text(encoding="utf-8")
        assert before == after  # 'HabitCompletion' already matches a real class


def test_patch_relationship_string_aliases_structurally_unreachable_after_strip(monkeypatch=None):
    """Confirms the Exp051 finding directly: running the live-pipeline order
    (strip_relationships first) leaves nothing for this function to find."""
    with tempfile.TemporaryDirectory() as td:
        model_a = (
            "from app.database import Base\n"
            "from sqlalchemy import Column, Integer, relationship\n\n"
            "class Book(Base):\n"
            "    __tablename__ = 'books'\n"
            "    id = Column(Integer, primary_key=True)\n"
            "    genre = relationship('Genre')\n"
        )
        model_b = (
            "from app.database import Base\n"
            "from sqlalchemy import Column, Integer\n\n"
            "class Genres(Base):\n"
            "    __tablename__ = 'genres'\n"
            "    id = Column(Integer, primary_key=True)\n"
        )
        proj = _mk_project(td, models={"book.py": model_a, "genre.py": model_b})
        _patch_strip_relationships(proj)  # runs first in the real pipeline
        before = (proj / "app" / "models" / "book.py").read_text(encoding="utf-8")
        assert "relationship(" not in before  # confirmed: already gone
        _patch_relationship_string_aliases(proj)
        after = (proj / "app" / "models" / "book.py").read_text(encoding="utf-8")
        assert before == after  # nothing left to fix, no-op


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
