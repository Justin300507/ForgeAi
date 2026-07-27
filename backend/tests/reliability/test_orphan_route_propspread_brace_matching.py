"""
_patch_wire_orphan_frontend_routes's anchor-template extraction used a
naive non-greedy regex ("element={(.*?)}" followed by optional whitespace
and "/>") to capture the JSX inside
an existing authenticated route (e.g. /dashboard) to clone for a newly
discovered orphan page. Non-greedy `.*?` stops at the FIRST "} />" it
finds -- for the extremely common prop-spread shape
`<DashboardPage {...pageProps} />`, that's the closing brace of
`{...pageProps}` itself, not the real end of the outer element={...}
attribute. The captured template was silently truncated mid-attribute
(no closing `/>` left in it at all), so the self-closing-tag-clone regex
downstream could never match -- every orphan page failed to wire in with
"couldn't find the anchor's page tag inside its own template to clone",
permanently stuck failing validation on "Missing frontend import target:
./pages/<OrphanPage>". Reproduced live on a real habit-tracker deploy,
2026-07-27 (MePage never got wired in, generation stuck at Forge Score 64/D).

Fixed by replacing the regex with app.utils.brace_matching.find_matching_brace
(real depth-tracking), which already exists and is tested elsewhere in this
codebase for the identical class of bug (Exp053 consolidation).

Run directly: python tests/reliability/test_orphan_route_propspread_brace_matching.py
"""
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import _patch_wire_orphan_frontend_routes

_APP_JSX_WITH_PROPSPREAD = (
    "import React from 'react';\n"
    "import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';\n"
    "import PrivateRoute from './components/PrivateRoute';\n"
    "import LoginPage from './pages/LoginPage';\n"
    "import DashboardPage from './pages/DashboardPage';\n\n"
    "const App = () => {\n"
    "  const pageProps = {};\n"
    "  return (\n"
    "  <Router>\n"
    "    <Routes>\n"
    "      <Route path=\"/login\" element={<LoginPage />} />\n"
    "      <Route path=\"/dashboard\" element={<PrivateRoute><DashboardPage {...pageProps} /></PrivateRoute>} />\n"
    "      <Route path=\"*\" element={<Navigate to=\"/dashboard\" />} />\n"
    "    </Routes>\n"
    "  </Router>\n"
    "  );\n"
    "};\n"
    "export default App;\n"
)


def _project(app_jsx: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix="propspread_test_"))
    (root / "src" / "pages").mkdir(parents=True)
    (root / "src" / "App.jsx").write_text(app_jsx, encoding="utf-8")
    for page in ("LoginPage", "DashboardPage"):
        (root / "src" / "pages" / f"{page}.jsx").write_text(
            f"const {page} = () => <div/>;\nexport default {page};\n", encoding="utf-8")
    (root / "src" / "pages" / "MePage.jsx").write_text(
        "const MePage = () => <div/>;\nexport default MePage;\n", encoding="utf-8")
    return root


def test_orphan_page_wired_in_despite_propspread_anchor():
    root = _project(_APP_JSX_WITH_PROPSPREAD)
    try:
        _patch_wire_orphan_frontend_routes(root)
        out = (root / "src" / "App.jsx").read_text(encoding="utf-8")
        assert "import MePage from './pages/MePage'" in out
        assert re.search(r'<Route\s+path="[^"]*"\s+element=\{<PrivateRoute><MePage\s*/></PrivateRoute>\}', out), out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_cloned_route_preserves_wrapper_not_just_bare_tag():
    # The clone must keep the PrivateRoute wrapper from the anchor, not just
    # substitute a bare <MePage /> with no auth guard.
    root = _project(_APP_JSX_WITH_PROPSPREAD)
    try:
        _patch_wire_orphan_frontend_routes(root)
        out = (root / "src" / "App.jsx").read_text(encoding="utf-8")
        me_route = next(l for l in out.splitlines() if "MePage" in l and "<Route" in l)
        assert "PrivateRoute" in me_route
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_still_works_without_propspread_bare_anchor():
    # Non-regression: the simple bare-tag anchor shape must keep working.
    app_jsx = (
        "import React from 'react';\n"
        "import { Routes, Route } from 'react-router-dom';\n"
        "import LoginPage from './pages/LoginPage';\n"
        "import DashboardPage from './pages/DashboardPage';\n\n"
        "const App = () => (\n"
        "  <Routes>\n"
        "    <Route path=\"/login\" element={<LoginPage />} />\n"
        "    <Route path=\"/dashboard\" element={<DashboardPage />} />\n"
        "  </Routes>\n"
        ");\n"
        "export default App;\n"
    )
    root = _project(app_jsx)
    try:
        _patch_wire_orphan_frontend_routes(root)
        out = (root / "src" / "App.jsx").read_text(encoding="utf-8")
        assert "import MePage from './pages/MePage'" in out
        assert re.search(r'<Route\s+path="[^"]*"\s+element=\{<MePage\s*/>\}', out), out
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
