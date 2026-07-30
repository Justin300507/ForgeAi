"""
Exp149: main.py can import a `*_router` symbol from a route module that
doesn't actually define it, when the correct import for that exact
symbol already exists elsewhere in the same file (imported from the
module that actually defines it). ImportError crashes the whole app
before any endpoint can run, and "Symbol closure check" never converges
because nothing removes the dead import.

Confirmed live (community_recycling_tracker, 2026-07-30): main.py had
BOTH
  from app.routes.recycling_locations_routes import recycling_locations_router
  from app.routes.recycling_location_routes import recycling_locations_router
-- the second imports a symbol that only the SINGULAR module
(recycling_location_routes.py, which defines recycling_location_router)
does not have; the plural symbol is only real in the plural module,
already correctly imported by the first line.

Run directly: python tests/reliability/test_exp149_broken_cross_module_router_import.py
"""
import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import _patch_broken_cross_module_router_import


def _project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="exp149_test_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def test_removes_broken_cross_module_import_when_correct_one_exists():
    root = _project({
        "app/main.py": (
            "from fastapi import FastAPI\n"
            "from app.routes.recycling_locations_routes import recycling_locations_router\n"
            "from app.routes.recycling_location_routes import recycling_locations_router\n"
            "from app.routes.recycling_location_routes import recycling_location_router\n\n"
            "app = FastAPI()\n"
            "app.include_router(recycling_locations_router)\n"
            "app.include_router(recycling_location_router)\n"
        ),
        "app/routes/recycling_locations_routes.py": (
            "from fastapi import APIRouter\nrecycling_locations_router = APIRouter()\n"
        ),
        "app/routes/recycling_location_routes.py": (
            "from fastapi import APIRouter\nrecycling_location_router = APIRouter()\n"
        ),
    })
    try:
        assert _patch_broken_cross_module_router_import(root) == 1
        out = (root / "app/main.py").read_text(encoding="utf-8")
        ast.parse(out)
        assert out.count("import recycling_locations_router") == 1
        assert "from app.routes.recycling_location_routes import recycling_locations_router" not in out
        assert "from app.routes.recycling_locations_routes import recycling_locations_router" in out
        assert "from app.routes.recycling_location_routes import recycling_location_router" in out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_does_not_remove_the_only_import_of_a_symbol():
    """If the broken import were the ONLY source of that symbol, removing
    it would just trade an ImportError for a NameError -- must leave it
    alone since there's nothing safe to do mechanically."""
    root = _project({
        "app/main.py": (
            "from fastapi import FastAPI\n"
            "from app.routes.foo_routes import bar_router\n\n"
            "app = FastAPI()\n"
            "app.include_router(bar_router)\n"
        ),
        "app/routes/foo_routes.py": (
            "from fastapi import APIRouter\nfoo_router = APIRouter()\n"
        ),
    })
    try:
        before = (root / "app/main.py").read_text(encoding="utf-8")
        assert _patch_broken_cross_module_router_import(root) == 0
        assert (root / "app/main.py").read_text(encoding="utf-8") == before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_clean_project_untouched():
    root = _project({
        "app/main.py": (
            "from fastapi import FastAPI\n"
            "from app.routes.task_routes import task_router\n\n"
            "app = FastAPI()\napp.include_router(task_router)\n"
        ),
        "app/routes/task_routes.py": "from fastapi import APIRouter\ntask_router = APIRouter()\n",
    })
    try:
        before = (root / "app/main.py").read_text(encoding="utf-8")
        assert _patch_broken_cross_module_router_import(root) == 0
        assert (root / "app/main.py").read_text(encoding="utf-8") == before
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
