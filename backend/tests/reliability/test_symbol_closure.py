"""
Verifies Stage 2a-symbols (_run_symbol_closure_check in
app/verification/engine.py): every `from local.module import X` name must
actually be defined in that module, not just the module file existing.

Root cause this catches (confirmed live, 2026-07-11, real generated
output): app/verification/engine.py's existing import-closure check (Stage
2a) only verifies the MODULE resolves to a file -- a route file importing
a schema CLASS the schema file never defines (wrong name, a rename that
missed a call site) passes it silently and crashes at boot with
ImportError. A corpus sweep of 53 real generated_projects/ output found
this exact bug in 8 of them (~15%) once this check existed.

Also verifies the two false-positive fixes made while validating against
that corpus: `from package import submodule` is valid whenever the
submodule file exists (even with an empty/no __init__.py -- namespace
packages, PEP 420), regardless of whether it's literally named inside
__init__.py.

Run directly: python tests/reliability/test_symbol_closure.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.verification.engine import _run_symbol_closure_check
from app.core.context import GenerationContext


def _make_project(files: dict) -> Path:
    """files: {relative_path: content}. Returns the project root."""
    root = Path(tempfile.mkdtemp(prefix="symclosure_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def _check(root: Path):
    ctx = GenerationContext(job_id="t", idea="x", project_path=root, project_name="t")
    try:
        return _run_symbol_closure_check(ctx)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_flags_a_class_the_target_module_never_defines():
    root = _make_project({
        "app/schemas/contact.py": "class ContactNoteCreate:\n    pass\n",
        "app/routes/contact_routes.py":
            "from app.schemas.contact import NoteCreate\n",
    })
    result = _check(root)
    assert result.status.value == "failed"
    assert len(result.diagnostics) == 1
    assert "NoteCreate" in result.diagnostics[0].message
    assert "app.schemas.contact" in result.diagnostics[0].message


def test_passes_when_the_imported_name_is_actually_defined():
    root = _make_project({
        "app/schemas/contact.py": "class NoteCreate:\n    pass\n",
        "app/routes/contact_routes.py":
            "from app.schemas.contact import NoteCreate\n",
    })
    result = _check(root)
    assert result.status.value == "passed"
    assert result.diagnostics == []


def test_submodule_import_from_package_is_not_flagged():
    """`from app import schemas` where app/schemas.py exists is valid
    Python even though app/__init__.py never names `schemas`."""
    root = _make_project({
        "app/__init__.py": "",
        "app/schemas.py": "class Foo:\n    pass\n",
        "app/routes.py": "from app import schemas\n",
    })
    result = _check(root)
    assert result.status.value == "passed"


def test_namespace_package_submodule_import_is_not_flagged():
    """A directory with .py files but NO __init__.py is still a real,
    importable namespace package (PEP 420) -- confirmed live on todoapp's
    app/schemas/ (task.py + user.py, no __init__.py)."""
    root = _make_project({
        "app/__init__.py": "",
        "app/schemas/task.py": "class TaskOut:\n    pass\n",
        "app/routes.py": "from app import schemas\n",
    })
    result = _check(root)
    assert result.status.value == "passed"


def test_genuinely_missing_submodule_is_still_flagged():
    root = _make_project({
        "app/__init__.py": "",
        "app/routes.py": "from app import crud\n",
    })
    result = _check(root)
    assert result.status.value == "failed"
    assert "crud" in result.diagnostics[0].message


def test_star_import_in_target_module_is_not_flagged():
    """Can't statically know what `from x import *` re-exports -- must not
    guess and risk a false positive."""
    root = _make_project({
        "app/schemas/base.py": "class Anything:\n    pass\n",
        "app/schemas/contact.py": "from app.schemas.base import *\n",
        "app/routes/contact_routes.py":
            "from app.schemas.contact import WhoKnowsWhatThisIs\n",
    })
    result = _check(root)
    assert result.status.value == "passed"


def test_reexport_via_import_in_target_module_is_not_flagged():
    """A name imported INTO a module becomes part of its namespace too --
    a common re-export pattern (`app/schemas/__init__.py` importing from
    submodules so callers can `from app.schemas import UserOut`)."""
    root = _make_project({
        "app/schemas/user.py": "class UserOut:\n    pass\n",
        "app/schemas/__init__.py": "from app.schemas.user import UserOut\n",
        "app/routes/user_routes.py": "from app.schemas import UserOut\n",
    })
    result = _check(root)
    assert result.status.value == "passed"


def test_unresolved_module_is_skipped_not_double_flagged():
    """A module that doesn't resolve to any file at all is Stage 2a's
    (import closure) job to flag, not this stage's -- avoid a confusing
    double diagnostic for the same root problem."""
    root = _make_project({
        "app/routes.py": "from app.schemas.nonexistent import Foo\n",
    })
    result = _check(root)
    assert result.status.value == "passed"
    assert result.diagnostics == []


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
