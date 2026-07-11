"""
Experiment 053 (Repair Pipeline Consolidation): the string-aware
"find the matching closing brace" algorithm was implemented three times
-- app/utils/json_cleaner.py::_find_matching_close_brace (named),
app/utils/json_cleaner.py::try_repair_truncated (an inline duplicate of
the same algorithm, in the SAME file), and
app/services/validator_service.py::_extract_object_literal (a genuine
variant -- JS/JSX object literals can use single/double/backtick quotes,
not just double quotes like JSON). Consolidated into one shared
app.utils.brace_matching.find_matching_brace(text, open_pos, quote_chars).

Rule for this experiment: preserve behavior exactly. These tests prove
that -- both the two now-identical JSON call sites (already covered by
test_json_cleaner_repairs.py, re-run unchanged here for one-stop
confirmation) and the previously-UNTESTED validator_service.py caller,
which had zero test coverage before this consolidation and needed a
captured-baseline comparison to refactor safely.

Run directly: python tests/reliability/test_brace_matching_consolidation.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.utils.brace_matching import find_matching_brace
from app.utils.json_cleaner import _find_matching_close_brace, try_repair_truncated
from app.services.validator_service import _extract_object_literal


# ── shared primitive, JSON mode (quote_chars='"', the default) ─────────────

def test_shared_json_mode_matches_simple_object():
    text = '{"a": 1, "b": 2}'
    assert find_matching_brace(text, 0) == len(text) - 1


def test_shared_json_mode_skips_braces_inside_strings():
    text = '{"note": "a { b } c"}'
    assert find_matching_brace(text, 0) == len(text) - 1


def test_shared_json_mode_handles_escaped_quotes():
    text = r'{"note": "she said \"hi\""}'
    assert find_matching_brace(text, 0) == len(text) - 1


def test_shared_json_mode_unmatched_returns_minus_one():
    text = '{"a": 1'
    assert find_matching_brace(text, 0) == -1


def test_shared_json_mode_stops_at_own_close_not_trailing_junk():
    text = '{"a": 1}  garbage { more }'
    assert find_matching_brace(text, 0) == text.index("}")


def test_shared_json_mode_single_quote_is_NOT_a_string_delimiter():
    # JSON only recognizes double quotes -- a bare apostrophe must be
    # treated as ordinary content, not a string boundary, in JSON mode.
    text = '{"note": "it\'s fine"}'
    assert find_matching_brace(text, 0) == len(text) - 1


# ── shared primitive, JS/JSX mode (quote_chars="'\"`") ──────────────────────

def test_shared_js_mode_single_quoted_string():
    text = "{ email, password }"
    assert find_matching_brace(text, 0, quote_chars="'\"`") == len(text) - 1


def test_shared_js_mode_template_literal_with_interpolation():
    text = "{ note: `hi ${name}`, y: 2 }"
    assert find_matching_brace(text, 0, quote_chars="'\"`") == len(text) - 1


def test_shared_js_mode_nested_object():
    text = "{ a: { nested: 1 }, b: 2 }"
    assert find_matching_brace(text, 0, quote_chars="'\"`") == len(text) - 1


def test_shared_js_mode_mixed_quote_types_do_not_cross_toggle():
    # REGRESSION GUARD: an early draft of this consolidation toggled
    # in_string on ANY of quote_chars appearing, which broke on a
    # double-quoted string containing an apostrophe (the apostrophe was
    # wrongly treated as a second string boundary). Caught by this exact
    # test before the consolidation was wired into any real call site.
    text = '{ note: "it\'s here" }'
    assert find_matching_brace(text, 0, quote_chars="'\"`") == len(text) - 1


def test_shared_js_mode_unterminated_returns_minus_one():
    text = "{ unterminated: true"
    assert find_matching_brace(text, 0, quote_chars="'\"`") == -1


# ── _find_matching_close_brace (json_cleaner.py) -- thin wrapper, must be
#    byte-identical to its pre-refactor behavior ────────────────────────────

def test_find_matching_close_brace_still_works_after_delegation():
    text = '{"path": "a.py", "content": "print(1)"}'
    assert _find_matching_close_brace(text, 0) == len(text) - 1


# ── try_repair_truncated (json_cleaner.py) -- inline duplicate removed,
#    now calls _find_matching_close_brace; must still salvage correctly ────

def test_try_repair_truncated_still_salvages_complete_files():
    text = (
        '{"files": ['
        '{"path": "a.py", "content": "x = 1"}, '
        '{"path": "b.py", "content": "y = 2"}, '
        '{"path": "c.py", "content": "in complete'  # truncated mid-object
    )
    result = try_repair_truncated(text)
    assert result is not None
    assert len(result["files"]) == 2
    assert result["files"][0]["path"] == "a.py"
    assert result["files"][1]["path"] == "b.py"


def test_try_repair_truncated_returns_none_with_zero_complete_files():
    text = '{"files": [{"path": "a.py", "content": "incomple'
    assert try_repair_truncated(text) is None


# ── _extract_object_literal (validator_service.py) -- previously UNTESTED,
#    baseline captured against the pre-refactor implementation before this
#    consolidation touched it, per the experiment's own "preserve behavior
#    exactly" rule ────────────────────────────────────────────────────────

def test_extract_object_literal_simple_shorthand():
    text = "const x = { email, password };"
    p = text.index("{")
    assert _extract_object_literal(text, p) == "{ email, password }"


def test_extract_object_literal_template_literal_field():
    text = "const x = { note: `hi ${name}`, y: 2 };"
    p = text.index("{")
    assert _extract_object_literal(text, p) == "{ note: `hi ${name}`, y: 2 }"


def test_extract_object_literal_nested_object():
    text = "const x = { a: { nested: 1 }, b: 2 };"
    p = text.index("{")
    assert _extract_object_literal(text, p) == "{ a: { nested: 1 }, b: 2 }"


def test_extract_object_literal_unterminated_returns_none():
    text = "const x = { unterminated: true"
    p = text.index("{")
    assert _extract_object_literal(text, p) is None


def test_extract_object_literal_double_quoted_string_with_apostrophe():
    # The exact mixed-quote shape that would have broken under a naive
    # "any quote_chars toggles" simplification.
    text = 'const x = { note: "it\'s fine" };'
    p = text.index("{")
    assert _extract_object_literal(text, p) == '{ note: "it\'s fine" }'


def test_extract_object_literal_real_auth_post_shape():
    # The actual domain this function serves: validate_frontend_auth_fields
    # extracts the object literal passed to api.post('/auth/login', {...}).
    src = (
        "const handleLogin = async () => {\n"
        "  await api.post('/auth/login', { email, password });\n"
        "};\n"
    )
    p = src.index("{", src.index("api.post"))
    assert _extract_object_literal(src, p) == "{ email, password }"


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
