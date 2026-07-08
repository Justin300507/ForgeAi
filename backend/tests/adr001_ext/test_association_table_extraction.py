"""
Fixture-based tests for entity_metadata.py's Phase C association-table
extraction (docs/ADR-001-extension-investigation.md). Plain assert-based
(no pytest installed in this project) -- run directly:
python tests/adr001_ext/test_association_table_extraction.py

Fixture is the REAL generated code found during the ADR-001 extension
investigation (generated_projects/blogsphere/app/models/post_tags.py),
not an invented shape.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.entity_metadata import extract_association_table, extract_entity_definition

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

FIXTURE_REGULAR_MODEL = '''
from sqlalchemy import Column, Integer, String
from app.database import Base

class Priority(Base):
    __tablename__ = "priorities"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
'''

FIXTURE_EMPTY_TABLE = '''
from sqlalchemy import Table
from app.database import Base

empty_thing = Table("empty_thing", Base.metadata)
'''


def test_post_tags_extracted_correctly():
    assoc = extract_association_table(FIXTURE_POST_TAGS)
    assert assoc is not None
    assert assoc.variable_name == "post_tags"
    assert assoc.table_name == "post_tags"
    assert len(assoc.columns) == 2

    by_name = {c.name: c for c in assoc.columns}
    assert by_name["post_id"].is_foreign_key
    assert by_name["post_id"].fk_target == "posts.id"
    assert by_name["post_id"].is_primary_key
    assert by_name["tag_id"].is_foreign_key
    assert by_name["tag_id"].fk_target == "tags.id"
    assert by_name["tag_id"].is_primary_key


def test_composite_primary_key_recognized():
    assoc = extract_association_table(FIXTURE_POST_TAGS)
    assert set(assoc.composite_primary_key()) == {"post_id", "tag_id"}


def test_regular_class_based_model_returns_none():
    # extract_association_table must not mistake a normal class-based
    # model for a bare Table() construct.
    assert extract_association_table(FIXTURE_REGULAR_MODEL) is None
    # And the reverse must hold too: extract_entity_definition must not
    # mistake a bare Table() for a class-based model.
    assert extract_entity_definition(FIXTURE_POST_TAGS) is None


def test_table_with_no_columns_returns_none():
    assert extract_association_table(FIXTURE_EMPTY_TABLE) is None


def test_empty_content_returns_none():
    assert extract_association_table("") is None


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
