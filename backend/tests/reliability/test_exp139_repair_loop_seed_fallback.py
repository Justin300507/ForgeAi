"""
Exp139 (part 2): the repair-loop's seed_routes.py fallback
(app/repair/orchestrator.py's `_apply_fix_group`) now tries the
deterministic ADR-002 seeder before writing the old zero-insert stub.

Reproduces the live todo canary incident (2026-07-22): the LLM-generated
seed_routes.py crashed at runtime (`TypeError: ... got multiple values for
keyword argument`), the repair loop's `_is_seed_related_group` branch
caught it and replaced the file with `_SAFE_SEED_ROUTES_STUB` -- which
inserts ZERO rows into any table. `priorities` stayed empty for the rest
of the run, so every `POST /tasks` with a required `priority_id` foreign
key failed with IntegrityError no matter what id the CRUD journey tried
(the FK-reference-lookup fix in test_exp139_fk_reference_lookup.py cannot
help here: there is no real row to discover). v6_orchestrator.py's
INITIAL generation path already called the deterministic seeder in
exactly this situation; the repair-loop path (triggered when a crash is
caught AFTER initial generation, not during it) never did.

Run directly: python tests/reliability/test_exp139_repair_loop_seed_fallback.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.core.context import Diagnostic, DiagnosticGroup, ErrorCategory, ErrorSeverity, GenerationContext
from app.repair.orchestrator import _apply_fix_group, _SAFE_SEED_ROUTES_STUB
from app.retry.manager import StrategyConfig
from app.core.context import FixStrategy


def _tmp_project_with_lookup_entity() -> Path:
    """A minimal project shaped like the live todo incident: a Task model
    with a required FK to a Priority lookup entity."""
    root = Path(tempfile.mkdtemp(prefix="exp139_seed_"))
    models = root / "app" / "models"
    routes = root / "app" / "routes"
    models.mkdir(parents=True)
    routes.mkdir(parents=True)
    (models / "__init__.py").write_text("", encoding="utf-8")
    (models / "priority.py").write_text(
        "from sqlalchemy import Column, Integer, String\n"
        "from app.database import Base\n\n"
        "class Priority(Base):\n"
        "    __tablename__ = 'priorities'\n"
        "    id = Column(Integer, primary_key=True)\n"
        "    name = Column(String, nullable=False)\n",
        encoding="utf-8",
    )
    (models / "task.py").write_text(
        "from sqlalchemy import Column, Integer, String, ForeignKey\n"
        "from app.database import Base\n\n"
        "class Task(Base):\n"
        "    __tablename__ = 'tasks'\n"
        "    id = Column(Integer, primary_key=True)\n"
        "    title = Column(String, nullable=False)\n"
        "    priority_id = Column(Integer, ForeignKey('priorities.id'), nullable=False)\n",
        encoding="utf-8",
    )
    return root


def _crashing_seed_group() -> DiagnosticGroup:
    diag = Diagnostic(
        error_id="exp139-seed-crash",
        category=ErrorCategory.RUNTIME,
        severity=ErrorSeverity.CRITICAL,
        source="runtime",
        message="POST /seed returned 500",
        file_path="app/routes/seed_routes.py",
    )
    return DiagnosticGroup(
        group_id="exp139-seed-group",
        root_cause="seed_routes.py crashed",
        diagnostics=[diag],
        affected_files=["app/routes/seed_routes.py"],
        suggested_strategy=FixStrategy.PATCH_FILE,
        priority=1,
    )


def test_repair_loop_seed_fallback_prefers_deterministic_seeder_over_noop_stub():
    root = _tmp_project_with_lookup_entity()
    try:
        ctx = GenerationContext("exp139", "test", root, "exp139_app")
        cfg = StrategyConfig(1, FixStrategy.PATCH_FILE, "test", "test")

        modified, written = _apply_fix_group(_crashing_seed_group(), ctx, cfg)

        assert modified == ["app/routes/seed_routes.py"]
        content = written["app/routes/seed_routes.py"]
        # The zero-insert stub must NOT have been chosen when a real
        # lookup entity (Priority) is discoverable -- this is the exact
        # gap that let priorities stay empty in the live incident.
        assert content != _SAFE_SEED_ROUTES_STUB
        assert "Priority(" in content, (
            "deterministic seeder should generate real Priority row inserts, "
            f"got:\n{content}"
        )
        on_disk = (root / "app" / "routes" / "seed_routes.py").read_text(encoding="utf-8")
        assert on_disk == content
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_repair_loop_seed_fallback_still_writes_stub_when_generator_fails():
    """No models discoverable (generator returns None, telemetry) -- must
    still fall back to the safe zero-insert stub, never crash the repair
    loop itself, and never write nothing at all."""
    root = Path(tempfile.mkdtemp(prefix="exp139_seed_empty_"))
    (root / "app" / "routes").mkdir(parents=True)
    try:
        ctx = GenerationContext("exp139", "test", root, "exp139_app")
        cfg = StrategyConfig(1, FixStrategy.PATCH_FILE, "test", "test")

        modified, written = _apply_fix_group(_crashing_seed_group(), ctx, cfg)

        assert modified == ["app/routes/seed_routes.py"]
        assert written["app/routes/seed_routes.py"] == _SAFE_SEED_ROUTES_STUB
        on_disk = (root / "app" / "routes" / "seed_routes.py").read_text(encoding="utf-8")
        assert on_disk == _SAFE_SEED_ROUTES_STUB
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_repair_loop_seed_fallback_survives_generator_exception():
    """The generator raising outright (not just returning None) must be
    caught -- this fallback boundary is the whole point of the branch and
    must never itself become a new crash."""
    root = _tmp_project_with_lookup_entity()
    try:
        ctx = GenerationContext("exp139", "test", root, "exp139_app")
        cfg = StrategyConfig(1, FixStrategy.PATCH_FILE, "test", "test")

        with mock.patch(
            "app.services.deterministic_seed_generator.generate",
            side_effect=RuntimeError("boom"),
        ):
            modified, written = _apply_fix_group(_crashing_seed_group(), ctx, cfg)

        assert modified == ["app/routes/seed_routes.py"]
        assert written["app/routes/seed_routes.py"] == _SAFE_SEED_ROUTES_STUB
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
