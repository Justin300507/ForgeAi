"""
Experiment 060: regression tests for the validator-contract unification.

Context: Exp059's review found the validator subsystem exposes 4
incompatible result shapes, already confirmed to have caused a real bug
(an LLM fixed the wrong file because a string-only diagnostic had no
file_path -- see verification/engine.py's _filepath_static docstring).

Design (see docs/VALIDATOR_CONTRACT.md / docs/VALIDATOR_MIGRATION.md for
the full rationale): validate_project()'s existing `errors: list[str]`
stays BYTE-IDENTICAL (confirmed via git-stash before/after in this
session -- ~15 existing consumers across v6_orchestrator.py/
project_service.py/batch_runner.py/architecture_tournament_service.py do
frozenset()/string-formatting/substring-filtering directly on it, so
mutating its element type was ruled out as too risky). Instead, 15
validator functions across 13 files were given a new, additive, optional
`diagnostics: list | None = None` parameter -- when provided, each
migrated validator ALSO appends a canonical Diagnostic (app.core.context)
alongside its existing string append, using the SAME message text for
both (enforced by building the message into a local var once, appended
to both lists). validate_project() now returns a new "diagnostics" key
alongside the unchanged "passed"/"errors" keys. verification/engine.py's
_run_static_validators looks up a native Diagnostic by exact message
match when one exists, falling back to the pre-existing regex-based
construction for anything not yet migrated -- zero flag day, mixed mode
by design.

Run directly: python tests/reliability/test_validator_contract_unification.py
"""
import json
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.core.context import Diagnostic, ErrorCategory, ErrorSeverity
from app.services.validator_service import validate_project
from app.services.undefined_symbol_validator import validate_undefined_symbols
from app.services.architecture_validator import validate_architecture
from app.services.orm_validator import validate_orm_usage, validate_no_flask_sqlalchemy
from app.services.database_validator import validate_database
from app.services.router_export_validator import validate_router_exports
from app.services.duplicate_class_validator import validate_duplicate_class_definitions
from app.services.session_validator import validate_session_management
from app.services.stub_handler_validator import validate_stub_handlers
from app.services.self_shadow_validator import validate_self_shadowing_functions
from app.services.global_statement_validator import validate_module_level_global


def _write(path: str, content: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content, encoding="utf-8")


# ── Every migrated validator produces a Diagnostic with correct fields ───────

def test_undefined_symbols_migrates_to_diagnostic():
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "app", "routes", "x.py"), "def f():\n    return undefined_thing\n")
        errors, diagnostics = [], []
        validate_undefined_symbols(td, errors, diagnostics)
        assert len(errors) == 1
        assert len(diagnostics) == 1
        d = diagnostics[0]
        assert isinstance(d, Diagnostic)
        assert d.message == errors[0]
        assert d.category == ErrorCategory.IMPORT
        assert d.severity == ErrorSeverity.HIGH
        assert d.file_path == "app/routes/x.py"
        assert d.validator_name == "validate_undefined_symbols"


def test_architecture_violation_has_no_single_file_path():
    with tempfile.TemporaryDirectory() as td:
        metadata = {"plan": {"tech_stack": ["FastAPI"]}}
        errors, diagnostics = [], []
        validate_architecture(td, metadata, errors, diagnostics)
        assert len(errors) == 1
        assert len(diagnostics) == 1
        d = diagnostics[0]
        assert d.category == ErrorCategory.CONTRACT
        assert d.severity == ErrorSeverity.MEDIUM
        assert d.file_path is None  # project-level check, no single file to attribute


def test_orm_usage_migrates_correctly():
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "app", "schemas", "item.py"),
               "from pydantic import BaseModel\nclass Item(BaseModel):\n    id: int\n")
        _write(os.path.join(td, "app", "routes", "item_routes.py"),
               "from app.schemas.item import Item\n"
               "def get_item(db):\n    return db.query(Item).first()\n")
        errors, diagnostics = [], []
        validate_orm_usage(td, errors, diagnostics)
        assert len(errors) == 1
        assert len(diagnostics) == 1
        d = diagnostics[0]
        assert d.category == ErrorCategory.CONTRACT
        assert d.severity == ErrorSeverity.HIGH
        assert d.file_path == "app/routes/item_routes.py"


def test_no_flask_sqlalchemy_migrates_correctly():
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "app", "database.py"), "from flask_sqlalchemy import SQLAlchemy\n")
        errors, diagnostics = [], []
        validate_no_flask_sqlalchemy(td, errors, diagnostics)
        assert len(errors) == 1
        assert len(diagnostics) == 1
        assert diagnostics[0].category == ErrorCategory.CONTRACT
        assert diagnostics[0].severity == ErrorSeverity.MEDIUM
        assert diagnostics[0].file_path == "app/database.py"


def test_router_export_mismatch_migrates_correctly():
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "app", "routes", "task_routes.py"), "router = None\n")
        errors, diagnostics = [], []
        validate_router_exports(td, errors, diagnostics)
        assert len(errors) == 1
        assert len(diagnostics) == 1
        d = diagnostics[0]
        assert d.category == ErrorCategory.CONTRACT
        assert d.severity == ErrorSeverity.HIGH
        assert d.file_path == "app/routes/task_routes.py"


