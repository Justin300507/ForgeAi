"""
Exp106: quoted (ForwardRef) annotations in route files 500 /openapi.json.

Two confirmed live shapes:
- restaurant_pos_system: `response_model=List["SaleOut"]` with the import
  deferred inside the handler AND app/schemas/sale.py never generated;
- expense_tracker (Railway, 2026-07-15): `budget_in: "BudgetCreate"` —
  `PydanticUserError: 'BudgetCreate' is not fully defined`, breaking /docs
  and blinding the journey's schema introspection (Exp105's precondition).

Fixes under test:
- _patch_quoted_route_annotations: unquotes annotation-position strings
  that name a real app/schemas|models class and hoists the import to
  module level (column-0 anchor — an indented function-body import must
  not be used as the insertion point);
- _SCHEMA_IMPORT_RE accepts indented imports, so
  _patch_create_missing_schemas stubs modules that only a function-body
  import references.

Run directly: python tests/reliability/test_exp106_quoted_annotations.py
"""
import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import (
    _patch_create_missing_schemas,
    _patch_quoted_route_annotations,
)


def _project(routes: dict, schemas: dict, models: dict | None = None) -> Path:
    root = Path(tempfile.mkdtemp(prefix="exp106_test_"))
    for rel, content in {
        **{f"app/routes/{n}": c for n, c in routes.items()},
        **{f"app/schemas/{n}": c for n, c in schemas.items()},
        **{f"app/models/{n}": c for n, c in (models or {}).items()},
    }.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    (root / "app" / "schemas").mkdir(parents=True, exist_ok=True)
    (root / "app" / "schemas" / "__init__.py").touch()
    return root


_SCHEMA_BUDGET = "from pydantic import BaseModel\n\nclass BudgetCreate(BaseModel):\n    amount: float\n"


def test_unquotes_response_model_and_hoists_import():
    root = _project(
        routes={"sale_routes.py": (
            "from fastapi import APIRouter\n"
            "from typing import List\n\n"
            "router = APIRouter()\n\n"
            '@router.get("/sales", response_model=List["SaleOut"])\n'
            "def get_sales():\n"
            "    from app.schemas.sale import SaleOut\n"
            "    return []\n"
        )},
        schemas={"sale.py": "from pydantic import BaseModel\n\nclass SaleOut(BaseModel):\n    id: int\n"},
    )
    try:
        assert _patch_quoted_route_annotations(root) == 1
        out = (root / "app/routes/sale_routes.py").read_text(encoding="utf-8")
        assert "response_model=List[SaleOut]" in out
        assert "from app.schemas.sale import SaleOut" in out.splitlines()[2]  # module level, after imports
        ast.parse(out)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_unquotes_live_parameter_annotation_shape():
    """expense_tracker's exact shape: quoted body-param annotation."""
    root = _project(
        routes={"budget_routes.py": (
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n"
            '@router.put("/budgets/{id}")\n'
            'def update_budget(id: int, budget_in: "BudgetCreate"):\n'
            "    from app.schemas.budget import BudgetCreate\n"
            "    return budget_in\n"
        )},
        schemas={"budget.py": _SCHEMA_BUDGET},
    )
    try:
        assert _patch_quoted_route_annotations(root) == 1
        out = (root / "app/routes/budget_routes.py").read_text(encoding="utf-8")
        assert 'budget_in: BudgetCreate)' in out
        assert "from app.schemas.budget import BudgetCreate\n" in out
        # hoisted to module level (column 0), not injected mid-function
        assert any(l == "from app.schemas.budget import BudgetCreate"
                   for l in out.splitlines()[:4])
        ast.parse(out)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_dict_literals_and_unknown_names_untouched():
    src = (
        "from fastapi import APIRouter\n\n"
        "router = APIRouter()\n\n"
        '@router.get("/x")\n'
        "def x():\n"
        '    return {"role": "BudgetCreate", "msg": "SaleOut"}\n\n'
        '@router.get("/y", response_model=List["NotAClassAnywhere"])\n'
        "def y():\n"
        "    return []\n"
    )
    root = _project(routes={"misc_routes.py": src}, schemas={"budget.py": _SCHEMA_BUDGET})
    try:
        _patch_quoted_route_annotations(root)
        out = (root / "app/routes/misc_routes.py").read_text(encoding="utf-8")
        assert '{"role": "BudgetCreate", "msg": "SaleOut"}' in out
        assert 'List["NotAClassAnywhere"]' in out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_no_duplicate_import_when_already_imported():
    root = _project(
        routes={"budget_routes.py": (
            "from fastapi import APIRouter\n"
            "from app.schemas.budget import BudgetCreate\n\n"
            "router = APIRouter()\n\n"
            '@router.post("/budgets")\n'
            'def create(b: "BudgetCreate"):\n'
            "    return b\n"
        )},
        schemas={"budget.py": _SCHEMA_BUDGET},
    )
    try:
        assert _patch_quoted_route_annotations(root) == 1
        out = (root / "app/routes/budget_routes.py").read_text(encoding="utf-8")
        assert out.count("from app.schemas.budget import BudgetCreate") == 1
        assert 'b: BudgetCreate)' in out
        ast.parse(out)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_missing_schema_referenced_only_by_inner_import_gets_stubbed_then_unquoted():
    """The full restaurant_pos_system chain: no schema module at all, the
    only reference is an indented function-body import (the old column-0
    _SCHEMA_IMPORT_RE anchor was blind to it)."""
    root = _project(
        routes={"sale_routes.py": (
            "from fastapi import APIRouter\n"
            "from typing import List\n\n"
            "router = APIRouter()\n\n"
            '@router.get("/sales", response_model=List["SaleOut"])\n'
            "def get_sales():\n"
            "    from app.schemas.sale import SaleOut\n"
            "    return []\n"
        )},
        schemas={},
    )
    try:
        assert _patch_create_missing_schemas(root) >= 1
        assert (root / "app/schemas/sale.py").exists()
        assert _patch_quoted_route_annotations(root) == 1
        out = (root / "app/routes/sale_routes.py").read_text(encoding="utf-8")
        assert "response_model=List[SaleOut]" in out
        ast.parse(out)
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
