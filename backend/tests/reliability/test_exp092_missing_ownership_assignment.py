"""
Experiment 092: regression tests for _patch_missing_ownership_assignment
(app/services/deterministic_patcher.py) -- the CREATE-time counterpart
to _patch_ownership_fk_attribute_drift.

Root cause (Exp091): 17/23 (74%) of tracked JourneyCRUDFailure instances
share one shape -- a POST handler accepts current_user but never assigns
the constructed model's ownership FK before db.add(). Confirmed live in
generated_projects/inventory_manager/app/routes/product_routes.py's
create_product(): current_user accepted, never referenced again.
app/prompts/shared_contract.py already instructs this exact assignment
but scopes it to the literal string "user_id", missing owner_id/
author_id/creator_id/created_by.

Run directly: python tests/reliability/test_exp092_missing_ownership_assignment.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import _patch_missing_ownership_assignment


def _make_project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="ownershipassign_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


_TASK_MODEL = '''\
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
'''

_POST_MODEL = '''\
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Post(Base):
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
'''

_PRODUCT_MODEL_NO_OWNERSHIP = '''\
from sqlalchemy import Column, Integer, String, Float
from app.database import Base

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=True)
    unit_cost = Column(Float, nullable=False)
'''


def _route_missing_assignment(entity="Task", var="task", module="tasks", path="/tasks"):
    return f'''\
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.{module} import {entity}
from app.utils.auth import get_current_user

router = APIRouter()

@router.post("{path}")
def create_{var}(
    {var}_in: dict,
    db: Session = Depends(get_db),
    current_user: "Users" = Depends(get_current_user),
):
    {var} = {entity}(**{{k: v for k, v in {var}_in.items() if k in {entity}.__table__.columns.keys()}})
    db.add({var})
    db.commit()
    db.refresh({var})
    return {var}
'''


# ---------------------------------------------------------------------------
# Task 3/4: core detection + injection, confirmed live shape
# ---------------------------------------------------------------------------

def test_injects_assignment_when_missing_user_id():
    root = _make_project({
        "app/models/tasks.py": _TASK_MODEL,
        "app/routes/task_routes.py": _route_missing_assignment(),
    })
    n = _patch_missing_ownership_assignment(root)
    out = (root / "app" / "routes" / "task_routes.py").read_text(encoding="utf-8")
    assert n == 1
    assert "task.user_id = current_user.id" in out
    # Must land before db.add(task), not after.
    lines = out.splitlines()
    add_idx = next(i for i, l in enumerate(lines) if "db.add(task)" in l)
    assign_idx = next(i for i, l in enumerate(lines) if "task.user_id = current_user.id" in l)
    assert assign_idx < add_idx


def test_injects_assignment_for_author_id_naming_gap():
    # The exact prompt-scope gap Exp091 found: the contract rule only
    # mentions the literal string "user_id" -- this confirms the
    # deterministic fix isn't scoped that narrowly.
    root = _make_project({
        "app/models/posts.py": _POST_MODEL,
        "app/routes/post_routes.py": _route_missing_assignment(
            entity="Post", var="post", module="posts", path="/posts"
        ),
    })
    n = _patch_missing_ownership_assignment(root)
    out = (root / "app" / "routes" / "post_routes.py").read_text(encoding="utf-8")
    assert n == 1
    assert "post.author_id = current_user.id" in out


def test_idempotent_second_pass_no_op():
    root = _make_project({
        "app/models/tasks.py": _TASK_MODEL,
        "app/routes/task_routes.py": _route_missing_assignment(),
    })
    n1 = _patch_missing_ownership_assignment(root)
    after_first = (root / "app" / "routes" / "task_routes.py").read_text(encoding="utf-8")
    n2 = _patch_missing_ownership_assignment(root)
    after_second = (root / "app" / "routes" / "task_routes.py").read_text(encoding="utf-8")
    assert n1 == 1
    assert n2 == 0
    assert after_first == after_second


# ---------------------------------------------------------------------------
# Task 5: preservation cases
# ---------------------------------------------------------------------------

def test_preserves_handler_already_assigning_via_constructor_kwarg():
    route = '''\
from fastapi import APIRouter, Depends
from app.database import get_db
from app.models.tasks import Task
from app.utils.auth import get_current_user

router = APIRouter()

@router.post("/tasks")
def create_task(task_in: dict, db=Depends(get_db), current_user=Depends(get_current_user)):
    task = Task(title=task_in["title"], user_id=current_user.id)
    db.add(task)
    db.commit()
    return task
'''
    root = _make_project({"app/models/tasks.py": _TASK_MODEL, "app/routes/task_routes.py": route})
    n = _patch_missing_ownership_assignment(root)
    out = (root / "app" / "routes" / "task_routes.py").read_text(encoding="utf-8")
    assert n == 0
    assert out == route


def test_preserves_handler_already_assigning_via_attribute():
    route = '''\
from fastapi import APIRouter, Depends
from app.database import get_db
from app.models.tasks import Task
from app.utils.auth import get_current_user

router = APIRouter()

@router.post("/tasks")
def create_task(task_in: dict, db=Depends(get_db), current_user=Depends(get_current_user)):
    task = Task(title=task_in["title"])
    task.user_id = current_user.id
    db.add(task)
    db.commit()
    return task
'''
    root = _make_project({"app/models/tasks.py": _TASK_MODEL, "app/routes/task_routes.py": route})
    n = _patch_missing_ownership_assignment(root)
    out = (root / "app" / "routes" / "task_routes.py").read_text(encoding="utf-8")
    assert n == 0
    assert out == route


def test_preserves_custom_ownership_logic_with_different_value():
    # The field IS assigned, just from something other than
    # current_user.id -- must not be overridden or duplicated.
    route = '''\
from fastapi import APIRouter, Depends
from app.database import get_db
from app.models.posts import Post
from app.utils.auth import get_current_user

router = APIRouter()

@router.post("/posts")
def create_post(post_in: dict, db=Depends(get_db), current_user=Depends(get_current_user)):
    post = Post(title=post_in["title"])
    post.author_id = post_in.get("assigned_author_id", current_user.id)
    db.add(post)
    db.commit()
    return post
'''
    root = _make_project({"app/models/posts.py": _POST_MODEL, "app/routes/post_routes.py": route})
    n = _patch_missing_ownership_assignment(root)
    out = (root / "app" / "routes" / "post_routes.py").read_text(encoding="utf-8")
    assert n == 0
    assert out == route


def test_preserves_dict_mutation_then_unpack_assignment():
    # Reproduces generated_projects/recipe_forge/app/routes/recipe_routes.py
    # exactly: recipe_for_db["user_id"] = current_user.id, then
    # Recipe(**recipe_for_db) -- ownership already correctly assigned via
    # dict mutation before unpacking, not a literal kwarg or a
    # post-construction attribute assignment.
    route = '''\
from fastapi import APIRouter, Depends
from app.database import get_db
from app.models.tasks import Task
from app.utils.auth import get_current_user

router = APIRouter()

@router.post("/tasks")
def create_task(task_in: dict, db=Depends(get_db), current_user=Depends(get_current_user)):
    task_for_db = dict(task_in)
    task_for_db["user_id"] = current_user.id
    task = Task(**task_for_db)
    db.add(task)
    db.commit()
    return task
'''
    root = _make_project({"app/models/tasks.py": _TASK_MODEL, "app/routes/task_routes.py": route})
    n = _patch_missing_ownership_assignment(root)
    out = (root / "app" / "routes" / "task_routes.py").read_text(encoding="utf-8")
    assert n == 0
    assert out == route


def test_models_without_ownership_fk_untouched():
    # Reproduces generated_projects/inventory_manager exactly: current_user
    # accepted and unused, but the model has no ownership column at all.
    route = _route_missing_assignment(entity="Product", var="product", module="products", path="/products")
    root = _make_project({
        "app/models/products.py": _PRODUCT_MODEL_NO_OWNERSHIP,
        "app/routes/product_routes.py": route,
    })
    n = _patch_missing_ownership_assignment(root)
    out = (root / "app" / "routes" / "product_routes.py").read_text(encoding="utf-8")
    assert n == 0
    assert out == route


def test_no_current_user_dependency_untouched():
    # A POST handler with no auth dependency at all is out of scope --
    # nothing to assign from.
    route = '''\
from fastapi import APIRouter, Depends
from app.database import get_db
from app.models.tasks import Task

router = APIRouter()

@router.post("/tasks")
def create_task(task_in: dict, db=Depends(get_db)):
    task = Task(title=task_in["title"])
    db.add(task)
    db.commit()
    return task
'''
    root = _make_project({"app/models/tasks.py": _TASK_MODEL, "app/routes/task_routes.py": route})
    n = _patch_missing_ownership_assignment(root)
    out = (root / "app" / "routes" / "task_routes.py").read_text(encoding="utf-8")
    assert n == 0
    assert out == route


def test_no_db_add_call_untouched():
    # Confirmed edge case: no db.add(var) reachable -- conservative no-op
    # rather than guessing an insertion point.
    route = '''\
from fastapi import APIRouter, Depends
from app.database import get_db
from app.models.tasks import Task
from app.utils.auth import get_current_user

router = APIRouter()

@router.post("/tasks")
def create_task(task_in: dict, db=Depends(get_db), current_user=Depends(get_current_user)):
    task = Task(title=task_in["title"])
    return task
'''
    root = _make_project({"app/models/tasks.py": _TASK_MODEL, "app/routes/task_routes.py": route})
    n = _patch_missing_ownership_assignment(root)
    out = (root / "app" / "routes" / "task_routes.py").read_text(encoding="utf-8")
    assert n == 0
    assert out == route


def test_no_op_when_no_routes_or_models_dir():
    root = Path(tempfile.mkdtemp(prefix="ownershipassign_empty_"))
    n = _patch_missing_ownership_assignment(root)
    assert n == 0


def test_get_handler_not_touched():
    # Only POST handlers are in scope -- a GET with the same shape (there
    # usually isn't a db.add() in a GET, but confirm decorator gating too).
    route = '''\
from fastapi import APIRouter, Depends
from app.database import get_db
from app.models.tasks import Task
from app.utils.auth import get_current_user

router = APIRouter()

@router.get("/tasks")
def list_tasks(db=Depends(get_db), current_user=Depends(get_current_user)):
    task = Task(title="x")
    db.add(task)
    db.commit()
    return task
'''
    root = _make_project({"app/models/tasks.py": _TASK_MODEL, "app/routes/task_routes.py": route})
    n = _patch_missing_ownership_assignment(root)
    out = (root / "app" / "routes" / "task_routes.py").read_text(encoding="utf-8")
    assert n == 0
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
