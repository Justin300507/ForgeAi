"""
Fixture-based tests for the ADR-002 seeder integration of association
tables (ADR-001 extension Phase C integration --
deterministic_seed_generator.py). Includes an execution-based test that
runs the generated seed_routes.py against a real in-memory SQLite
database, matching this project's established ADR-002 verification
rigor (proving runtime behavior, not just structural output).

Plain assert-based (no pytest installed in this project) -- run directly:
python tests/adr001_ext/test_association_table_seeding.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_seed_generator import (
    discover_association_tables,
    find_lookup_entities,
    find_seedable_association_tables,
    generate,
    render_seed_routes,
    topological_order,
)

FIXTURE_POST = '''
from sqlalchemy import Column, Integer, String
from app.database import Base

class Post(Base):
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
'''

FIXTURE_TAG = '''
from sqlalchemy import Column, Integer, String
from app.database import Base

class Tag(Base):
    __tablename__ = "tags"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
'''

FIXTURE_POST_TAGS = '''
from sqlalchemy import Table, Column, Integer, ForeignKey
from app.database import Base

post_tags = Table(
    "post_tags",
    Base.metadata,
    Column("post_id", Integer, ForeignKey("posts.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
)
'''

FIXTURE_POST_TAGS_WRONG_SHAPE = '''
from sqlalchemy import Table, Column, Integer, ForeignKey, String
from app.database import Base

weird_table = Table(
    "weird_table",
    Base.metadata,
    Column("post_id", Integer, ForeignKey("posts.id"), primary_key=True),
    Column("extra", String(50)),
)
'''


def _build_project(models: dict) -> str:
    tmp = tempfile.mkdtemp()
    models_dir = os.path.join(tmp, "app", "models")
    os.makedirs(models_dir, exist_ok=True)
    for fname, content in models.items():
        with open(os.path.join(models_dir, fname), "w", encoding="utf-8") as f:
            f.write(content)
    return tmp


def test_pure_many_to_many_neither_side_independently_referenced_becomes_eligible():
    # The realistic common case this fix specifically targets: nothing
    # OTHER than the association table references "tags" or "posts" --
    # without folding association-table FKs into find_lookup_entities'
    # candidacy computation, neither would ever become a candidate and
    # post_tags could never be seedable.
    project_path = _build_project({
        "post.py": FIXTURE_POST, "tag.py": FIXTURE_TAG, "post_tags.py": FIXTURE_POST_TAGS,
    })
    from app.services.deterministic_seed_generator import discover_models
    entities = discover_models(project_path)
    association_tables = discover_association_tables(project_path)

    eligible, exclusion_log = find_lookup_entities(entities, association_tables)
    eligible_tables = {e.table_name for e in eligible}
    assert eligible_tables == {"posts", "tags"}, (eligible_tables, exclusion_log)

    ordered = topological_order(eligible)
    seedable, assoc_exclusions = find_seedable_association_tables(association_tables, ordered)
    assert len(seedable) == 1
    assert seedable[0].table_name == "post_tags"
    assert assoc_exclusions == []


def test_wrong_column_shape_excluded_and_logged():
    project_path = _build_project({
        "post.py": FIXTURE_POST,
        "weird_table.py": FIXTURE_POST_TAGS_WRONG_SHAPE,  # only 1 FK column, not 2
    })
    from app.services.deterministic_seed_generator import discover_models
    entities = discover_models(project_path)
    association_tables = discover_association_tables(project_path)
    eligible, _ = find_lookup_entities(entities, association_tables)
    ordered = topological_order(eligible)

    seedable, exclusions = find_seedable_association_tables(association_tables, ordered)
    assert seedable == []
    assert any("expected exactly 2" in line for line in exclusions), exclusions


def test_generate_end_to_end_includes_association_table():
    project_path = _build_project({
        "post.py": FIXTURE_POST, "tag.py": FIXTURE_TAG, "post_tags.py": FIXTURE_POST_TAGS,
    })
    source, telemetry = generate(project_path)
    assert source is not None
    assert telemetry["fallback_used"] is False
    assert telemetry["association_tables_seeded"] == 1
    assert "def _seed_post_tags(db: Session)" in source
    assert "post_tags.insert()" in source
    assert "select(func.count()).select_from(post_tags)" in source


def test_generated_association_table_seed_executes_correctly_against_real_db():
    # Execution-based test, matching this project's established ADR-002
    # rigor: exec the rendered source against a real in-memory SQLite
    # database via a real project layout, not just inspect the text.
    project_path = _build_project({
        "post.py": FIXTURE_POST, "tag.py": FIXTURE_TAG, "post_tags.py": FIXTURE_POST_TAGS,
    })
    source, telemetry = generate(project_path)
    assert source is not None

    # Build a real, importable project: app/database.py + the same model
    # files + the generated seed_routes.py.
    app_dir = os.path.join(project_path, "app")
    routes_dir = os.path.join(app_dir, "routes")
    os.makedirs(routes_dir, exist_ok=True)
    open(os.path.join(app_dir, "__init__.py"), "w").close()
    open(os.path.join(app_dir, "models", "__init__.py"), "w").close()
    open(os.path.join(routes_dir, "__init__.py"), "w").close()

    with open(os.path.join(app_dir, "database.py"), "w", encoding="utf-8") as f:
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
    with open(os.path.join(routes_dir, "seed_routes.py"), "w", encoding="utf-8") as f:
        f.write(source)

    sys.path.insert(0, project_path)
    for mod_name in list(sys.modules):
        if mod_name == "app" or mod_name.startswith("app."):
            del sys.modules[mod_name]

    import importlib
    db_module = importlib.import_module("app.database")
    # Import every model module explicitly BEFORE create_all() -- SQLAlchemy's
    # declarative Base.metadata only knows about classes/tables that have
    # actually been imported by the time create_all() runs.
    importlib.import_module("app.models.post")
    importlib.import_module("app.models.tag")
    importlib.import_module("app.models.post_tags")
    db_module.Base.metadata.create_all(db_module.engine)
    seed_module = importlib.import_module("app.routes.seed_routes")

    session = db_module.SessionLocal()
    try:
        result = seed_module.seed_data(db=session)
        assert result["seeded"] is True
        summary = result["summary"]
        assert summary["posts"]["inserted"] == 3
        assert summary["tags"]["inserted"] == 3
        assert summary["post_tags"]["inserted"] == 3
        assert summary["post_tags"]["already_existed"] == 0

        # Idempotency: calling again must insert zero new rows anywhere,
        # including the association table.
        result2 = seed_module.seed_data(db=session)
        summary2 = result2["summary"]
        assert summary2["posts"]["inserted"] == 0
        assert summary2["tags"]["inserted"] == 0
        assert summary2["post_tags"]["inserted"] == 0
        assert summary2["post_tags"]["already_existed"] == 3
    finally:
        session.close()
        sys.path.remove(project_path)


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
