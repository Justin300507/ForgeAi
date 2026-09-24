"""
Regression tests for _patch_response_model_for_renamed_primary_key
(app/services/deterministic_patcher.py).

Root cause confirmed live (recipe_sharing_platform, 2026-09-16
comprehensive-20-app run, Forge Score 75.08): Recipe's primary key column
is named `recipe_id`, not `id`. create_recipe/update_recipe/get_recipe
returned the bare ORM instance with no `response_model=` and no `->`
return annotation, so FastAPI's jsonable_encoder serialized it via
vars(obj) -- the real column name `recipe_id`, never `id`. The CRUD
journey runner (and any real client expecting the conventional
`{"id": ...}` shape) captured `id=None` on every create, cascading into
"no entity_id captured" on every Edit/Delete/persistence step after.

Confirmed directly: a plain `@property def id` alone is invisible to
jsonable_encoder's vars(obj) path -- fixing this needs BOTH the model
property (so Pydantic's getattr()-based from_attributes validation can
see it) AND response_model= on the route (so the object is actually
routed through Pydantic instead of vars(obj)). Verified end-to-end
against the real generated_projects/recipe_sharing_platform code that
the patched RecipeResponse.model_validate(recipe) -> jsonable_encoder
chain now yields {"id": ..., ...}, matching what a real client sees.

Run directly: python tests/reliability/test_renamed_primary_key_response_model.py
"""
import ast
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import (
    _patch_response_model_for_renamed_primary_key as _patch,
)


def _make_project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="renamedpk_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


_MODEL = '''\
from sqlalchemy import Column, Integer, Text
from app.database import Base

class Recipe(Base):
    __tablename__ = 'recipes'

    recipe_id = Column(Integer, primary_key=True, nullable=False)
    title = Column(Text, nullable=False)
'''

_SCHEMA = '''\
from pydantic import BaseModel
from typing import Optional

class RecipeCreate(BaseModel):
    title: str

class RecipeUpdate(BaseModel):
    title: Optional[str] = None

class RecipeResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    title: Optional[str] = None
'''

_ROUTES = '''\
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.recipe import Recipe
from app.schemas.recipe import RecipeCreate, RecipeUpdate

recipe_router = APIRouter()

@recipe_router.get('/recipes')
def get_recipes(limit: int = Query(50), offset: int = Query(0), db: Session = Depends(get_db)):
    recipes = db.query(Recipe).offset(offset).limit(limit).all()
    total = db.query(Recipe).count()
    return {'items': recipes, 'total': total}

@recipe_router.get('/recipes/{id}')
def get_recipe(id: int, db: Session = Depends(get_db)):
    recipe = db.query(Recipe).filter(Recipe.recipe_id == id).first()
    if not recipe:
        raise HTTPException(status_code=404, detail='Not found')
    return recipe

@recipe_router.post('/recipes', status_code=201)
def create_recipe(recipe_in: RecipeCreate, db: Session = Depends(get_db)):
    recipe = Recipe(**recipe_in.dict())
    db.add(recipe)
    db.commit()
    db.refresh(recipe)
    return recipe

@recipe_router.put('/recipes/{id}')
def update_recipe(id: int, recipe_in: RecipeUpdate, db: Session = Depends(get_db)):
    recipe = db.query(Recipe).filter(Recipe.recipe_id == id).first()
    if not recipe:
        raise HTTPException(status_code=404, detail='Not found')
    for key, value in recipe_in.dict(exclude_unset=True).items():
        setattr(recipe, key, value)
    db.commit()
    db.refresh(recipe)
    return recipe

@recipe_router.delete('/recipes/{id}', status_code=204)
def delete_recipe(id: int, db: Session = Depends(get_db)):
    recipe = db.query(Recipe).filter(Recipe.recipe_id == id).first()
    if not recipe:
        raise HTTPException(status_code=404, detail='Not found')
    db.delete(recipe)
    db.commit()
'''


def _project():
    return _make_project({
        "app/models/recipe.py": _MODEL,
        "app/schemas/recipe.py": _SCHEMA,
        "app/routes/recipe_routes.py": _ROUTES,
    })


def test_model_gets_id_property_alias():
    root = _project()
    n = _patch(root)
    assert n >= 1
    out = (root / "app" / "models" / "recipe.py").read_text(encoding="utf-8")
    ast.parse(out)
    assert "import builtins" in out
    assert "def id(self):" in out
    assert "return self.recipe_id" in out


