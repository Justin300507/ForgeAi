"""
Exp107: hyphenated router identifiers/filenames are a SyntaxError (or an
un-importable module) that kills main.py outright.

Confirmed live twice (ForgeBench v1 era): hospital_management_system
(`consultation-note_router`) and real_estate_marketplace
(`agent-dashboard_router` + `agent-dashboard_routes.py` as an actual
filename). After the patch both real apps import and build OpenAPI (see
experiments.md Exp107 for the corpus A/B).

Run directly: python tests/reliability/test_exp107_hyphenated_routers.py
"""
import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import _patch_hyphenated_router_identifiers


def _project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="exp107_test_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def test_sanitizes_identifier_and_dedupes_repair_added_twin():
    """hospital's exact shape: the broken hyphen line AND the correctly
    spelled line the repair loop added later both present."""
    root = _project({
        "app/main.py": (
            "from fastapi import FastAPI\n"
            "from app.routes.consultation_note_routes import consultation-note_router\n"
            "from app.routes.consultation_note_routes import consultation_note_router\n\n"
            "app = FastAPI()\n"
            "app.include_router(consultation-note_router)\n"
            "app.include_router(consultation_note_router)\n"
        ),
        "app/routes/consultation_note_routes.py": (
            "from fastapi import APIRouter\nconsultation_note_router = APIRouter()\n"
        ),
    })
    try:
        assert _patch_hyphenated_router_identifiers(root) >= 1
        out = (root / "app/main.py").read_text(encoding="utf-8")
        ast.parse(out)
        assert "-" not in out.split("FastAPI\n")[1].split("app = ")[0]
        assert out.count("import consultation_note_router") == 1
        assert out.count("app.include_router(consultation_note_router)") == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_renames_hyphenated_route_file_and_rewrites_module_refs():
    """real_estate's exact shape: the FILENAME itself is hyphenated, so
    the module can never be imported regardless of identifier spelling."""
    root = _project({
        "app/main.py": (
            "from fastapi import FastAPI\n"
            "from app.routes.agent-dashboard_routes import agent-dashboard_router\n\n"
            "app = FastAPI()\n"
            "app.include_router(agent-dashboard_router)\n"
        ),
        "app/routes/agent-dashboard_routes.py": (
            "from fastapi import APIRouter\nagent-dashboard_router = APIRouter()\n"
        ),
    })
    try:
        assert _patch_hyphenated_router_identifiers(root) >= 2
        assert (root / "app/routes/agent_dashboard_routes.py").exists()
        assert not (root / "app/routes/agent-dashboard_routes.py").exists()
        main_out = (root / "app/main.py").read_text(encoding="utf-8")
        ast.parse(main_out)
        assert "from app.routes.agent_dashboard_routes import agent_dashboard_router" in main_out
        route_out = (root / "app/routes/agent_dashboard_routes.py").read_text(encoding="utf-8")
        ast.parse(route_out)
        assert "agent_dashboard_router = APIRouter()" in route_out
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
        assert _patch_hyphenated_router_identifiers(root) == 0
        assert (root / "app/main.py").read_text(encoding="utf-8") == before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_orphan_hyphenated_twin_deleted_when_correct_twin_exists():
    """Exp147 (meal_prep_planner, 2026-07-30): leaving the orphan hyphenated
    twin in place (old behavior) let later regen/wiring passes read its raw
    `<name>-router` identifier and copy it into main.py next to the
    *correct* module path -- `from app.routes.meal_plan_routes import
    meal-plan_router` -- a NameError even though every individual hyphen
    elsewhere was already fixed. The dead duplicate must be deleted, not
    content-patched-in-place, so there is no stale identifier left to copy."""
    root = _project({
        "app/routes/tag-extra_routes.py": "from fastapi import APIRouter\ntag-extra_router = APIRouter()\n",
        "app/routes/tag_extra_routes.py": "from fastapi import APIRouter\ntag_extra_router = APIRouter()\n",
    })
    try:
        _patch_hyphenated_router_identifiers(root)
        assert (root / "app/routes/tag_extra_routes.py").exists()
        assert not (root / "app/routes/tag-extra_routes.py").exists()
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
