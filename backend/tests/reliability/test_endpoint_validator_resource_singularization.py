"""
Regression tests for _singularize_resource (app/services/endpoint_validator.py).

Root cause confirmed live (simple_crm canary run, 2026-09-23): a plain
`.rstrip("s")` at both call sites in this module turned a frontend call to
"/activities" into resource name "activitie" (neither a real word nor
prefix-matchable against any real model), producing the malformed
`app/routes/activitie_routes.py` in the "Missing endpoint" repair message
-- guaranteeing the repair loop's missing-endpoint grounding fix could
never match it to a real model/schema. Same class of bug already fixed
once elsewhere in this codebase (deterministic_patcher.py's
`_find_resource_model_and_schema`: "Classes" minus a trailing "s" is
"Classe", not "Class") -- this brings the same tested algorithm
(app/contract/adapter.py's `_singularize`) to this module's two
independent, previously-unfixed call sites.

Run directly: python tests/reliability/test_endpoint_validator_resource_singularization.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.endpoint_validator import (
    _singularize_resource,
    validate_frontend_api_calls,
)


def test_ies_plurals_singularize_correctly():
    assert _singularize_resource("activities") == "activity"
    assert _singularize_resource("categories") == "category"
    assert _singularize_resource("classes") == "class"


def test_ses_xes_ches_plurals_singularize_correctly():
    assert _singularize_resource("statuses") == "status"
    assert _singularize_resource("boxes") == "box"


def test_invariant_words_untouched():
    assert _singularize_resource("status") == "status"
    assert _singularize_resource("series") == "series"
    assert _singularize_resource("species") == "species"
    assert _singularize_resource("news") == "news"


def test_plain_s_plurals_singularize_correctly():
    assert _singularize_resource("items") == "item"
    assert _singularize_resource("users") == "user"
    assert _singularize_resource("tasks") == "task"


def test_already_singular_words_untouched():
    assert _singularize_resource("item") == "item"
    assert _singularize_resource("class") == "class"


def test_validate_frontend_api_calls_uses_real_singular_filename(tmp_path):
    project = tmp_path
    src_dir = project / "src" / "pages"
    src_dir.mkdir(parents=True)
    (src_dir / "Dashboard.jsx").write_text(
        "import API from '../api';\n"
        "async function load() { const r = await API.get('/activities'); }\n",
        encoding="utf-8",
    )
    routes_dir = project / "app" / "routes"
    routes_dir.mkdir(parents=True)
    (routes_dir / "contact_routes.py").write_text(
        "from fastapi import APIRouter\n"
        "contact_router = APIRouter()\n"
        "@contact_router.get('/contacts')\n"
        "def get_contacts():\n"
        "    return []\n",
        encoding="utf-8",
    )
    (project / "app" / "main.py").write_text(
        "from app.routes.contact_routes import contact_router\n", encoding="utf-8",
    )

    errors = []
    validate_frontend_api_calls(str(project), errors)
    assert len(errors) == 1
    assert "app/routes/activity_routes.py" in errors[0]
    assert "activitie_routes.py" not in errors[0]


if __name__ == "__main__":
    import traceback
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            if t.__code__.co_argcount == 0:
                t()
            else:
                import tempfile
                from pathlib import Path
                t(Path(tempfile.mkdtemp(prefix="epval_")))
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
