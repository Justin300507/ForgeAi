"""
Fixture-based tests for ADR-001 extension Phase D:
derive_relationship_kinds() (app/services/entity_metadata.py) -- accurate
cross-entity relationship-kind derivation, replacing Phase B's per-file
local heuristic.

Plain assert-based (no pytest installed in this project) -- run directly:
python tests/adr001_ext/test_relationship_kind_derivation.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.entity_metadata import derive_relationship_kinds, extract_entity_definition

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

FIXTURE_ORPHAN_NO_BACK_POPULATES = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base

class Note(Base):
    __tablename__ = "notes"
    id = Column(Integer, primary_key=True)
    body = Column(String(500), nullable=False)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=False)

    contact = relationship("Contact")
'''

FIXTURE_CONTACT = '''
from sqlalchemy import Column, Integer, String
from app.database import Base

class Contact(Base):
    __tablename__ = "contacts"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
'''


def _entities(*sources):
    return [e for e in (extract_entity_definition(s) for s in sources) if e is not None]


def test_many_to_many_confirmed_both_sides_agree():
    entities = _entities(FIXTURE_POST, FIXTURE_TAG, FIXTURE_USER, FIXTURE_COMMENT)
    derive_relationship_kinds(entities)

    post = next(e for e in entities if e.class_name == "Post")
    tag = next(e for e in entities if e.class_name == "Tag")

    post_tags_rel = next(r for r in post.relationships if r.attr_name == "tags")
    tag_posts_rel = next(r for r in tag.relationships if r.attr_name == "posts")

    assert post_tags_rel.kind == "many_to_many"
    assert tag_posts_rel.kind == "many_to_many"


def test_many_to_one_and_one_to_many_confirmed_by_fk_cross_check():
    entities = _entities(FIXTURE_POST, FIXTURE_USER, FIXTURE_TAG, FIXTURE_COMMENT)
    derive_relationship_kinds(entities)

    post = next(e for e in entities if e.class_name == "Post")
    user = next(e for e in entities if e.class_name == "User")
    comment = next(e for e in entities if e.class_name == "Comment")

    # Post.author_id is an FK to users -> Post's "author" relationship is many_to_one
    author_rel = next(r for r in post.relationships if r.attr_name == "author")
    assert author_rel.kind == "many_to_one"

    # User has no FK to posts -> User's "posts" relationship is one_to_many
    user_posts_rel = next(r for r in user.relationships if r.attr_name == "posts")
    assert user_posts_rel.kind == "one_to_many"

    # Comment.post_id is an FK to posts -> Comment's "post" relationship is many_to_one
    comment_post_rel = next(r for r in comment.relationships if r.attr_name == "post")
    assert comment_post_rel.kind == "many_to_one"

    # Post has no FK to comments -> Post's "comments" relationship is one_to_many
    post_comments_rel = next(r for r in post.relationships if r.attr_name == "comments")
    assert post_comments_rel.kind == "one_to_many"


def test_no_back_populates_counterpart_falls_back_to_own_fk_evidence():
    # Note declares a relationship to Contact but Contact has no reverse
    # relationship at all (no back_populates on either side) -- Phase D
    # must still classify Note's own side using its own FK evidence,
    # rather than refusing to determine anything.
    entities = _entities(FIXTURE_ORPHAN_NO_BACK_POPULATES, FIXTURE_CONTACT)
    derive_relationship_kinds(entities)

    note = next(e for e in entities if e.class_name == "Note")
    contact_rel = next(r for r in note.relationships if r.attr_name == "contact")
    assert contact_rel.kind == "many_to_one"  # Note.contact_id is a real FK to contacts


def test_target_entity_not_in_collection_does_not_crash():
    # Post references "User" and "Tag" but only Post itself is in the
    # collection passed to derive_relationship_kinds -- must degrade
    # gracefully (fall back to own-FK evidence), never raise.
    entities = _entities(FIXTURE_POST)
    derive_relationship_kinds(entities)  # must not raise

    post = entities[0]
    author_rel = next(r for r in post.relationships if r.attr_name == "author")
    assert author_rel.kind == "many_to_one"  # still derivable from Post's own FK


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
