"""
Unit tests for deterministic_seed_generator's discovery, candidacy,
eligibility, and ordering logic. Plain assert-based (no pytest installed
in this project) -- run directly: python tests/adr002/test_discovery_and_ordering.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_seed_generator import (
    discover_models,
    find_lookup_entities,
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

FIXTURE_USER = '''
from sqlalchemy import Column, Integer, String
from app.database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False)
'''

FIXTURE_SHIM = 'from app.models.priorities import Priority\n'

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

FIXTURE_TAG = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Tag(Base):
    __tablename__ = "tags"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
'''

FIXTURE_POST = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Post(Base):
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    tag_id = Column(Integer, ForeignKey("tags.id"), nullable=False)
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

FIXTURE_NODE_SELF_REF = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Node(Base):
    __tablename__ = "nodes"
    id = Column(Integer, primary_key=True)
    label = Column(String(50), nullable=False)
    parent_id = Column(Integer, ForeignKey("nodes.id"), nullable=False)
'''

FIXTURE_ALPHA_CYCLE = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Alpha(Base):
    __tablename__ = "alphas"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    beta_id = Column(Integer, ForeignKey("betas.id"), nullable=False)
'''

FIXTURE_BETA_CYCLE = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Beta(Base):
    __tablename__ = "betas"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    alpha_id = Column(Integer, ForeignKey("alphas.id"), nullable=False)
'''

FIXTURE_POST_COMMENT = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Post(Base):
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    comment_id = Column(Integer, ForeignKey("comments.id"), nullable=False)
'''

FIXTURE_COMMENT_USER_FK = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Comment(Base):
    __tablename__ = "comments"
    id = Column(Integer, primary_key=True)
    text = Column(String(500), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
'''

FIXTURE_REPLY_COMMENT_FK = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Reply(Base):
    __tablename__ = "replies"
    id = Column(Integer, primary_key=True)
    text = Column(String(500), nullable=False)
    comment_id = Column(Integer, ForeignKey("comments.id"), nullable=False)
'''

FIXTURE_THREAD_REPLY_FK = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Thread(Base):
    __tablename__ = "threads"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    reply_id = Column(Integer, ForeignKey("replies.id"), nullable=False)
'''


def _entities(*sources: str) -> dict:
    out = {}
    for src in sources:
        e = extract_entity_definition(src)
        if e is not None:
            out[e.table_name] = e
    return out


def test_discover_models_basic():
    with tempfile.TemporaryDirectory() as tmp:
        models_dir = os.path.join(tmp, "app", "models")
        os.makedirs(models_dir)
        with open(os.path.join(models_dir, "priority.py"), "w") as f:
            f.write(FIXTURE_PRIORITY)
        with open(os.path.join(models_dir, "task.py"), "w") as f:
            f.write(FIXTURE_TASK)
        entities = discover_models(tmp)
        assert set(entities.keys()) == {"priorities", "tasks"}, entities.keys()


def test_discover_models_skips_shim():
    with tempfile.TemporaryDirectory() as tmp:
        models_dir = os.path.join(tmp, "app", "models")
        os.makedirs(models_dir)
        with open(os.path.join(models_dir, "priorities.py"), "w") as f:
            f.write(FIXTURE_PRIORITY)
        with open(os.path.join(models_dir, "priority.py"), "w") as f:
            f.write(FIXTURE_SHIM)
        entities = discover_models(tmp)
        assert list(entities.keys()) == ["priorities"]


def test_discover_models_excludes_users():
    with tempfile.TemporaryDirectory() as tmp:
        models_dir = os.path.join(tmp, "app", "models")
        os.makedirs(models_dir)
        with open(os.path.join(models_dir, "user.py"), "w") as f:
            f.write(FIXTURE_USER)
        entities = discover_models(tmp)
        assert entities == {}


def test_discover_models_empty_project():
    with tempfile.TemporaryDirectory() as tmp:
        entities = discover_models(tmp)
        assert entities == {}


def test_find_lookup_entities_simple_fk_target():
    entities = _entities(FIXTURE_PRIORITY, FIXTURE_TASK)
    eligible, exclusions = find_lookup_entities(entities)
    names = {e.table_name for e in eligible}
    assert names == {"priorities"}, names
    assert exclusions == []


def test_find_lookup_entities_excludes_entity_with_no_inbound_fk():
    # Product has zero incoming FK -- it's the "business entity" and must
    # never become a candidate no matter how many FKs it declares outward.
    entities = _entities(FIXTURE_CATEGORY, FIXTURE_SUBCATEGORY, FIXTURE_PRODUCT)
    eligible, exclusions = find_lookup_entities(entities)
    names = {e.table_name for e in eligible}
    assert "products" not in names, names


def test_find_lookup_entities_multilevel_chain_eligible():
    entities = _entities(FIXTURE_CATEGORY, FIXTURE_SUBCATEGORY, FIXTURE_PRODUCT)
    eligible, exclusions = find_lookup_entities(entities)
    names = {e.table_name for e in eligible}
    assert names == {"categories", "subcategories"}, names
    assert exclusions == []


def test_find_lookup_entities_excludes_required_external_fk():
    # Tag is a candidate (Post FKs to it) but requires a real `users` row --
    # must be excluded, not resolved with a runtime lookup.
    entities = _entities(FIXTURE_TAG, FIXTURE_POST, FIXTURE_USER)
    eligible, exclusions = find_lookup_entities(entities)
    names = {e.table_name for e in eligible}
    assert "tags" not in names, names
    assert any("tags" in line and "outside the deterministic lookup graph" in line
               for line in exclusions), exclusions


def test_find_lookup_entities_nullable_external_fk_stays_eligible():
    # Status has a nullable FK to users -- must NOT be excluded, and the
    # nullable column is simply omitted later at render time (Task 2).
    entities = _entities(FIXTURE_STATUS, FIXTURE_TICKET, FIXTURE_USER)
    eligible, exclusions = find_lookup_entities(entities)
    names = {e.table_name for e in eligible}
    assert "statuses" in names, names
    assert exclusions == []


def test_find_lookup_entities_excludes_required_self_reference():
    # Node's own parent_id -> nodes.id makes "nodes" a candidate (it's an
    # FK target of itself); a REQUIRED self-reference can never be
    # satisfied by the first inserted row and must be excluded.
    entities = _entities(FIXTURE_NODE_SELF_REF)
    eligible, exclusions = find_lookup_entities(entities)
    names = {e.table_name for e in eligible}
    assert "nodes" not in names, names
    assert any("self-referential" in line for line in exclusions), exclusions


def test_topological_order_multilevel_chain():
    entities = _entities(FIXTURE_CATEGORY, FIXTURE_SUBCATEGORY, FIXTURE_PRODUCT)
    eligible, _ = find_lookup_entities(entities)
    ordered = topological_order(eligible)
    assert ordered is not None
    order_names = [e.table_name for e in ordered]
    assert order_names.index("categories") < order_names.index("subcategories"), order_names


def test_topological_order_detects_cycle():
    entities = _entities(FIXTURE_ALPHA_CYCLE, FIXTURE_BETA_CYCLE)
    eligible, exclusions = find_lookup_entities(entities)
    # Neither required FK points outside the pair, so eligibility keeps both --
    # the cycle can only be caught by topological_order.
    names = {e.table_name for e in eligible}
    assert names == {"alphas", "betas"}, names
    ordered = topological_order(eligible)
    assert ordered is None


def test_find_lookup_entities_two_hop_fixed_point_cascade():
    """
    Verifies that the while-loop actually re-scans after removing candidates.

    Setup: Post -> comments.id (makes Comment candidate), Comment -> users.id
    (external), Reply -> comments.id, Thread -> replies.id (makes Reply candidate).

    Pass 1: Comment excluded (FK to users outside eligible).
    Pass 2: Reply must be re-scanned and excluded (FK to comments no longer
    eligible after pass 1 removal).

    This test would FAIL if find_lookup_entities naively checked each candidate
    once against the ORIGINAL candidate set instead of iterating until fixed
    point (the while changed: loop is essential).
    """
    entities = _entities(
        FIXTURE_POST_COMMENT,
        FIXTURE_COMMENT_USER_FK,
        FIXTURE_REPLY_COMMENT_FK,
        FIXTURE_THREAD_REPLY_FK
    )
    eligible, exclusions = find_lookup_entities(entities)
    names = {e.table_name for e in eligible}

    # Neither comments nor replies should be in the eligible set
    assert "comments" not in names, f"comments unexpectedly eligible: {names}"
    assert "replies" not in names, f"replies unexpectedly eligible: {names}"

    # Verify the exclusion log contains evidence of both removals
    exclusion_text = "\n".join(exclusions)

    # Pass 1: comments excluded for FK to users (external)
    assert any("excluded comments" in line and "users" in line
               for line in exclusions), \
        f"Missing exclusion log for comments->users: {exclusions}"

    # Pass 2: replies excluded for FK to comments
    # This is the critical proof that the while-loop fired a second pass
    assert any("excluded replies" in line and "comments" in line
               for line in exclusions), \
        f"Missing exclusion log for replies->comments (two-pass proof): {exclusions}"


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
