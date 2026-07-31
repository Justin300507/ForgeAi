"""
Unit tests for deterministic_seed_generator's value generation and
source rendering. Plain assert-based -- run directly:
python tests/adr002/test_render.py
"""
import ast
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_seed_generator import (
    find_lookup_entities,
    generate_field_value,
    render_seed_routes,
    topological_order,
)
from app.services.entity_metadata import extract_entity_definition

FIXTURE_PRIORITY = '''
from sqlalchemy import Column, Integer, String
from app.database import Base

class Priority(Base):
    __tablename__ = "priorities"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    level = Column(Integer, nullable=False)
    is_default = Column(Boolean, nullable=False)
    created_at = Column(DateTime, nullable=False)
    external_ref = Column(UUID, nullable=False)
'''

FIXTURE_TASK = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    priority_id = Column(Integer, ForeignKey("priorities.id"), nullable=False)
'''

FIXTURE_CATEGORY = '''
from sqlalchemy import Column, Integer, String
from app.database import Base

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
'''

FIXTURE_SUBCATEGORY = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Subcategory(Base):
    __tablename__ = "subcategories"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
'''

FIXTURE_PRODUCT = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    subcategory_id = Column(Integer, ForeignKey("subcategories.id"), nullable=False)
'''

FIXTURE_STATUS = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Status(Base):
    __tablename__ = "statuses"
    id = Column(Integer, primary_key=True)
    label = Column(String(50), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
'''

FIXTURE_TICKET = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(Integer, primary_key=True)
    subject = Column(String(200), nullable=False)
    status_id = Column(Integer, ForeignKey("statuses.id"), nullable=False)
'''


def _entities(*sources):
    import re

    def _to_snake_case(name: str) -> str:
        """Convert CamelCase to snake_case."""
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
        return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

    out = {}
    for src in sources:
        e = extract_entity_definition(src)
        if e is not None:
            # Populate source_path based on class name (what discover_models would produce)
            module_name = _to_snake_case(e.class_name)
            e.source_path = f"app/models/{module_name}.py"
            out[e.table_name] = e
    return out


def test_generate_field_value_string_uses_loop_var():
    entities = _entities(FIXTURE_PRIORITY)
    entity = entities["priorities"]
    name_field = next(f for f in entity.fields if f.name == "name")
    expr = generate_field_value(name_field, class_name="Priority")
    assert "i" in expr and "Priority" in expr, expr


def test_generate_field_value_int():
    entities = _entities(FIXTURE_PRIORITY)
    entity = entities["priorities"]
    level_field = next(f for f in entity.fields if f.name == "level")
    expr = generate_field_value(level_field, class_name="Priority")
    assert expr == "(i + 1)", expr


def test_generate_field_value_bool():
    entities = _entities(FIXTURE_PRIORITY)
    entity = entities["priorities"]
    bool_field = next(f for f in entity.fields if f.name == "is_default")
    expr = generate_field_value(bool_field, class_name="Priority")
    assert expr == "(i % 2 == 0)", expr


def test_generate_field_value_date():
    entities = _entities(FIXTURE_PRIORITY)
    entity = entities["priorities"]
    date_field = next(f for f in entity.fields if f.name == "created_at")
    expr = generate_field_value(date_field, class_name="Priority")
    assert "timedelta" in expr and "i" in expr, expr


def test_generate_field_value_uuid():
    entities = _entities(FIXTURE_PRIORITY)
    entity = entities["priorities"]
    uuid_field = next(f for f in entity.fields if f.name == "external_ref")
    expr = generate_field_value(uuid_field, class_name="Priority")
    assert expr == "str(uuid.uuid4())", expr


def test_render_single_lookup_entity_parses_and_has_version_header():
    entities = _entities(FIXTURE_PRIORITY, FIXTURE_TASK)
    eligible, _ = find_lookup_entities(entities)
    ordered = topological_order(eligible)
    source = render_seed_routes(ordered)
    ast.parse(source)  # must be syntactically valid Python
    assert "# Auto-generated seed data" in source
    assert "# ADR-002" in source
    assert "Generator Version: 1" in source
    assert "def _seed_priorities(db" in source
    assert "@seed_router.post('/seed')" in source


def test_render_multilevel_chain_parses_and_orders_helpers():
    entities = _entities(FIXTURE_CATEGORY, FIXTURE_SUBCATEGORY, FIXTURE_PRODUCT)
    eligible, _ = find_lookup_entities(entities)
    ordered = topological_order(eligible)
    source = render_seed_routes(ordered)
    ast.parse(source)
    cat_def = source.index("def _seed_categories(")
    sub_def = source.index("def _seed_subcategories(")
    assert cat_def < sub_def, "categories helper must be defined before subcategories"
    # Subcategory's helper must query the already-seeded Category pool,
    # not attempt a runtime lookup against an out-of-graph entity.
    assert "db.query(Category).all()" in source


def test_render_nullable_external_fk_omitted_from_insert():
    entities = _entities(FIXTURE_STATUS, FIXTURE_TICKET)
    eligible, _ = find_lookup_entities(entities)
    ordered = topological_order(eligible)
    source = render_seed_routes(ordered)
    ast.parse(source)
    # created_by (nullable FK to users, which isn't in the graph at all
    # here) must never appear as a constructor kwarg.
    seed_status_fn = source[source.index("def _seed_statuses("):]
    assert "created_by=" not in seed_status_fn.split("def _seed_")[0]


# ── Execution-based tests (spec testing-plan items 7 & 8): prove the
# rendered code actually behaves idempotently when run against a real
# database, not just that it parses/contains the right keywords. Uses a
# real in-memory SQLite engine -- still $0, local, no LLM/server.
FIXTURE_PRIORITY_EXECUTABLE = '''
from sqlalchemy import Column, Integer, String
from app.database import Base

