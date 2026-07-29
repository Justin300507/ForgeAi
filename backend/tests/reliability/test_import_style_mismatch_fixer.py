"""
The repair loop's LLM fix for "Import style mismatch" diagnostics
(app/repair/orchestrator.py's `_apply_fix_group`) kept oscillating
between two equally-broken states instead of converging.

Reproduces the live incident (habit_tracker, 2026-07-30): the validator
correctly diagnosed `src/hooks/useAuth.jsx` as `export default` while
`src/components/PrivateRoute.jsx` imported it as `import { useAuth } from
'../hooks/useAuth'` (named/curly-brace syntax) -- a fully mechanical
finding with only one possible correct fix (rewrite the importer to a
default import). The LLM fix instead alternated between rewriting the
wrong side each round, producing a different but equally-broken Vite
build error every time, and burned all 3 stalled-fix-attempt retries
without ever reaching a working state.

Run directly: python tests/reliability/test_import_style_mismatch_fixer.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.core.context import Diagnostic, DiagnosticGroup, ErrorCategory, ErrorSeverity, FixStrategy, GenerationContext
from app.repair.orchestrator import (
    _apply_fix_group,
    _fix_import_style_mismatch_group,
    _is_import_style_mismatch_group,
)
from app.retry.manager import StrategyConfig


def _mismatch_diagnostic(module_path: str, rel_file: str) -> Diagnostic:
    return Diagnostic(
        error_id=f"exp-import-style-{rel_file}",
        category=ErrorCategory.SYNTAX,
        severity=ErrorSeverity.HIGH,
        source="static",
        message=(
            f"Import style mismatch: {module_path} uses a default export but "
            f"is imported with named-import syntax (curly braces) in {rel_file}"
        ),
        file_path=rel_file,
    )


def _mismatch_group(module_path: str, rel_file: str) -> DiagnosticGroup:
    diag = _mismatch_diagnostic(module_path, rel_file)
    return DiagnosticGroup(
        group_id="exp-import-style-group",
        root_cause=diag.message,
        diagnostics=[diag],
        affected_files=[rel_file],
        suggested_strategy=FixStrategy.PATCH_FILE,
        priority=1,
    )


def _tmp_project_with_mismatch() -> Path:
    root = Path(tempfile.mkdtemp(prefix="import_style_mismatch_"))
    hooks = root / "src" / "hooks"
    components = root / "src" / "components"
    hooks.mkdir(parents=True)
    components.mkdir(parents=True)
    (hooks / "useAuth.jsx").write_text(
        "export default function useAuth() {\n"
        "  return { user: null };\n"
        "}\n",
        encoding="utf-8",
    )
    (components / "PrivateRoute.jsx").write_text(
        "import { useAuth } from '../hooks/useAuth';\n\n"
        "export default function PrivateRoute({ children }) {\n"
        "  const { user } = useAuth();\n"
        "  return user ? children : null;\n"
        "}\n",
        encoding="utf-8",
    )
    return root


def test_is_import_style_mismatch_group_matches_the_exact_validator_message():
    group = _mismatch_group("../hooks/useAuth", "src/components/PrivateRoute.jsx")
    assert _is_import_style_mismatch_group(group) is True


def test_is_import_style_mismatch_group_rejects_unrelated_diagnostics():
    diag = Diagnostic(
        error_id="unrelated",
        category=ErrorCategory.RUNTIME,
        severity=ErrorSeverity.HIGH,
        source="runtime",
        message="POST /habits returned 500",
        file_path="app/routes/habit_routes.py",
    )
    group = DiagnosticGroup(
        group_id="unrelated-group", root_cause=diag.message, diagnostics=[diag],
        affected_files=["app/routes/habit_routes.py"], suggested_strategy=FixStrategy.PATCH_FILE, priority=1,
    )
    assert _is_import_style_mismatch_group(group) is False


def test_fixer_rewrites_the_importer_to_a_default_import_not_the_target():
    root = _tmp_project_with_mismatch()
    try:
        group = _mismatch_group("../hooks/useAuth", "src/components/PrivateRoute.jsx")
        modified, written = _fix_import_style_mismatch_group(root, group)

        assert modified == ["src/components/PrivateRoute.jsx"]
        content = written["src/components/PrivateRoute.jsx"]
        assert "import useAuth from '../hooks/useAuth';" in content
        assert "import { useAuth }" not in content
        # The target (useAuth.jsx) must be untouched -- it was never wrong.
        target_content = (root / "src" / "hooks" / "useAuth.jsx").read_text(encoding="utf-8")
        assert "export default function useAuth" in target_content

        on_disk = (root / "src" / "components" / "PrivateRoute.jsx").read_text(encoding="utf-8")
        assert on_disk == content
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_fixer_skips_ambiguous_multi_name_imports_rather_than_guess():
    root = _tmp_project_with_mismatch()
    try:
        (root / "src" / "components" / "PrivateRoute.jsx").write_text(
            "import { useAuth, somethingElse } from '../hooks/useAuth';\n",
            encoding="utf-8",
        )
        group = _mismatch_group("../hooks/useAuth", "src/components/PrivateRoute.jsx")
        modified, written = _fix_import_style_mismatch_group(root, group)

        assert modified == []
        assert written == {}
        # File must be left exactly as-is -- no partial/guessed rewrite.
        on_disk = (root / "src" / "components" / "PrivateRoute.jsx").read_text(encoding="utf-8")
        assert "import { useAuth, somethingElse }" in on_disk
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_apply_fix_group_uses_the_deterministic_fixer_before_the_llm():
    root = _tmp_project_with_mismatch()
    try:
        ctx = GenerationContext("exp-import-style", "test", root, "exp_import_style_app")
        cfg = StrategyConfig(1, FixStrategy.PATCH_FILE, "test", "test")
        group = _mismatch_group("../hooks/useAuth", "src/components/PrivateRoute.jsx")

        modified, written = _apply_fix_group(group, ctx, cfg)

        assert modified == ["src/components/PrivateRoute.jsx"]
        assert "import useAuth from '../hooks/useAuth';" in written["src/components/PrivateRoute.jsx"]
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
