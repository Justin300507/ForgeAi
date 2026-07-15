"""
Exp112: the orphan-frontend-route wirer's import regex required the closing
quote immediately after the module name, so every `./pages/X.jsx`-suffixed
import was invisible to it — it re-imported EVERY page extensionless and
esbuild died on duplicate symbols.

Confirmed live (simple_crm, exp109-milestone-r2): the sole reason an
86.5/B app was blocked from deploying ("critical stage 'frontend_build'
failed"). Also adds a dedupe backstop after the wirer in
run_deterministic_patches.

Run directly: python tests/reliability/test_exp112_wirer_extension_blindness.py
"""
import inspect
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services import deterministic_patcher
from app.services.deterministic_patcher import (
    _patch_dedupe_frontend_imports,
    _patch_wire_orphan_frontend_routes,
)

_APP_JSX = (
    "import React from 'react';\n"
    "import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';\n"
    "import LoginPage from './pages/LoginPage.jsx';\n"
    "import DashboardPage from './pages/DashboardPage.jsx';\n\n"
    "const App = () => (\n"
    "  <Router>\n"
    "    <Routes>\n"
    "      <Route path='/login' element={<LoginPage />} />\n"
    "      <Route path='/' element={<DashboardPage />} />\n"
    "    </Routes>\n"
    "  </Router>\n"
    ");\n"
    "export default App;\n"
)


def _project() -> Path:
    root = Path(tempfile.mkdtemp(prefix="exp112_test_"))
    (root / "src" / "pages").mkdir(parents=True)
    (root / "src" / "App.jsx").write_text(_APP_JSX, encoding="utf-8")
    for page in ("LoginPage", "DashboardPage"):
        (root / "src" / "pages" / f"{page}.jsx").write_text(
            f"const {page} = () => <div/>;\nexport default {page};\n", encoding="utf-8")
    return root


def test_wirer_sees_jsx_suffixed_imports_and_injects_nothing():
    root = _project()
    try:
        _patch_wire_orphan_frontend_routes(root)
        out = (root / "src" / "App.jsx").read_text(encoding="utf-8")
        assert out.count("import LoginPage") == 1, out
        assert out.count("import DashboardPage") == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_wirer_still_imports_genuinely_orphan_page():
    root = _project()
    try:
        (root / "src" / "pages" / "ReportsPage.jsx").write_text(
            "const ReportsPage = () => <div/>;\nexport default ReportsPage;\n",
            encoding="utf-8")
        _patch_wire_orphan_frontend_routes(root)
        out = (root / "src" / "App.jsx").read_text(encoding="utf-8")
        assert out.count("import ReportsPage") == 1
        assert out.count("import LoginPage") == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_dedupe_backstop_registered_after_wirer():
    source = inspect.getsource(deterministic_patcher.run_deterministic_patches)
    wire_pos = source.find('"_patch_wire_orphan_frontend_routes"')
    backstop_pos = source.find('"_patch_dedupe_frontend_imports_post_wire"')
    assert wire_pos != -1 and backstop_pos != -1
    assert backstop_pos > wire_pos


def test_dedupe_removes_mixed_extension_duplicates():
    root = _project()
    try:
        app = root / "src" / "App.jsx"
        content = app.read_text(encoding="utf-8")
        content = content.replace(
            "const App",
            "import LoginPage from './pages/LoginPage'\n"
            "import DashboardPage from './pages/DashboardPage'\nconst App")
        app.write_text(content, encoding="utf-8")
        _patch_dedupe_frontend_imports(root)
        out = app.read_text(encoding="utf-8")
        assert out.count("import LoginPage") == 1
        assert out.count("import DashboardPage") == 1
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