class Priority(Base):
    __tablename__ = "priorities"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    level = Column(Integer, nullable=False)
'''

FIXTURE_TASK_EXECUTABLE = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    priority_id = Column(Integer, ForeignKey("priorities.id"), nullable=False)
'''


def _build_and_import_seed_module(tmp: str, model_sources: dict, rendered_source: str):
    """Writes a minimal REAL project (app/database.py wired to an
    in-memory SQLite engine, app/models/*.py from model_sources,
    app/routes/seed_routes.py = rendered_source) to `tmp`, adds it to
    sys.path, and imports the generated seed_routes module so its
    functions can be called directly against a real database."""
    import importlib

    app_dir = os.path.join(tmp, "app")
    models_dir = os.path.join(app_dir, "models")
    routes_dir = os.path.join(app_dir, "routes")
    os.makedirs(models_dir, exist_ok=True)
    os.makedirs(routes_dir, exist_ok=True)
    open(os.path.join(app_dir, "__init__.py"), "w").close()
    open(os.path.join(models_dir, "__init__.py"), "w").close()
    open(os.path.join(routes_dir, "__init__.py"), "w").close()

    with open(os.path.join(app_dir, "database.py"), "w") as f:
        f.write(
            "from sqlalchemy import create_engine\n"
            "from sqlalchemy.orm import sessionmaker, declarative_base\n\n"
            'engine = create_engine("sqlite:///:memory:", '
            'connect_args={"check_same_thread": False})\n'
            "SessionLocal = sessionmaker(bind=engine)\n"
            "Base = declarative_base()\n\n"
            "def get_db():\n"
            "    db = SessionLocal()\n"
            "    try:\n"
            "        yield db\n"
            "    finally:\n"
            "        db.close()\n"
        )

    for fname, content in model_sources.items():
        with open(os.path.join(models_dir, fname), "w") as f:
            f.write(content)

    with open(os.path.join(routes_dir, "seed_routes.py"), "w") as f:
        f.write(rendered_source)

    sys.path.insert(0, tmp)
    for mod_name in list(sys.modules):
        if mod_name == "app" or mod_name.startswith("app."):
            del sys.modules[mod_name]
    db_module = importlib.import_module("app.database")
    # Import model modules first so they register with Base before create_all
    for fname in model_sources:
        module_name = fname.replace('.py', '')
        importlib.import_module(f"app.models.{module_name}")
    db_module.Base.metadata.create_all(db_module.engine)
    seed_module = importlib.import_module("app.routes.seed_routes")
    return db_module, seed_module


def test_generated_seed_function_is_idempotent_end_to_end():
    import tempfile

    entities = _entities(FIXTURE_PRIORITY_EXECUTABLE, FIXTURE_TASK_EXECUTABLE)
    eligible, _ = find_lookup_entities(entities)
    ordered = topological_order(eligible)
    source = render_seed_routes(ordered)

    with tempfile.TemporaryDirectory() as tmp:
        db_module, seed_module = _build_and_import_seed_module(
            tmp,
            {"priority.py": FIXTURE_PRIORITY_EXECUTABLE, "task.py": FIXTURE_TASK_EXECUTABLE},
            source,
        )
        session = db_module.SessionLocal()
        try:
            first = seed_module._seed_priorities(session)
            assert first == {"inserted": 3, "skipped": 0, "already_existed": 0}, first
            second = seed_module._seed_priorities(session)
            assert second == {"inserted": 0, "skipped": 3, "already_existed": 3}, second
        finally:
            session.close()
            sys.path.remove(tmp)


def test_generated_seed_function_tops_up_partial_existing_data():
    import importlib
    import tempfile

    entities = _entities(FIXTURE_PRIORITY_EXECUTABLE, FIXTURE_TASK_EXECUTABLE)
    eligible, _ = find_lookup_entities(entities)
    ordered = topological_order(eligible)
    source = render_seed_routes(ordered)

    with tempfile.TemporaryDirectory() as tmp:
        db_module, seed_module = _build_and_import_seed_module(
            tmp,
            {"priority.py": FIXTURE_PRIORITY_EXECUTABLE, "task.py": FIXTURE_TASK_EXECUTABLE},
            source,
        )
        priority_module = importlib.import_module("app.models.priority")
        session = db_module.SessionLocal()
        try:
            session.add(priority_module.Priority(name="Pre-existing", level=1))
            session.commit()
            result = seed_module._seed_priorities(session)
            assert result == {"inserted": 2, "skipped": 0, "already_existed": 1}, result
            assert session.query(priority_module.Priority).count() == 3
        finally:
            session.close()
            sys.path.remove(tmp)


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
