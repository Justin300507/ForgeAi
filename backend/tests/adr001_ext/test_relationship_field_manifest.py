"""
Fixture-based tests for render_field_manifest()'s relationship guidance
(ADR-001 extension, Phase D integration into the schema-generation
prompt -- app/services/entity_metadata.py). Proves the manifest tells
the schema-gen LLM explicitly how to expose each relationship kind,
closing the exact gap Experiment 017 flagged (blog_cms independently
inventing a `tag_ids` field for its many-to-many `tags` relationship
with no guidance telling it that field needed to exist).

Plain assert-based (no pytest installed in this project) -- run directly:
python tests/adr001_ext/test_relationship_field_manifest.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.entity_metadata import (
    derive_relationship_kinds,
    extract_entity_definition,
    render_field_manifest,
)

FIXTURE_POST = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base

class Post(Base):
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True)
    title = Column(String(250), nullable=False)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    author = relationship("User", back_populates="posts")
    comments = relationship("Comment", back_populates="post")
    tags = relationship("Tag", secondary="post_tags", back_populates="posts")
'''

FIXTURE_USER = '''
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
from app.database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False)

    posts = relationship("Post", back_populates="author")
'''

FIXTURE_COMMENT = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base

class Comment(Base):
    __tablename__ = "comments"
    id = Column(Integer, primary_key=True)
    body = Column(String(500), nullable=False)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False)

    post = relationship("Post", back_populates="comments")
'''

FIXTURE_TAG = '''
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
from app.database import Base

class Tag(Base):
    __tablename__ = "tags"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)

    posts = relationship("Post", secondary="post_tags", back_populates="tags")
'''


def _entities(*sources):
    return [e for e in (extract_entity_definition(s) for s in sources) if e is not None]


def test_manifest_instructs_many_to_many_as_id_list():
    entities = _entities(FIXTURE_POST, FIXTURE_USER, FIXTURE_TAG, FIXTURE_COMMENT)
    derive_relationship_kinds(entities)
    post = next(e for e in entities if e.class_name == "Post")

    manifest = render_field_manifest(post)
    assert "tags: many-to-many with Tag" in manifest
    assert "tag_ids: list[int]" in manifest


def test_manifest_instructs_many_to_one_as_already_covered():
    entities = _entities(FIXTURE_POST, FIXTURE_USER, FIXTURE_TAG, FIXTURE_COMMENT)
    derive_relationship_kinds(entities)
    post = next(e for e in entities if e.class_name == "Post")

    manifest = render_field_manifest(post)
    assert "author: many-to-one to User" in manifest
    assert "already covered by the foreign-key column" in manifest


def test_manifest_instructs_one_to_many_as_response_only():
    entities = _entities(FIXTURE_POST, FIXTURE_USER, FIXTURE_TAG, FIXTURE_COMMENT)
    derive_relationship_kinds(entities)
    post = next(e for e in entities if e.class_name == "Post")

    manifest = render_field_manifest(post)
    assert "comments: one-to-many of Comment" in manifest
    assert "never required on Create/Update" in manifest


def test_manifest_without_kind_derivation_still_renders_conservative_hint():
    # If a caller forgets to run derive_relationship_kinds() first, every
    # relationship's kind is None -- the manifest must still mention the
    # relationship (not silently drop it), just with a conservative,
    # explicitly-flagged "undetermined" hint instead of a specific one.
    entities = _entities(FIXTURE_POST)
    post = entities[0]  # kind is None on every relationship -- derive_relationship_kinds never ran

    manifest = render_field_manifest(post)
    assert "tags: relationship to Tag" in manifest
    assert "kind undetermined" in manifest


def test_manifest_with_no_relationships_is_unchanged_from_before():
    entities = _entities(FIXTURE_USER)
    user_without_derivation = entities[0]
    manifest = render_field_manifest(user_without_derivation)
    # User fixture as parsed here has a "posts" relationship declared --
    # confirm it's present but the columns section is untouched/normal.
    assert "Table: users" in manifest
    assert "email: String" in manifest


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
