"""
Nothing previously checked that every backend resource (app/routes/
*_routes.py) has a corresponding, actually-routed frontend page.

Reproduces the live incident (habit_tracker, 2026-07-30, scored 94.9/A
and would have deployed): full habit CRUD existed on the backend, zero
way to create/view/edit/delete a habit from the UI. No validation error
fired because nothing else happened to reference the missing page --
validate_frontend_imports/validate_frontend_nav_targets/validate_
frontend_placeholder_routes all require SOMETHING (an import, a nav
link, a placeholder route) pointing at the gap first, and here nothing
did. "Login works, then there's nothing to do" was invisible to every
automated check.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.validator_service import validate_frontend_resource_pages


def _project_with_routes(route_files: list[str]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="resource_page_coverage_"))
    routes_dir = root / "app" / "routes"
    routes_dir.mkdir(parents=True)
    for fname in route_files:
        (routes_dir / fname).write_text("# route file\n", encoding="utf-8")
    (root / "src").mkdir(parents=True)
    return root


def _write_app_jsx(root: Path, body: str) -> None:
    (root / "src" / "App.jsx").write_text(body, encoding="utf-8")


def test_detects_a_resource_with_no_routed_page_at_all():
    root = _project_with_routes(
        ["auth_routes.py", "seed_routes.py", "stats_routes.py", "habit_routes.py"]
    )
    _write_app_jsx(root, """
        import Dashboard from './pages/Dashboard';
        import Login from './pages/Login';
        export default function App() {
          return (
            <Routes>
              <Route path="/dashboard" element={<PrivateRoute><Dashboard /></PrivateRoute>} />
              <Route path="/login" element={<Login />} />
            </Routes>
          );
        }
    """)
    errors = []
    validate_frontend_resource_pages(str(root), errors)
    assert errors == ["Missing frontend import target: ./pages/HabitPage"]


def test_infrastructure_route_files_are_never_flagged():
    root = _project_with_routes(
        ["auth_routes.py", "seed_routes.py", "stats_routes.py", "stat_routes.py"]
    )
    _write_app_jsx(root, """
        import Dashboard from './pages/Dashboard';
        export default function App() {
          return (
            <Routes>
              <Route path="/dashboard" element={<PrivateRoute><Dashboard /></PrivateRoute>} />
            </Routes>
          );
        }
    """)
    errors = []
    validate_frontend_resource_pages(str(root), errors)
    assert errors == []


def test_no_false_positive_when_every_resource_has_a_real_routed_page():
    root = _project_with_routes(["auth_routes.py", "habit_routes.py", "user_routes.py"])
    _write_app_jsx(root, """
        import Dashboard from './pages/Dashboard';
        import HabitPage from './pages/HabitPage';
        import UserPage from './pages/UserPage';
        export default function App() {
          return (
            <Routes>
              <Route path="/dashboard" element={<PrivateRoute><Dashboard /></PrivateRoute>} />
              <Route path="/habits" element={<PrivateRoute><HabitPage /></PrivateRoute>} />
              <Route path="/users" element={<PrivateRoute><UserPage /></PrivateRoute>} />
            </Routes>
          );
        }
    """)
    errors = []
    validate_frontend_resource_pages(str(root), errors)
    assert errors == []


def test_inline_placeholder_route_still_counts_as_missing():
    """A <Route> that exists but renders inline JSX (no real component tag)
    must not count as "has a page" -- validate_frontend_placeholder_routes
    handles the placeholder-text case; this validator's job is narrower:
    no REAL component rendering this resource at all."""
    root = _project_with_routes(["habit_routes.py"])
    _write_app_jsx(root, """
        export default function App() {
          return (
            <Routes>
              <Route path="/habits" element={<PrivateRoute><div>habits go here</div></PrivateRoute>} />
            </Routes>
          );
        }
    """)
    errors = []
    validate_frontend_resource_pages(str(root), errors)
    assert errors == ["Missing frontend import target: ./pages/HabitPage"]


def test_noop_when_app_jsx_has_no_routes_at_all():
    """validate_frontend_placeholder_routes already reports a completely
    routeless App.jsx as its own, more severe error -- this validator must
    not also fire and produce a confusing double-report for the same root
    cause."""
    root = _project_with_routes(["habit_routes.py"])
    _write_app_jsx(root, "export default function App() { return <div>App</div>; }")
    errors = []
    validate_frontend_resource_pages(str(root), errors)
    assert errors == []


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
