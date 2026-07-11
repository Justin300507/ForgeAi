"""
Verifies _python_syntax_error() and its wiring into the repair loop's
three .py write sites in app/repair/orchestrator.py (_apply_fix_group,
_regenerate_module, _synthesize_missing_backend_files).

Root cause this fixes (confirmed live, 2026-07-11, real generated
output): the .jsx/.js/.tsx/.ts patch path already rejects a fix with
unbalanced JSX tags before writing it (_jsx_tag_mismatches) -- the .py
path had NO equivalent syntax gate at all, so a syntactically broken LLM
fix response got written straight to disk. A real fix responding to a
"Duplicate class definition" diagnostic on a generated CRM app's
contact_routes.py rewrote it with a multi-line `from app.schemas.contact
import (` whose body had three more complete `from ... import X`
statements spliced into the middle of the open parenthesis -- guaranteed
SyntaxError at the very next Compile stage, discovered only after burning
a full verify cycle.

Run directly: python tests/reliability/test_python_fix_syntax_gate.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.repair.orchestrator import _python_syntax_error


def test_valid_python_returns_none():
    assert _python_syntax_error("import os\n\ndef f():\n    return 1\n") is None


def test_the_actual_real_bug_is_detected():
    """The exact malformed content found live: a new import statement
    spliced into the middle of an existing multi-line parenthesized
    import block."""
    broken = (
        "from app.schemas.contact import (\n"
        "from app.schemas.contact import NoteResponse\n"
        "    ContactBase,\n"
        "    ContactCreate,\n"
        ")\n"
    )
    err = _python_syntax_error(broken)
    assert err is not None
    assert "line" in err


def test_unbalanced_parens_detected():
    assert _python_syntax_error("def f(a, b:\n    return a\n") is not None


def test_indentation_error_detected():
    assert _python_syntax_error("def f():\nreturn 1\n") is not None


def test_empty_content_is_valid_python():
    # An empty file is syntactically valid (if semantically useless) --
    # this function only guards against SyntaxError, nothing broader.
    assert _python_syntax_error("") is None


def test_apply_fix_group_rejects_before_write():
    src_path = os.path.join(os.path.dirname(__file__), "..", "..",
                             "app", "repair", "orchestrator.py")
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    # All three .py write sites must call the gate before target.write_text
    assert src.count("_python_syntax_error(") >= 4  # 1 def + 3 call sites
    assert 'elif target.suffix == ".py":' in src


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
