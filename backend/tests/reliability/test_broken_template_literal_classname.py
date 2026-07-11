"""
Experiment 049: the LLM's single most common way to break a Vite build --
a className built from a template literal containing a multi-line ternary
where the `${` interpolation opener got dropped or left empty. Seen across
many unrelated components (Pagination, Dashboard, Toast, Register,
Calendar), not one app's quirk.

deterministic_patcher._patch_broken_template_literal_classname collapses
the broken attribute to a static className string -- guaranteed-valid
syntax, at the cost of that one element's conditional styling.

These fixtures are real content pulled from backend/llm_cache/ and from
generated_projects/ during the corpus investigation, not synthetic
approximations. Where esbuild is available (frontend/node_modules), every
assertion is checked against the REAL parser, not a heuristic -- an earlier
draft of this detector looked plausible but had an 85% false-positive rate
against the full 882-file generated_projects/ corpus before three rounds of
refinement; esbuild is what actually caught that, so it's what these tests
use too.

Run directly: python tests/reliability/test_broken_template_literal_classname.py
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import _patch_broken_template_literal_classname

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_ESBUILD = os.path.join(_REPO_ROOT, "frontend", "node_modules", ".bin",
                         "esbuild.cmd" if os.name == "nt" else "esbuild")
_HAS_ESBUILD = os.path.exists(_ESBUILD)


def _esbuild_ok(jsx_source: str) -> bool:
    """True if esbuild parses this as valid JSX. Skips (returns True) when
    esbuild isn't available, so the suite stays runnable without a frontend
    node_modules install -- the structural assertions still run either way."""
    if not _HAS_ESBUILD:
        return True
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".jsx", delete=False, encoding="utf-8") as f:
        f.write(jsx_source)
        path = f.name
    try:
        r = subprocess.run([_ESBUILD, path], capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    finally:
        os.unlink(path)


# ── Real broken examples (from backend/llm_cache/, 2026-07-11 corpus check) ──

BROKEN_DROPPED_DOLLAR = """\
const Pagination = ({ currentPage, onPageChange }) => (
  <button
    key={page}
    onClick={() => onPageChange(page)}
    className={`px-3 py-1 rounded-lg text-sm font-medium `
      currentPage === page
        ? 'bg-emerald-600 text-white'
        : 'text-slate-700 dark:text-slate-300'
    }`}
  >
    {page}
  </button>
);
"""

BROKEN_EMPTY_INTERP = """\
const Toast = ({ toast }) => (
  <div className={`fixed bottom-4 right-4 px-4 py-3 rounded-xl shadow-lg text-sm font-medium text-white z-50 ${}`
    toast.type === 'success' ? 'bg-emerald-600' : 'bg-red-600'
  }`}>
    {toast.msg}
  </div>
);
"""

BROKEN_CHAINED_TERNARY_WITH_TAG_SUFFIX = """\
const Deal = ({ deal }) => (
  <span className={`text-xs font-bold uppercase tracking-wider px-2 py-1 `
    deal.status === 'Won' ? 'text-emerald-600 bg-emerald-50' :
    deal.status === 'Lost' ? 'text-red-600 bg-red-50' :
    'text-sky-600 bg-sky-50'
  }`}>
    {deal.status}
  </span>
);
"""


def test_dropped_dollar_brace_collapses_to_static_and_builds():
    fixed, n = _patch_broken_template_literal_classname(BROKEN_DROPPED_DOLLAR)
    assert n == 1
    assert "`" not in fixed
    assert 'className="px-3 py-1 rounded-lg text-sm font-medium"' in fixed
    assert _esbuild_ok(fixed)


def test_empty_interpolation_collapses_to_static_and_builds():
    fixed, n = _patch_broken_template_literal_classname(BROKEN_EMPTY_INTERP)
    assert n == 1
    assert "`" not in fixed
    assert _esbuild_ok(fixed)


def test_chained_ternary_with_tag_suffix_preserved_and_builds():
    fixed, n = _patch_broken_template_literal_classname(BROKEN_CHAINED_TERNARY_WITH_TAG_SUFFIX)
    assert n == 1
    assert "`" not in fixed
    assert ">" in fixed  # the tag's closing `>` must survive, not just get dropped
    assert _esbuild_ok(fixed)


# ── Real VALID patterns that must NOT be touched (false positives found ──
# ── and fixed during the 2026-07-11 corpus sweep) ──────────────────────────

VALID_MULTILINE_INTERPOLATION = """\
const Day = ({ isCurrentMonth, completed }) => (
  <div
    className={`relative w-9 h-9 flex items-center justify-center rounded-lg text-sm font-medium
      ${isCurrentMonth ? 'text-slate-900 dark:text-white' : 'text-slate-400 dark:text-slate-600'}
      ${completed ? 'bg-emerald-100 dark:bg-emerald-900/30' : ''}
    `}
  >
    {day}
  </div>
);
"""

VALID_SAME_LINE_TAG_CLOSE = """\
const Badge = ({ booking, statusClass }) => (
  <div>
    <span className={`badge ${statusClass(booking.status)}`}>{booking.status}</span>
  </div>
);
"""

VALID_CONCATENATION = """\
const Task = ({ task }) => (
  <button
    className={`w-6 h-6 rounded-lg border flex items-center justify-center ` +
      (task.completed
        ? 'bg-emerald-500 border-emerald-500 text-white'
        : 'border-slate-300 dark:border-slate-600')
    }
    aria-label={task.completed ? 'Mark pending' : 'Mark completed'}
  >
    x
  </button>
);
"""

VALID_SINGLE_LINE_TEMPLATE = """\
const Icon = ({ n }) => <span className={`icon icon-${n}`}>*</span>;
"""


def test_valid_multiline_interpolation_untouched():
    fixed, n = _patch_broken_template_literal_classname(VALID_MULTILINE_INTERPOLATION)
    assert n == 0
    assert fixed == VALID_MULTILINE_INTERPOLATION
    assert _esbuild_ok(VALID_MULTILINE_INTERPOLATION)


def test_valid_same_line_tag_close_untouched():
    fixed, n = _patch_broken_template_literal_classname(VALID_SAME_LINE_TAG_CLOSE)
    assert n == 0
    assert fixed == VALID_SAME_LINE_TAG_CLOSE
    assert _esbuild_ok(VALID_SAME_LINE_TAG_CLOSE)


def test_valid_concatenation_untouched():
    fixed, n = _patch_broken_template_literal_classname(VALID_CONCATENATION)
    assert n == 0
    assert fixed == VALID_CONCATENATION
    assert _esbuild_ok(VALID_CONCATENATION)


def test_valid_single_line_template_untouched():
    fixed, n = _patch_broken_template_literal_classname(VALID_SINGLE_LINE_TEMPLATE)
    assert n == 0
    assert fixed == VALID_SINGLE_LINE_TEMPLATE


def test_no_match_on_plain_static_classname():
    src = 'const X = () => <div className="flex items-center">hi</div>;\n'
    fixed, n = _patch_broken_template_literal_classname(src)
    assert n == 0
    assert fixed == src


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
    print(f"\n{len(tests) - failed}/{len(tests)} passed  (esbuild {'available' if _HAS_ESBUILD else 'NOT available -- structural checks only'})")
    raise SystemExit(1 if failed else 0)
