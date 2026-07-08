"""
Fixture-based tests for entity_metadata.py's Phase A relationship
extraction (docs/ADR-001-extension-investigation.md). Plain assert-based
(no pytest installed in this project) -- run directly:
python tests/adr001_ext/test_relationship_extraction.py

Fixtures are drawn from real generated code
(generated_projects/blogsphere/app/models/{post,tag,comment}.py) found
during the investigation, not invented shapes.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.entity_metadata import extract_entity_definition

FIXTURE_POST = '''
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.models.tag import Tag

class Post(Base):
    __tablename__ = "posts"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(250), nullable=False)
    slug = Column(String(250), nullable=False, unique=True)
    content = Column(Text, nullable=False)
    status = Column(String(20), nullable=False)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    view_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    author = relationship("User", back_populates="posts")
    comments = relationship("Comment", back_populates="post")
    tags = relationship("Tag", secondary="post_tags", back_populates="posts")
'''

FIXTURE_TAG = '''
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
from app.database import Base

class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    slug = Column(String(100), nullable=False, unique=True)

    posts = relationship("Post", secondary="post_tags", back_populates="tags")
'''

FIXTURE_NO_RELATIONSHIPS = '''
from sqlalchemy import Column, Integer, String
from app.database import Base

class Priority(Base):
    __tablename__ = "priorities"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
'''

FIXTURE_MULTILINE_RELATIONSHIP = '''
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
from app.database import Base

class Author(Base):
    __tablename__ = "authors"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)

    posts = relationship(
        "Post",
        back_populates="author",
    )
'''


def test_post_relationships_extracted_correctly():
    entity = extract_entity_definition(FIXTURE_POST)
    assert entity is not None
    assert len(entity.relationships) == 3, entity.relationships

    by_attr = {r.attr_name: r for r in entity.relationships}

    author = by_attr["author"]
    assert author.target_class == "User"
    assert author.back_populates == "posts"
    assert author.secondary is None

    comments = by_attr["comments"]
    assert comments.target_class == "Comment"
    assert comments.back_populates == "post"
    assert comments.secondary is None

    tags = by_attr["tags"]
    assert tags.target_class == "Tag"
    assert tags.back_populates == "posts"
    assert tags.secondary == "post_tags"


def test_tag_relationship_mirrors_post_many_to_many():
    entity = extract_entity_definition(FIXTURE_TAG)
    assert entity is not None
    assert len(entity.relationships) == 1
    rel = entity.relationships[0]
    assert rel.attr_name == "posts"
    assert rel.target_class == "Post"
    assert rel.secondary == "post_tags"
    assert rel.back_populates == "tags"


def test_existing_fields_and_table_name_unaffected():
    # Regression: adding relationships must not change any existing
    # field/table_name/class_name extraction for the same entity.
    entity = extract_entity_definition(FIXTURE_POST)
    assert entity.class_name == "Post"
    assert entity.table_name == "posts"
    assert entity.field_names() == {
        "id", "title", "slug", "content", "status", "author_id",
        "view_count", "created_at", "updated_at",
    }
    required = {f.name for f in entity.required_fields()}
    assert required == {"title", "slug", "content", "status"}


def test_entity_with_no_relationships_defaults_to_empty_list():
    entity = extract_entity_definition(FIXTURE_NO_RELATIONSHIPS)
    assert entity is not None
    assert entity.relationships == []


def test_multiline_relationship_call_not_captured_documented_limitation():
    # Deliberate, documented limitation (mirrors _COLUMN_RE's own
    # single-line-call requirement) -- Phase A's objective is richer
    # metadata, not parser modernization. A relationship() call split
    # across multiple lines is invisible today; this test proves that's
    # the current, expected, known behavior rather than a silent bug.
    entity = extract_entity_definition(FIXTURE_MULTILINE_RELATIONSHIP)
    assert entity is not None
    assert entity.relationships == []
    assert entity.field_names() == {"id", "name"}


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
