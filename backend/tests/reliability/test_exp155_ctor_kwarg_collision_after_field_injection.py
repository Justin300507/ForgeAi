"""
Exp155 (community_tool_library + freelance_job_board, 2026-07-31): both
apps scored 74.5/76.5 with POST /tools and POST /job_postings 500ing on
every single request. Root cause: `patch_missing_required_constructor_
kwargs` (database_patcher.py) appends a new trailing kwarg (e.g.
`availability=False`) to a `Model(**{k: v for k, v in x.dict().items()
if k in Model.__table__.columns.keys()}, ...)` call to satisfy a NOT
NULL column the schema doesn't supply -- but `run_deterministic_
patches` (which contains `_patch_filtered_ctor_kwarg_collision`, the
function that adds `and k not in {...}` exclusions to prevent exactly
this collision) always runs BEFORE it at both of v6_orchestrator.py's
call sites, so the newly-added kwarg is never checked against the
dict-comprehension unpack that would ALSO supply it at runtime --
`TypeError: got multiple values for keyword argument 'availability'`.

This test reproduces the real bug end to end using the two real
patcher functions in their (fixed) v6_orchestrator.py call order,
confirming the composition self-heals the collision the field-injector
itself just introduced.

Run directly: python tests/reliability/test_exp155_ctor_kwarg_collision_after_field_injection.py
"""
import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.database_patcher import patch_missing_required_constructor_kwargs
from app.services.deterministic_patcher import _patch_filtered_ctor_kwarg_collision


def _project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="exp155_test_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


MODEL_TOOL = '''\
from sqlalchemy import Column, Integer, String, Boolean
from app.database import Base

class Tool(Base):
    __tablename__ = "tools"
    id = Column(Integer, primary_key=True, nullable=False)
    name = Column(String, nullable=False)
    availability = Column(Boolean, nullable=False)
'''

SCHEMA_TOOL = '''\
from pydantic import BaseModel, Field
from typing import Optional

class ToolCreate(BaseModel):
    model_config = {"from_attributes": True}
    name: str = Field(min_length=1)
'''

ROUTE_TOOL = '''\
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.tool import Tool
from app.schemas.tool import ToolCreate

tool_router = APIRouter()

@tool_router.post("/tools")
def create_tool(tool_in: ToolCreate, db: Session = Depends(get_db)):
    tool = Tool(**{k: v for k, v in tool_in.dict().items() if k in Tool.__table__.columns.keys()})
    db.add(tool)
    db.commit()
    db.refresh(tool)
    return tool
'''


def test_field_injector_then_collision_fixer_self_heals():
    root = _project({
        "app/models/tool.py": MODEL_TOOL,
        "app/schemas/tool.py": SCHEMA_TOOL,
        "app/routes/tool_routes.py": ROUTE_TOOL,
    })
    try:
        # Exact v6_orchestrator.py call order after the fix: field-injector
        # first (introduces the collision), collision-fixer second (heals it).
        n1 = patch_missing_required_constructor_kwargs(str(root))
        route_file = root / "app" / "routes" / "tool_routes.py"
        mid_state = route_file.read_text(encoding="utf-8")
        assert "availability=False" in mid_state, "field injector should have added the kwarg"
        assert "and k not in" not in mid_state, "collision must exist before the fixer runs"

        n2 = _patch_filtered_ctor_kwarg_collision(root)
        assert n2 == 1, "collision fixer should have found and fixed the newly-introduced collision"

        out = route_file.read_text(encoding="utf-8")
        ast.parse(out)
        assert "and k not in {'availability'}" in out
        assert "availability=False" in out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_collision_fixer_is_noop_once_already_converged():
    """Sanity/idempotency: once the pair has run once and converged,
    running the collision fixer again on the already-fixed file is a
    true no-op (it never re-wraps or double-excludes)."""
    root = _project({
        "app/models/tool.py": MODEL_TOOL,
        "app/schemas/tool.py": SCHEMA_TOOL,
        "app/routes/tool_routes.py": ROUTE_TOOL,
    })
    try:
        patch_missing_required_constructor_kwargs(str(root))
        _patch_filtered_ctor_kwarg_collision(root)
        route_file = root / "app" / "routes" / "tool_routes.py"
        converged = route_file.read_text(encoding="utf-8")

        n_again = _patch_filtered_ctor_kwarg_collision(root)
        assert n_again == 0
        assert route_file.read_text(encoding="utf-8") == converged
    finally:
        shutil.rmtree(root, ignore_errors=True)


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
