"""
Exp052 (Deterministic Repair Test Coverage Initiative): regression tests
for the 11 frontend-rewrite repair functions in deterministic_patcher.py
that were previously untested (see docs/REPAIR_INVENTORY.md /
docs/REPAIR_DEBT.md from Exp051's audit).

A live search of generated_projects/*/src for real instances of each bug
pattern came back empty -- expected, since these patchers already run on
every project before it settles into generated_projects/'s final state
(same reason Exp049 had to pull fixtures from llm_cache/ raw responses
instead of post-pipeline output). Fixtures here are instead built directly
from the real production incidents each function's own docstring cites
(habit_forge, forge_expense_tracker, a weekly-report/badges endpoint) --
grounded in actual observed bugs, not arbitrary minimal strings, even
though they're not byte-for-byte pulled files.

Run directly: python tests/reliability/test_frontend_rewrite_repairs.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import (
    _patch_missing_icon_imports,
    _patch_disallowed_icon_packages,
    _patch_frontend_auth_field_names,
    _patch_frontend_signup_password_key,
    _patch_stale_status_on_error,
    _patch_hidden_loading_status,
    _patch_unsafe_optional_chain_before_array_method,
    _patch_response_data_used_as_bare_array,
    _patch_response_data_assumed_wrapped,
    _patch_pagination_component,
    _patch_login_redirect_target,
    _PAGINATION_TEMPLATE,
)


def _project(tmp_path, files):
    """files: {relative_path: content}. Returns the project root Path."""
    root = tmp_path / "proj"
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


# ── _patch_missing_icon_imports ─────────────────────────────────────────────

def test_missing_icon_imports_adds_undeclared_lucide_icon(tmp_path):
    src = (
        "import React from 'react';\n"
        "export default function TasksPage() {\n"
        "  return <div><ChevronRight size={16} /></div>;\n"
        "}\n"
    )
    root = _project(tmp_path, {"src/pages/TasksPage.jsx": src})
    n = _patch_missing_icon_imports(root)
    assert n == 1
    out = (root / "src/pages/TasksPage.jsx").read_text(encoding="utf-8")
    assert "import { ChevronRight } from 'lucide-react';" in out


def test_missing_icon_imports_router_component_goes_to_router_dom(tmp_path):
    # Link is both a real lucide icon name AND a router component -- must
    # resolve to react-router-dom, not lucide-react (a prior version of
    # this patcher got this wrong and silently broke every <Link>).
    src = (
        "import React from 'react';\n"
        "export default function Nav() { return <Link to='/x'>Go</Link>; }\n"
    )
    root = _project(tmp_path, {"src/components/Nav.jsx": src})
    _patch_missing_icon_imports(root)
    out = (root / "src/components/Nav.jsx").read_text(encoding="utf-8")
    assert "from 'react-router-dom'" in out
    assert "Link" not in out.split("from 'react-router-dom'")[0].split("\n")[-1] or \
        "{ Link }" in out  # Link is imported, just from the right package
    assert "import { Link } from 'lucide-react'" not in out


def test_missing_icon_imports_multiple_occurrences_in_one_file(tmp_path):
    src = (
        "import React from 'react';\n"
        "export default function X() {\n"
        "  return <div><Trash2 /><Wrench /><ChevronRight /></div>;\n"
        "}\n"
    )
    root = _project(tmp_path, {"src/pages/X.jsx": src})
    n = _patch_missing_icon_imports(root)
    assert n == 1
    out = (root / "src/pages/X.jsx").read_text(encoding="utf-8")
    for icon in ("Trash2", "Wrench", "ChevronRight"):
        assert icon in out.split("lucide-react")[0]  # all three landed in the import


def test_missing_icon_imports_noop_when_already_imported(tmp_path):
    src = (
        "import React from 'react';\n"
        "import { ChevronRight } from 'lucide-react';\n"
        "export default function X() { return <ChevronRight />; }\n"
    )
    root = _project(tmp_path, {"src/pages/X.jsx": src})
    n = _patch_missing_icon_imports(root)
    assert n == 0
    assert (root / "src/pages/X.jsx").read_text(encoding="utf-8") == src


def test_missing_icon_imports_idempotent(tmp_path):
    src = "export default function X() { return <ChevronRight />; }\n"
    root = _project(tmp_path, {"src/pages/X.jsx": src})
    _patch_missing_icon_imports(root)
    once = (root / "src/pages/X.jsx").read_text(encoding="utf-8")
    n2 = _patch_missing_icon_imports(root)
    twice = (root / "src/pages/X.jsx").read_text(encoding="utf-8")
    assert n2 == 0
    assert once == twice


# ── _patch_disallowed_icon_packages ─────────────────────────────────────────

def test_disallowed_icon_packages_rewrites_heroicons_to_lucide(tmp_path):
    src = (
        "import { PlusIcon, TrashIcon } from '@heroicons/react/24/outline';\n"
        "export default function X() { return <div><PlusIcon /><TrashIcon /></div>; }\n"
    )
    root = _project(tmp_path, {"src/pages/X.jsx": src})
    n = _patch_disallowed_icon_packages(root)
    assert n == 1
    out = (root / "src/pages/X.jsx").read_text(encoding="utf-8")
    assert "@heroicons/react" not in out
    assert "from 'lucide-react'" in out
    assert "<Plus />" in out or "<Plus/>" in out
    assert "<Trash" in out  # TrashIcon -> Trash or Trash2 depending on _LUCIDE_ICONS membership


def test_disallowed_icon_packages_unmappable_icon_falls_back_to_circle(tmp_path):
    src = (
        "import { SomeNonexistentIcon } from '@heroicons/react/24/solid';\n"
        "export default function X() { return <SomeNonexistentIcon />; }\n"
    )
    root = _project(tmp_path, {"src/pages/X.jsx": src})
    n = _patch_disallowed_icon_packages(root)
    assert n == 1
    out = (root / "src/pages/X.jsx").read_text(encoding="utf-8")
    assert "<Circle />" in out or "<Circle/>" in out


def test_disallowed_icon_packages_noop_on_clean_file(tmp_path):
    src = "import { Plus } from 'lucide-react';\nexport default function X() { return <Plus />; }\n"
    root = _project(tmp_path, {"src/pages/X.jsx": src})
    n = _patch_disallowed_icon_packages(root)
    assert n == 0
    assert (root / "src/pages/X.jsx").read_text(encoding="utf-8") == src


def test_disallowed_icon_packages_idempotent(tmp_path):
    src = "import { PlusIcon } from '@heroicons/react/24/outline';\nconst X = () => <PlusIcon />;\n"
    root = _project(tmp_path, {"src/pages/X.jsx": src})
    _patch_disallowed_icon_packages(root)
    once = (root / "src/pages/X.jsx").read_text(encoding="utf-8")
    n2 = _patch_disallowed_icon_packages(root)
    assert n2 == 0
    assert (root / "src/pages/X.jsx").read_text(encoding="utf-8") == once


# ── _patch_frontend_auth_field_names ────────────────────────────────────────

def test_frontend_auth_field_names_fixes_username_and_id(tmp_path):
    # Real shape from the patcher's own docstring: backend returns
    # {access_token, user_id, email, display_name} -- no `username`/`id`.
    src = (
        "async function login(email, password) {\n"
        "  const res = await api.post('/auth/login', { email, password });\n"
        "  if (res.data.username) localStorage.setItem('display_name', res.data.username);\n"
        "  if (res.data.id) localStorage.setItem('user_id', res.data.id);\n"
        "}\n"
    )
    root = _project(tmp_path, {"src/pages/Login.jsx": src})
    n = _patch_frontend_auth_field_names(root)
    assert n == 1
    out = (root / "src/pages/Login.jsx").read_text(encoding="utf-8")
    assert "res.data.display_name" in out
    assert "res.data.user_id" in out
    assert "res.data.username" not in out


def test_frontend_auth_field_names_scoped_leaves_unrelated_id_alone(tmp_path):
    # Must not touch a .id/.username that isn't part of the exact
    # guard+setItem pair (e.g. an unrelated task.id elsewhere in the file).
    src = (
        "async function login(email, password) {\n"
        "  const res = await api.post('/auth/login', { email, password });\n"
        "  if (res.data.id) localStorage.setItem('user_id', res.data.id);\n"
        "}\n"
        "function renderTask(task) { return task.id; }\n"
    )
    root = _project(tmp_path, {"src/pages/Login.jsx": src})
    _patch_frontend_auth_field_names(root)
    out = (root / "src/pages/Login.jsx").read_text(encoding="utf-8")
    assert "task.id" in out  # untouched


def test_frontend_auth_field_names_requires_auth_endpoint_present(tmp_path):
    # No /auth/login or /auth/register in the file -- must not fire even
    # if the exact .username/.id shape happens to appear.
    src = (
        "if (res.data.username) localStorage.setItem('display_name', res.data.username);\n"
    )
    root = _project(tmp_path, {"src/pages/Other.jsx": src})
    n = _patch_frontend_auth_field_names(root)
    assert n == 0
    assert (root / "src/pages/Other.jsx").read_text(encoding="utf-8") == src


def test_frontend_auth_field_names_idempotent(tmp_path):
    src = (
        "await api.post('/auth/login', {});\n"
        "if (res.data.username) localStorage.setItem('display_name', res.data.username);\n"
    )
    root = _project(tmp_path, {"src/pages/Login.jsx": src})
    _patch_frontend_auth_field_names(root)
    once = (root / "src/pages/Login.jsx").read_text(encoding="utf-8")
    n2 = _patch_frontend_auth_field_names(root)
    assert n2 == 0
    assert (root / "src/pages/Login.jsx").read_text(encoding="utf-8") == once


# ── _patch_frontend_signup_password_key ─────────────────────────────────────

def test_signup_password_key_fixes_hashed_password(tmp_path):
    # Real shape from forge_expense_tracker per the docstring.
    src = (
        "async function register(email, password, display_name) {\n"
        "  await api.post('/auth/register', { email, hashed_password: password, display_name });\n"
        "}\n"
    )
    root = _project(tmp_path, {"src/pages/Register.jsx": src})
    n = _patch_frontend_signup_password_key(root)
    assert n == 1
    out = (root / "src/pages/Register.jsx").read_text(encoding="utf-8")
    assert "hashed_password" not in out
    assert "password: password" in out or "password:password" in out.replace(" ", "")


def test_signup_password_key_requires_register_or_signup_endpoint(tmp_path):
    src = "await api.post('/other', { hashed_password: password });\n"
    root = _project(tmp_path, {"src/pages/X.jsx": src})
    n = _patch_frontend_signup_password_key(root)
    assert n == 0
    assert (root / "src/pages/X.jsx").read_text(encoding="utf-8") == src


def test_signup_password_key_multiple_occurrences(tmp_path):
    src = (
        "await api.post('/auth/register', { hashed_password: a });\n"
        "await api.post('/auth/register', { hashed_password: b });\n"
    )
    root = _project(tmp_path, {"src/pages/Register.jsx": src})
    n = _patch_frontend_signup_password_key(root)
    assert n == 1  # one file patched, both occurrences fixed within it
    out = (root / "src/pages/Register.jsx").read_text(encoding="utf-8")
    assert out.count("hashed_password") == 0
    assert out.count("password:") == 2


def test_signup_password_key_idempotent(tmp_path):
    src = "await api.post('/auth/register', { hashed_password: password });\n"
    root = _project(tmp_path, {"src/pages/Register.jsx": src})
    _patch_frontend_signup_password_key(root)
    once = (root / "src/pages/Register.jsx").read_text(encoding="utf-8")
    n2 = _patch_frontend_signup_password_key(root)
    assert n2 == 0
    assert (root / "src/pages/Register.jsx").read_text(encoding="utf-8") == once


# ── _patch_stale_status_on_error ────────────────────────────────────────────

def test_stale_status_on_error_inserts_clear_call(tmp_path):
    src = (
        "function useLoad() {\n"
        "  const [status, setStatus] = useState(null);\n"
        "  const msg = parseError(err);\n"
        "  if (msg) { setError(msg); setLoading(false); return; }\n"
        "  if (attempt < 3) { setStatus(`retrying (${attempt}/3)`); }\n"
        "}\n"
    )
    root = _project(tmp_path, {"src/pages/List.jsx": src})
    n = _patch_stale_status_on_error(root)
    assert n == 1
    out = (root / "src/pages/List.jsx").read_text(encoding="utf-8")
    assert "setStatus(null)" in out


def test_stale_status_on_error_requires_setStatus_declared(tmp_path):
    # No setStatus setter anywhere in the file -- must not insert a call
    # to something undefined.
    src = "if (msg) { setError(msg); setLoading(false); return; }\n"
    root = _project(tmp_path, {"src/pages/X.jsx": src})
    n = _patch_stale_status_on_error(root)
    assert n == 0
    assert (root / "src/pages/X.jsx").read_text(encoding="utf-8") == src


def test_stale_status_on_error_idempotent(tmp_path):
    src = (
        "const [status, setStatus] = useState(null);\n"
        "if (msg) { setError(msg); setLoading(false); return; }\n"
    )
    root = _project(tmp_path, {"src/pages/X.jsx": src})
    _patch_stale_status_on_error(root)
    once = (root / "src/pages/X.jsx").read_text(encoding="utf-8")
    n2 = _patch_stale_status_on_error(root)
    assert n2 == 0
    assert (root / "src/pages/X.jsx").read_text(encoding="utf-8") == once


# ── _patch_hidden_loading_status ────────────────────────────────────────────

def test_hidden_loading_status_hoists_message_above_ternary(tmp_path):
    src = (
        "return (\n"
        "  <div>\n"
        "    {loading ? (\n"
        "      <Skeleton />\n"
        "    ) : (\n"
        "      <div>\n"
        "        {status && <p className='text-sm'>{status}</p>}\n"
        "        <Content />\n"
        "      </div>\n"
        "    )}\n"
        "  </div>\n"
        ");\n"
    )
    root = _project(tmp_path, {"src/pages/Dashboard.jsx": src})
    n = _patch_hidden_loading_status(root)
    assert n == 1
    out = (root / "src/pages/Dashboard.jsx").read_text(encoding="utf-8")
    # the status block must now appear BEFORE "{loading ? ("
    assert out.index("status && <p") < out.index("{loading ? (")


def test_hidden_loading_status_requires_both_markers(tmp_path):
    src = "return <div>{status && <p>{status}</p>}</div>;\n"  # no loading ternary
    root = _project(tmp_path, {"src/pages/X.jsx": src})
    n = _patch_hidden_loading_status(root)
    assert n == 0
    assert (root / "src/pages/X.jsx").read_text(encoding="utf-8") == src


def test_hidden_loading_status_idempotent(tmp_path):
    src = (
        "return (\n"
        "  <div>\n"
        "    {loading ? (<Skeleton />) : (<div>{status && <p className='x'>{status}</p>}<C /></div>)}\n"
        "  </div>\n"
        ");\n"
    )
    root = _project(tmp_path, {"src/pages/X.jsx": src})
    _patch_hidden_loading_status(root)
    once = (root / "src/pages/X.jsx").read_text(encoding="utf-8")
    n2 = _patch_hidden_loading_status(root)
    twice = (root / "src/pages/X.jsx").read_text(encoding="utf-8")
    assert n2 == 0, "must not re-hoist an already-hoisted status block"
    assert once == twice


# ── _patch_unsafe_optional_chain_before_array_method ────────────────────────

def test_unsafe_optional_chain_fixes_map_and_length(tmp_path):
    src = (
        "function Dashboard({ stats }) {\n"
        "  return (\n"
        "    <div>\n"
        "      {stats?.weekly_completions.map(w => <Bar key={w.id} />)}\n"
        "      <span>{stats?.weekly_completions.length}</span>\n"
        "    </div>\n"
        "  );\n"
        "}\n"
    )
    root = _project(tmp_path, {"src/pages/Dashboard.jsx": src})
    n = _patch_unsafe_optional_chain_before_array_method(root)
    assert n == 1
    out = (root / "src/pages/Dashboard.jsx").read_text(encoding="utf-8")
    assert "stats?.weekly_completions?.map(" in out
    assert "stats?.weekly_completions?.length" in out


def test_unsafe_optional_chain_noop_when_already_safe(tmp_path):
    src = "return stats?.weekly_completions?.map(w => w);\n"
    root = _project(tmp_path, {"src/pages/X.jsx": src})
    n = _patch_unsafe_optional_chain_before_array_method(root)
    assert n == 0
    assert (root / "src/pages/X.jsx").read_text(encoding="utf-8") == src


def test_unsafe_optional_chain_multiple_methods_in_one_file(tmp_path):
    src = (
        "a?.x.map(f); a?.x.filter(f); a?.x.find(f);\n"
    )
    root = _project(tmp_path, {"src/pages/X.jsx": src})
    n = _patch_unsafe_optional_chain_before_array_method(root)
    assert n == 1
    out = (root / "src/pages/X.jsx").read_text(encoding="utf-8")
    assert out.count("a?.x?.") == 3


def test_unsafe_optional_chain_idempotent(tmp_path):
    src = "return stats?.weekly_completions.map(w => w);\n"
    root = _project(tmp_path, {"src/pages/X.jsx": src})
    _patch_unsafe_optional_chain_before_array_method(root)
    once = (root / "src/pages/X.jsx").read_text(encoding="utf-8")
    n2 = _patch_unsafe_optional_chain_before_array_method(root)
    assert n2 == 0
    assert (root / "src/pages/X.jsx").read_text(encoding="utf-8") == once


# ── _patch_response_data_used_as_bare_array ─────────────────────────────────

def test_response_data_bare_array_wraps_with_isArray_check(tmp_path):
    # Real shape from the docstring: a weekly report endpoint returns
    # {start_date, end_date, entries: [...]}, not a bare array.
    src = (
        "async function load() {\n"
        "  const weeklyRes = await api.get('/reports/weekly');\n"
        "  setData(weeklyRes.data.map(x => x.value));\n"
        "}\n"
    )
    root = _project(tmp_path, {"src/pages/Reports.jsx": src})
    n = _patch_response_data_used_as_bare_array(root)
    assert n == 1
    out = (root / "src/pages/Reports.jsx").read_text(encoding="utf-8")
    assert "Array.isArray(weeklyRes.data)" in out
    assert ".map(x => x.value)" in out


def test_response_data_bare_array_noop_when_already_wrapped(tmp_path):
    already = (
        "setData((Array.isArray(weeklyRes.data) ? weeklyRes.data : "
        "(weeklyRes.data?.entries || weeklyRes.data?.items || weeklyRes.data?.results || "
        "weeklyRes.data?.data || [])).map(x => x.value));\n"
    )
    root = _project(tmp_path, {"src/pages/Reports.jsx": already})
    n = _patch_response_data_used_as_bare_array(root)
    assert n == 0
    assert (root / "src/pages/Reports.jsx").read_text(encoding="utf-8") == already


def test_response_data_bare_array_idempotent(tmp_path):
    src = "setData(weeklyRes.data.map(x => x.value));\n"
    root = _project(tmp_path, {"src/pages/Reports.jsx": src})
    _patch_response_data_used_as_bare_array(root)
    once = (root / "src/pages/Reports.jsx").read_text(encoding="utf-8")
    n2 = _patch_response_data_used_as_bare_array(root)
    twice = (root / "src/pages/Reports.jsx").read_text(encoding="utf-8")
    assert n2 == 0, "second pass on already-wrapped output must not fire again"
    assert once == twice


# ── _patch_response_data_assumed_wrapped (inverse pair) ─────────────────────

def test_response_data_assumed_wrapped_wraps_with_isArray_check(tmp_path):
    # Real shape from the docstring: a badges endpoint returns a bare [].
    src = "setBadges(response.data.items);\n"
    root = _project(tmp_path, {"src/pages/Badges.jsx": src})
    n = _patch_response_data_assumed_wrapped(root)
    assert n == 1
    out = (root / "src/pages/Badges.jsx").read_text(encoding="utf-8")
    assert "Array.isArray(response.data)" in out


def test_response_data_assumed_wrapped_covers_items_entries_results(tmp_path):
    src = "a(x.data.items); b(y.data.entries); c(z.data.results);\n"
    root = _project(tmp_path, {"src/pages/X.jsx": src})
    n = _patch_response_data_assumed_wrapped(root)
    assert n == 1
    out = (root / "src/pages/X.jsx").read_text(encoding="utf-8")
    assert out.count("Array.isArray(") == 3


def test_response_data_assumed_wrapped_idempotent(tmp_path):
    src = "setBadges(response.data.items);\n"
    root = _project(tmp_path, {"src/pages/Badges.jsx": src})
    _patch_response_data_assumed_wrapped(root)
    once = (root / "src/pages/Badges.jsx").read_text(encoding="utf-8")
    n2 = _patch_response_data_assumed_wrapped(root)
    assert n2 == 0
    assert (root / "src/pages/Badges.jsx").read_text(encoding="utf-8") == once


def test_response_data_bare_array_and_wrapped_are_true_inverses_no_interaction(tmp_path):
    # Interaction-with-neighboring-syntax check: running BOTH patchers
    # (as the real run_frontend_patches sequence does) on a file containing
    # one of each shape must fix both independently without cross-touching.
    src = (
        "setA(resA.data.map(x => x));\n"
        "setB(resB.data.items);\n"
    )
    root = _project(tmp_path, {"src/pages/X.jsx": src})
    _patch_response_data_used_as_bare_array(root)
    _patch_response_data_assumed_wrapped(root)
    out = (root / "src/pages/X.jsx").read_text(encoding="utf-8")
    assert "Array.isArray(resA.data)" in out
    assert "Array.isArray(resB.data)" in out


# ── _patch_pagination_component ─────────────────────────────────────────────

def test_pagination_component_replaces_nonstandard_implementation(tmp_path):
    broken = (
        "const Pagination = ({ currentPage, totalPages, onPageChange }) => (\n"
        "  <div>{currentPage} of {totalPages}</div>\n"  # a real but non-canonical impl
        ");\n"
        "export default Pagination;\n"
    )
    root = _project(tmp_path, {"src/components/Pagination.jsx": broken})
    n = _patch_pagination_component(root)
    assert n == 1
    out = (root / "src/components/Pagination.jsx").read_text(encoding="utf-8")
    assert out == _PAGINATION_TEMPLATE


def test_pagination_component_path_variants_all_caught(tmp_path):
    # Exp049 finding: UI/Pagination.jsx and Common/Pagination.jsx were
    # silently missed by an earlier fixed-path version of this patcher.
    root = _project(tmp_path, {
        "src/components/UI/Pagination.jsx": "const Pagination=({currentPage})=>currentPage;\n",
        "src/components/Common/Pagination.jsx": "const Pagination=({currentPage})=>currentPage;\n",
    })
    n = _patch_pagination_component(root)
    assert n == 2
    assert (root / "src/components/UI/Pagination.jsx").read_text(encoding="utf-8") == _PAGINATION_TEMPLATE
    assert (root / "src/components/Common/Pagination.jsx").read_text(encoding="utf-8") == _PAGINATION_TEMPLATE


def test_pagination_component_ignores_non_pagination_file(tmp_path):
    # A file named Pagination.jsx that isn't actually the standard
    # component (no "currentPage") must be left alone.
    src = "export default function Pagination() { return <div>unrelated</div>; }\n"
    root = _project(tmp_path, {"src/components/Pagination.jsx": src})
    n = _patch_pagination_component(root)
    assert n == 0
    assert (root / "src/components/Pagination.jsx").read_text(encoding="utf-8") == src


def test_pagination_component_idempotent(tmp_path):
    root = _project(tmp_path, {"src/components/Pagination.jsx": "x={currentPage}\n"})
    _patch_pagination_component(root)
    n2 = _patch_pagination_component(root)
    assert n2 == 0, "already-canonical template must not be rewritten again"
    assert (root / "src/components/Pagination.jsx").read_text(encoding="utf-8") == _PAGINATION_TEMPLATE


# ── _patch_login_redirect_target ────────────────────────────────────────────

_APP_JSX_NO_DASHBOARD = """\
<Routes>
  <Route path="/" element={<Landing />} />
  <Route path="/login" element={<Login />} />
  <Route path="/register" element={<Register />} />
  <Route path="/habits" element={<PrivateRoute><HabitsPage /></PrivateRoute>} />