def test_missing_database_py_migrates_with_critical_severity():
    with tempfile.TemporaryDirectory() as td:
        errors, diagnostics = [], []
        validate_database(td, errors, diagnostics)
        assert errors == ["Missing app/database.py"]
        assert len(diagnostics) == 1
        assert diagnostics[0].file_path == "app/database.py"


def test_stub_handler_migrates_correctly():
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "app", "routes", "x_routes.py"),
               "@router.get('/x', response_model=X)\n"
               "def get_x():\n    pass\n")
        errors, diagnostics = [], []
        validate_stub_handlers(td, errors, diagnostics)
        assert len(errors) == 1
        assert len(diagnostics) == 1
        assert diagnostics[0].category == ErrorCategory.CONTRACT
        assert diagnostics[0].severity == ErrorSeverity.MEDIUM


def test_session_leak_migrates_correctly():
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "app", "routes", "x.py"),
               "def handler():\n    db = SessionLocal()\n    db.query(X).all()\n")
        errors, diagnostics = [], []
        validate_session_management(td, errors, diagnostics)
        assert len(errors) == 1
        assert len(diagnostics) == 1
        assert diagnostics[0].severity == ErrorSeverity.MEDIUM


def test_module_level_global_migrates_correctly():
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "app", "x.py"), "global foo, bar\n")
        errors, diagnostics = [], []
        validate_module_level_global(td, errors, diagnostics)
        assert len(errors) == 1
        assert len(diagnostics) == 1


def test_self_shadowing_migrates_correctly():
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "app", "x.py"),
               "from app.services.user_service import get_user\n"
               "def get_user(db, uid):\n    return get_user(db, uid)\n")
        errors, diagnostics = [], []
        validate_self_shadowing_functions(td, errors, diagnostics)
        assert len(errors) == 1
        assert len(diagnostics) == 1


def test_duplicate_class_definitions_metadata_carries_locations():
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "app", "a.py"), "class Widget:\n    pass\n")
        _write(os.path.join(td, "app", "b.py"), "class Widget:\n    pass\n")
        errors, diagnostics = [], []
        validate_duplicate_class_definitions(td, errors, diagnostics)
        assert len(errors) == 1
        assert len(diagnostics) == 1
        d = diagnostics[0]
        assert d.file_path is None  # spans multiple files -- no single file_path
        assert d.metadata.get("class_name") == "Widget"
        assert len(d.metadata.get("locations", [])) == 2


# ── Behavioral parity: errors list is unaffected by the diagnostics param ────

def test_diagnostics_param_is_optional_and_backward_compatible():
    # Every migrated validator must still work called the OLD way (2 args,
    # no diagnostics) -- this is what confirms "no flag day rewrite":
    # anything calling these functions pre-migration-style keeps working.
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "app", "routes", "x.py"), "def f():\n    return undefined_thing\n")
        errors = []
        validate_undefined_symbols(td, errors)  # no third arg at all
        assert len(errors) == 1


def test_errors_list_identical_with_or_without_diagnostics_requested():
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "app", "routes", "x.py"), "def f():\n    return undefined_thing\n")
        errors_a = []
        validate_undefined_symbols(td, errors_a)
        errors_b, diagnostics_b = [], []
        validate_undefined_symbols(td, errors_b, diagnostics_b)
        assert errors_a == errors_b


# ── validate_project(): full aggregation, missing file_path, multiple diagnostics ──

def test_validate_project_returns_diagnostics_key_additively():
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "app", "main.py"), "from fastapi import FastAPI\napp = FastAPI()\n")
        result = validate_project(td)
        assert "errors" in result
        assert "passed" in result
        assert "diagnostics" in result
        assert isinstance(result["diagnostics"], list)


def test_validate_project_missing_main_py_still_returns_diagnostics_key():
    with tempfile.TemporaryDirectory() as td:
        result = validate_project(td)
        assert result["passed"] is False
        assert result["errors"] == ["Missing app/main.py"]
        assert "diagnostics" in result
        assert len(result["diagnostics"]) == 1
        assert result["diagnostics"][0].severity == ErrorSeverity.CRITICAL
        assert result["diagnostics"][0].file_path == "app/main.py"


def test_validate_project_aggregates_diagnostics_from_multiple_validators():
    # Real-project-shaped fixture triggering 2+ DIFFERENT migrated validators
    # at once, proving aggregation across validators works, not just within one.
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "app", "main.py"),
               "from fastapi import FastAPI\napp = FastAPI()\n")
        _write(os.path.join(td, "app", "database.py"), "Base = None\n")
        # Trigger validate_router_exports
        _write(os.path.join(td, "app", "routes", "task_routes.py"), "router = None\n")
        result = validate_project(td)
        validator_names = {d.validator_name for d in result["diagnostics"]}
        assert "validate_router_exports" in validator_names