def test_single_object_routes_get_response_model_but_list_and_delete_do_not():
    root = _project()
    _patch(root)
    out = (root / "app" / "routes" / "recipe_routes.py").read_text(encoding="utf-8")
    ast.parse(out)
    assert "def get_recipes(" in out
    lines = out.splitlines()
    dec_for = {}
    for i, ln in enumerate(lines):
        if ln.startswith("@recipe_router."):
            # associate with the next def line's function name
            for j in range(i + 1, len(lines)):
                if lines[j].startswith("def "):
                    fname = lines[j].split("def ")[1].split("(")[0]
                    dec_for[fname] = ln
                    break
    assert "response_model" not in dec_for["get_recipes"]
    assert "response_model" not in dec_for["delete_recipe"]
    assert "response_model=RecipeResponse" in dec_for["get_recipe"]
    assert "response_model=RecipeResponse" in dec_for["create_recipe"]
    assert "response_model=RecipeResponse" in dec_for["update_recipe"]
    assert "from app.schemas.recipe import RecipeResponse" in out


def test_end_to_end_id_now_appears_in_serialized_response():
    root = _project()
    _patch(root)

    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]
    old_path = sys.path[:]
    try:
        sys.path = [p for p in sys.path if p not in (".", "")]
        sys.path.insert(0, str(root))
        import app.models.recipe as recipe_mod
        import app.schemas.recipe as schema_mod
        from fastapi.encoders import jsonable_encoder

        recipe = recipe_mod.Recipe(recipe_id=42, title="Pasta")
        # Before the fix, this is what a route with no response_model sends:
        assert "id" not in jsonable_encoder(recipe)
        assert jsonable_encoder(recipe)["recipe_id"] == 42

        # After the fix, the route now declares response_model=RecipeResponse,
        # so FastAPI actually sends this instead:
        resp = schema_mod.RecipeResponse.model_validate(recipe)
        encoded = jsonable_encoder(resp)
        assert encoded == {"id": 42, "title": "Pasta"}
    finally:
        sys.path = old_path
        for mod in list(sys.modules):
            if mod == "app" or mod.startswith("app."):
                del sys.modules[mod]


def test_already_id_named_pk_untouched():
    model = '''\
from sqlalchemy import Column, Integer, Text
from app.database import Base

class Task(Base):
    __tablename__ = 'tasks'
    id = Column(Integer, primary_key=True, nullable=False)
    title = Column(Text, nullable=False)
'''
    schema = '''\
from pydantic import BaseModel

class TaskResponse(BaseModel):
    id: int
    title: str
'''
    routes = '''\
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.task import Task
from app.schemas.task import TaskCreate

task_router = APIRouter()

@task_router.get('/tasks/{id}')
def get_task(id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == id).first()
    return task
'''
    root = _make_project({
        "app/models/task.py": model,
        "app/schemas/task.py": schema,
        "app/routes/task_routes.py": routes,
    })
    n = _patch(root)
    assert n == 0
    assert (root / "app" / "models" / "task.py").read_text(encoding="utf-8") == model
    assert (root / "app" / "routes" / "task_routes.py").read_text(encoding="utf-8") == routes


def test_route_already_has_response_model_untouched():
    root = _project()
    routes_path = root / "app" / "routes" / "recipe_routes.py"
    routes_path.write_text(
        routes_path.read_text(encoding="utf-8").replace(
            "@recipe_router.post('/recipes', status_code=201)",
            "@recipe_router.post('/recipes', status_code=201, response_model=dict)",
        ),
        encoding="utf-8",
    )
    _patch(root)
    out = routes_path.read_text(encoding="utf-8")
    assert out.count("response_model=") == out.count("response_model=dict") + 2  # get_recipe + update_recipe only
    assert "response_model=dict" in out  # untouched, not duplicated/overwritten


def test_idempotent_second_pass_is_noop():
    root = _project()
    _patch(root)
    model_first = (root / "app" / "models" / "recipe.py").read_text(encoding="utf-8")
    routes_first = (root / "app" / "routes" / "recipe_routes.py").read_text(encoding="utf-8")
    n2 = _patch(root)
    assert n2 == 0
    assert (root / "app" / "models" / "recipe.py").read_text(encoding="utf-8") == model_first
    assert (root / "app" / "routes" / "recipe_routes.py").read_text(encoding="utf-8") == routes_first


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
