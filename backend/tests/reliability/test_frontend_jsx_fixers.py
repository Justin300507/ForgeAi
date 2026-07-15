"""
Exp104: write-time JSX fixers in app/services/frontend_service.py.

The stray-paren fixer targets the confirmed live shape (2026-07-15,
event_manager_platform canary + 32 files across 7 corpus apps): an
attribute's template literal closed with `)}` instead of `}`, emitted
verbatim by the frontend LLM when reproducing the prompt's own correct
toast example. Full-corpus replay evidence: 1106 .jsx files scanned,
exactly the 32 known-bad files changed, 0 false positives; esbuild
transform of a real broken file goes exit 1 -> exit 0 under this fix.
"""
import inspect
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services import frontend_service
from app.services.frontend_service import _fix_stray_paren_after_attr_template


TOAST_BROKEN = (
    "{toast && (\n"
    "  <div className={`animate-scale-in fixed bottom-4 right-4 px-4 py-3 "
    "rounded-xl shadow-lg text-sm font-medium text-white z-50 ${toastColorClass}`)}>\n"
    "    {toast.msg}\n"
    "  </div>\n"
    ")}\n"
)


def test_fixes_confirmed_toast_shape():
    """The exact corpus shape: ...${toastColorClass}`)}  ->  ...`}."""
    fixed = _fix_stray_paren_after_attr_template(TOAST_BROKEN)
    assert "`)}" not in fixed
    assert "${toastColorClass}`}>" in fixed
    # The wrapper's legitimate closing ')}' on its own line is untouched.
    assert fixed.endswith(")}\n")


def test_fixes_shape_with_balanced_parens_inside_interpolation():
    src = "<div className={`w-${items.map((i) => i.id).join('-')}`)}>x</div>"
    fixed = _fix_stray_paren_after_attr_template(src)
    assert fixed == "<div className={`w-${items.map((i) => i.id).join('-')}`}>x</div>"


def test_skips_unbalanced_parens_inside_template():
    """A '(' opened inside ${...} could legitimately be closed by the ')',
    so the guard must leave the span alone rather than guess."""
    src = "<div className={`w-${fn(x}`)}>x</div>"
    assert _fix_stray_paren_after_attr_template(src) == src


def test_leaves_correct_template_attribute_untouched():
    src = "<div className={`a ${b}`}>x</div>"
    assert _fix_stray_paren_after_attr_template(src) == src


def test_leaves_function_call_attributes_untouched():
    """')}' that genuinely closes a call is out of scope: the regex requires
    the backtick to open immediately after '={'."""
    for src in (
        "<button onClick={() => navigate(`/events/${id}`)}>go</button>",
        "<div className={clsx(`a ${b}`)}>x</div>",
    ):
        assert _fix_stray_paren_after_attr_template(src) == src


def test_fixes_every_occurrence_not_just_first():
    src = "<a className={`x ${a}`)}>1</a>\n<b className={`y ${b}`)}>2</b>"
    fixed = _fix_stray_paren_after_attr_template(src)
    assert "`)}" not in fixed
    assert fixed.count("`}>") == 2


def test_wired_into_generate_frontend_write_chain():
    """Guard the wiring, not just the function: the write-time chain in
    generate_frontend must run this fixer (Exp055's lesson -- an unwired
    fixer silently regresses)."""
    source = inspect.getsource(frontend_service.generate_frontend)
    assert "_fix_stray_paren_after_attr_template(" in source


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