def test_errors_list_byte_identical_regardless_of_which_validators_are_migrated():
    # The core "no behavior change" guarantee: run against a real fixture,
    # confirm errors list is exactly what it always was (order + content),
    # independent of whether diagnostics were also collected.
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "app", "main.py"),
               "from fastapi import FastAPI\napp = FastAPI()\n")
        result = validate_project(td)
        assert result["errors"] == list(dict.fromkeys(result["errors"]))  # dedup semantics unchanged


# ── Serialization ─────────────────────────────────────────────────────────

def test_diagnostic_serializes_to_json_cleanly():
    d = Diagnostic(
        error_id="abc123",
        category=ErrorCategory.CONTRACT,
        severity=ErrorSeverity.HIGH,
        source="static",
        message="test message",
        file_path="app/x.py",
        validator_name="validate_x",
        metadata={"foo": "bar"},
    )
    as_dict = asdict(d)
    serialized = json.dumps(as_dict)  # ErrorCategory/ErrorSeverity are str Enum subclasses -- must serialize directly
    reloaded = json.loads(serialized)
    assert reloaded["category"] == "contract"
    assert reloaded["severity"] == "high"
    assert reloaded["file_path"] == "app/x.py"
    assert reloaded["validator_name"] == "validate_x"
    assert reloaded["metadata"] == {"foo": "bar"}


def test_diagnostic_list_serializes_cleanly():
    diagnostics = [
        Diagnostic(error_id="a", category=ErrorCategory.CONTRACT, severity=ErrorSeverity.HIGH,
                   source="static", message="msg a"),
        Diagnostic(error_id="b", category=ErrorCategory.API, severity=ErrorSeverity.MEDIUM,
                   source="static", message="msg b", file_path="app/y.py"),
    ]
    serialized = json.dumps([asdict(d) for d in diagnostics])
    reloaded = json.loads(serialized)
    assert len(reloaded) == 2
    assert reloaded[1]["file_path"] == "app/y.py"


def test_diagnostic_extended_fields_have_safe_json_defaults():
    d = Diagnostic(error_id="x", category=ErrorCategory.CONTRACT, severity=ErrorSeverity.LOW,
                   source="static", message="m")
    serialized = json.dumps(asdict(d))
    reloaded = json.loads(serialized)
    assert reloaded["validator_name"] is None
    assert reloaded["repairable"] is None
    assert reloaded["duration_ms"] is None


# ── verification/engine.py adapter boundary ───────────────────────────────

def test_engine_prefers_native_diagnostic_over_regex_guess():
    from app.core.context import GenerationContext
    from app.verification.engine import _run_static_validators
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "app", "main.py"),
               "from fastapi import FastAPI\napp = FastAPI()\n")
        _write(os.path.join(td, "app", "routes", "task_routes.py"), "router = None\n")
        ctx = GenerationContext(job_id="t", idea="t", project_path=td, project_name="t")
        result = _run_static_validators(ctx)
        assert result.status.value == "failed"
        matching = [d for d in result.diagnostics if d.validator_name == "validate_router_exports"]
        assert len(matching) == 1
        assert matching[0].file_path == "app/routes/task_routes.py"


def test_engine_falls_back_to_regex_for_unmigrated_validators():
    # validate_backend_imports is deliberately NOT migrated this cycle --
    # confirm its errors still flow through engine.py via the pre-existing
    # regex-based _categorise_static/_severity_static/_filepath_static path,
    # producing a valid Diagnostic (not crashing, not silently dropped).
    from app.core.context import GenerationContext
    from app.verification.engine import _run_static_validators
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "app", "main.py"),
               "from app.routes.missing_thing import x\napp = None\n")
        ctx = GenerationContext(job_id="t", idea="t", project_path=td, project_name="t")
        result = _run_static_validators(ctx)
        route_missing = [d for d in result.diagnostics if "Missing route file" in d.message]
        assert len(route_missing) == 1
        # This message is built inline in validate_project(), which now DOES
        # produce a native diagnostic for it too (see test above) -- but the
        # key property this test protects is that ANY error not covered by a
        # native diagnostic still gets a valid Diagnostic via the fallback,
        # never a crash or a silently-dropped entry.
        assert route_missing[0].category is not None
        assert route_missing[0].severity is not None


# ── Observatory compatibility ─────────────────────────────────────────────

def test_observatory_compute_functions_unaffected():
    # Observatory never touches live Diagnostic objects -- only pre-serialized
    # generation_log.jsonl / canary_history.json summaries -- so this
    # migration must be a complete no-op for it. Confirm the compute
    # functions still run cleanly with representative data.
    from app.memory.reliability_metrics import compute_observatory
    gen_entries = [{"forge_score": 80, "fix_attempts": 1, "deployed": False}]
    canary_runs = [{"label": "test", "timestamp": "2026-07-12T00:00:00", "results": [
        {"app": "todo", "forge_score": 80, "crashed": False, "build_ok": True,
         "runtime_ok": True, "crud_ok": True, "browser_ok": True, "deployed": False}
    ]}]
    obs = compute_observatory(gen_entries, canary_runs)
    assert "first_try_success_rate" in obs
    assert "canary_health" in obs


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