</Routes>
"""

_APP_JSX_HAS_DASHBOARD = """\
<Routes>
  <Route path="/login" element={<Login />} />
  <Route path="/dashboard" element={<PrivateRoute><Dashboard /></PrivateRoute>} />
</Routes>
"""


def test_login_redirect_target_rewrites_all_three_shapes(tmp_path):
    login_src = (
        "function onSuccess() { navigate('/dashboard'); }\n"
        "// nav: <NavLink to=\"/dashboard\">Home</NavLink>\n"
    )
    sidebar_src = "const NAV_ITEMS = [{ path: '/dashboard', label: 'Home' }];\n"
    root = _project(tmp_path, {
        "src/App.jsx": _APP_JSX_NO_DASHBOARD,
        "src/pages/Login.jsx": login_src,
        "src/components/Sidebar.jsx": sidebar_src,
    })
    n = _patch_login_redirect_target(root)
    assert n == 2  # Login.jsx and Sidebar.jsx both patched
    login_out = (root / "src/pages/Login.jsx").read_text(encoding="utf-8")
    sidebar_out = (root / "src/components/Sidebar.jsx").read_text(encoding="utf-8")
    assert "navigate('/habits')" in login_out
    assert 'to="/habits"' in login_out
    assert "path: '/habits'" in sidebar_out
    assert "/dashboard" not in login_out
    assert "/dashboard" not in sidebar_out


def test_login_redirect_target_noop_when_dashboard_route_exists(tmp_path):
    src = "navigate('/dashboard');\n"
    root = _project(tmp_path, {
        "src/App.jsx": _APP_JSX_HAS_DASHBOARD,
        "src/pages/Login.jsx": src,
    })
    n = _patch_login_redirect_target(root)
    assert n == 0
    assert (root / "src/pages/Login.jsx").read_text(encoding="utf-8") == src


def test_login_redirect_target_noop_when_no_sensible_fallback(tmp_path):
    # Only auth-like private routes exist (none real to redirect to).
    app = '<Routes><Route path="/login" element={<Login />} /></Routes>\n'
    src = "navigate('/dashboard');\n"
    root = _project(tmp_path, {"src/App.jsx": app, "src/pages/Login.jsx": src})
    n = _patch_login_redirect_target(root)
    assert n == 0
    assert (root / "src/pages/Login.jsx").read_text(encoding="utf-8") == src


def test_login_redirect_target_idempotent(tmp_path):
    src = "navigate('/dashboard');\n"
    root = _project(tmp_path, {"src/App.jsx": _APP_JSX_NO_DASHBOARD, "src/pages/Login.jsx": src})
    _patch_login_redirect_target(root)
    once = (root / "src/pages/Login.jsx").read_text(encoding="utf-8")
    n2 = _patch_login_redirect_target(root)
    assert n2 == 0
    assert (root / "src/pages/Login.jsx").read_text(encoding="utf-8") == once


# ── malformed-input handling (shared across several functions) ─────────────

def test_all_functions_handle_missing_src_dir_gracefully(tmp_path):
    root = tmp_path / "empty_proj"
    root.mkdir()
    for fn in (
        _patch_missing_icon_imports, _patch_disallowed_icon_packages,
        _patch_frontend_auth_field_names, _patch_frontend_signup_password_key,
        _patch_stale_status_on_error, _patch_hidden_loading_status,
        _patch_unsafe_optional_chain_before_array_method,
        _patch_response_data_used_as_bare_array,
        _patch_response_data_assumed_wrapped, _patch_pagination_component,
        _patch_login_redirect_target,
    ):
        assert fn(root) == 0, f"{fn.__name__} must return 0, not raise, on a project with no src/"


def test_functions_handle_unreadable_jsx_gracefully(tmp_path):
    # A file that exists but can't be decoded as UTF-8 must be skipped,
    # not crash the whole patch pass.
    root = _project(tmp_path, {"src/pages/Good.jsx": "return <ChevronRight />;\n"})
    bad = root / "src" / "pages" / "Bad.jsx"
    bad.write_bytes(b"\xff\xfe\x00bad binary content\x00\xff")
    n = _patch_missing_icon_imports(root)
    # errors="replace" on read means this still gets processed, not skipped --
    # the important assertion is that it doesn't raise and Good.jsx still
    # gets fixed correctly.
    assert n >= 1
    assert "ChevronRight" in (root / "src/pages/Good.jsx").read_text(encoding="utf-8")


if __name__ == "__main__":
    import inspect
    import traceback

    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            with tempfile.TemporaryDirectory() as td:
                params = inspect.signature(t).parameters
                if "tmp_path" in params:
                    t(Path(td))
                else:
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
