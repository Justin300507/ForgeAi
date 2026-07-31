"""
Exp146 (habit_tracker, 2026-07-15 and again 2026-07-31): the Vite build
error ("X is not exported by Y") and the static import-style-mismatch
diagnostic are the same underlying fact, computed once at the START of a
repair attempt -- an LLM group fixing the Vite error can rewrite Y's
export shape (or scaffold a NEW file Y imports from with its own
default/named shape), and the mechanical import-style-mismatch group
correctly detects its own diagnostic is now stale and skips rather than
guess. But nothing ever re-diagnoses the NEW mismatch within the same
attempt, so the frontend build stays broken and the loop repeats
identically -- confirmed live TWICE, both times ending in "3 consecutive
fix attempts with no score improvement", Forge Score stuck at ~42/F.

_reconcile_frontend_import_export_styles is a direct $0 blanket sweep
(not diagnostic-driven) run after every attempt's group-application loop.

Run directly: python tests/reliability/test_exp146_frontend_import_export_reconciliation.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.repair.orchestrator import _reconcile_frontend_import_export_styles as _patch


def _project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="exp146_test_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def test_fixes_cascading_two_hop_mismatch_in_one_pass():
    """The exact live shape: PrivateRoute -> useAuth -> AuthContext, both
    hops mismatched simultaneously (the "oscillation" case that stumped
    4 repair attempts in a row)."""
    root = _project({
        "src/hooks/useAuth.jsx": (
            "import { AuthContext } from '../context/AuthContext';\n"
            "export default function useAuth() { return AuthContext; }\n"
        ),
        "src/context/AuthContext.jsx": (
            "const AuthContext = {};\nexport default AuthContext;\n"
        ),
        "src/components/PrivateRoute.jsx": (
            "import { useAuth } from '../hooks/useAuth';\n"
            "export default function PrivateRoute({ children }) {\n"
            "  const auth = useAuth();\n  return auth ? children : null;\n}\n"
        ),
    })
    try:
        n = _patch(root)
        assert n == 2
        pr = (root / "src/components/PrivateRoute.jsx").read_text(encoding="utf-8")
        ua = (root / "src/hooks/useAuth.jsx").read_text(encoding="utf-8")
        assert "import useAuth from '../hooks/useAuth'" in pr
        assert "import AuthContext from '../context/AuthContext'" in ua
        assert "import { useAuth }" not in pr
        assert "import { AuthContext }" not in ua
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_idempotent_second_pass_is_noop():
    root = _project({
        "src/hooks/useAuth.jsx": "export default function useAuth() {}\n",
        "src/components/PrivateRoute.jsx": (
            "import { useAuth } from '../hooks/useAuth';\n"
        ),
    })
    try:
        _patch(root)
        first = (root / "src/components/PrivateRoute.jsx").read_text(encoding="utf-8")
        n2 = _patch(root)
        assert n2 == 0
        assert (root / "src/components/PrivateRoute.jsx").read_text(encoding="utf-8") == first
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_genuine_named_export_never_touched():
    """A real named export imported with named-import syntax is correct
    and must never be rewritten."""
    root = _project({
        "src/utils/helpers.js": "export function useAuth() {}\n",
        "src/components/PrivateRoute.jsx": (
            "import { useAuth } from '../utils/helpers';\n"
        ),
    })
    try:
        before = (root / "src/components/PrivateRoute.jsx").read_text(encoding="utf-8")
        assert _patch(root) == 0
        assert (root / "src/components/PrivateRoute.jsx").read_text(encoding="utf-8") == before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_multiple_named_imports_from_same_module_skipped():
    """Ambiguous which binding maps to the single default export --
    must never guess, same rule as the diagnostic-driven fixer."""
    root = _project({
        "src/hooks/useAuth.jsx": "export default function useAuth() {}\n",
        "src/components/PrivateRoute.jsx": (
            "import { useAuth, somethingElse } from '../hooks/useAuth';\n"
        ),
    })
    try:
        before = (root / "src/components/PrivateRoute.jsx").read_text(encoding="utf-8")
        assert _patch(root) == 0
        assert (root / "src/components/PrivateRoute.jsx").read_text(encoding="utf-8") == before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_bare_package_import_never_touched():
    """Only relative (./  or ../) module specifiers are in scope -- a
    named import from an npm package must never be considered."""
    root = _project({
        "src/components/PrivateRoute.jsx": (
            "import { useNavigate } from 'react-router-dom';\n"
        ),
    })
    try:
        before = (root / "src/components/PrivateRoute.jsx").read_text(encoding="utf-8")
        assert _patch(root) == 0
        assert (root / "src/components/PrivateRoute.jsx").read_text(encoding="utf-8") == before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_unresolvable_module_path_never_touched():
    root = _project({
        "src/components/PrivateRoute.jsx": (
            "import { useAuth } from '../hooks/doesNotExist';\n"
        ),
    })
    try:
        before = (root / "src/components/PrivateRoute.jsx").read_text(encoding="utf-8")
        assert _patch(root) == 0
        assert (root / "src/components/PrivateRoute.jsx").read_text(encoding="utf-8") == before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_no_src_dir_is_noop():
    root = Path(tempfile.mkdtemp(prefix="exp146_test_"))
    try:
        assert _patch(root) == 0
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
