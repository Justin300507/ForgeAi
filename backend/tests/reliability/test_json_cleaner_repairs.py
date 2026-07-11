"""
Exp052: json_cleaner.py repairs the LLM's own raw JSON *response text*
before it's parsed into files -- a different failure domain from every
other repair in this suite (which edit generated app code). Malformed
JSON from an LLM (truncation, unescaped inner quotes, triple-quoted
blocks, Windows path backslashes) is a real, frequent failure mode this
module exists to survive.

Run directly: python tests/reliability/test_json_cleaner_repairs.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.utils.json_cleaner import (
    extract_json, try_repair_truncated, _find_matching_close_brace,
    _extract_single_file_object, _escape_inner_quotes, _fix_path_backslashes,
    _fix_triple_quoted_content,
)
import re


# ── extract_json: the entry point ───────────────────────────────────────────

def test_clean_valid_json_passthrough():
    text = '{"path": "app/main.py", "content": "print(1)"}'
    result = extract_json(text)
    assert result == {"path": "app/main.py", "content": "print(1)"}


def test_idempotent_noop_on_already_valid_json():
    text = '{"files": [{"path": "a.py", "content": "x = 1"}]}'
    first = extract_json(text)
    # Re-running on the same original text (not on the parsed dict -- this
    # module's contract is text-in/dict-out) must yield an identical result.
    second = extract_json(text)
    assert first == second == {"files": [{"path": "a.py", "content": "x = 1"}]}


def test_strips_markdown_fences():
    text = '```json\n{"path": "x.py", "content": "y"}\n```'
    assert extract_json(text) == {"path": "x.py", "content": "y"}


def test_strips_bare_markdown_fence_no_json_tag():
    text = '```\n{"path": "x.py", "content": "y"}\n```'
    assert extract_json(text) == {"path": "x.py", "content": "y"}


def test_no_json_at_all_raises_valueerror_not_silent_failure():
    try:
        extract_json("I'm sorry, I cannot help with that request.")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_windows_path_backslashes_escaped():
    text = '{"path": "app\\routes\\user_routes.py", "content": "x"}'
    result = extract_json(text)
    assert result["path"] == "app\\routes\\user_routes.py"


def test_trailing_garbage_after_valid_object_ignored():
    # A real observed LLM failure shape: valid JSON followed by trailing
    # prose ("Let me know if you need anything else!") -- the classic
    # json.JSONDecodeError "Extra data" case.
    text = '{"path": "a.py", "content": "x"}\n\nLet me know if you need anything else!'
    result = extract_json(text)
    assert result == {"path": "a.py", "content": "x"}


def test_multiple_files_array_shape():
    text = '{"files": [{"path": "a.py", "content": "1"}, {"path": "b.py", "content": "2"}]}'
    result = extract_json(text)
    assert len(result["files"]) == 2
    assert result["files"][0]["path"] == "a.py"
    assert result["files"][1]["path"] == "b.py"


def test_real_newlines_in_content_preserved_as_escaped():
    text = '{"path": "a.py", "content": "line1\nline2"}'
    result = extract_json(text)
    assert result["content"] == "line1\nline2"


def test_unescaped_inner_quotes_in_content_recovered():
    # A real observed shape: the LLM embeds a Python dict literal containing
    # double-quoted keys inside the JSON string value without escaping them.
    text = '{"path": "app/schemas/user.py", "content": "model_config = {"from_attributes": True}"}'
    result = extract_json(text)
    assert result["path"] == "app/schemas/user.py"
    assert "from_attributes" in result["content"]


# ── _find_matching_close_brace ──────────────────────────────────────────────

def test_matching_brace_skips_braces_inside_strings():
    text = '{"a": "value with } inside", "b": 2}'
    end = _find_matching_close_brace(text, 0)
    assert end == len(text) - 1
    assert text[end] == "}"


def test_matching_brace_stops_before_trailing_extra_data():
    text = '{"a": 1}   {"b": 2}'
    end = _find_matching_close_brace(text, 0)
    assert text[0:end + 1] == '{"a": 1}'


def test_matching_brace_returns_minus_one_on_truncation():
    text = '{"a": {"b": 1'
    assert _find_matching_close_brace(text, 0) == -1


def test_matching_brace_nested_objects():
    text = '{"a": {"b": {"c": 1}}, "d": 2}'
    end = _find_matching_close_brace(text, 0)
    assert end == len(text) - 1


# ── try_repair_truncated ─────────────────────────────────────────────────────

def test_truncated_response_salvages_complete_files():
    # Two complete file objects, then a cut-off third (a real Cerebras
    # token-limit truncation shape).
    text = (
        '{"files": [{"path": "a.py", "content": "x = 1"}, '
        '{"path": "b.py", "content": "y = 2"}, '
        '{"path": "c.py", "content": "z = '
    )
    result = try_repair_truncated(text)
    assert result is not None
    assert len(result["files"]) == 2
    assert result["files"][0]["path"] == "a.py"
    assert result["files"][1]["path"] == "b.py"


def test_truncated_with_zero_complete_files_returns_none():
    text = '{"files": [{"path": "a.py", "content": "x = '
    assert try_repair_truncated(text) is None


def test_truncated_no_files_key_returns_none():
    assert try_repair_truncated('{"path": "a.py"') is None


def test_extract_json_falls_back_to_truncation_repair():
    # extract_json's own last-resort path should invoke try_repair_truncated
    # when nothing else parses.
    text = '{"files": [{"path": "a.py", "content": "ok"}, {"path": "b.py", "content": "cut off'
    result = extract_json(text)
    assert "files" in result
    assert result["files"][0]["path"] == "a.py"


# ── _extract_single_file_object ─────────────────────────────────────────────

def test_extract_single_file_object_last_quote_heuristic():
    text = '{"path": "a.py", "content": "x = {"nested": "dict"} plus more"}'
    result = _extract_single_file_object(text)
    assert result is not None
    assert result["path"] == "a.py"
    assert "nested" in result["content"]


def test_extract_single_file_object_bails_on_files_array_shape():
    text = '{"files": [{"path": "a.py", "content": "x"}]}'
    assert _extract_single_file_object(text) is None


def test_extract_single_file_object_missing_fields_returns_none():
    assert _extract_single_file_object('{"path": "a.py"}') is None


# ── _escape_inner_quotes ─────────────────────────────────────────────────────

def test_escape_inner_quotes_field_separator_comma_not_escaped():
    text = '{"a": "value",\n"b": "next"}'
    out = _escape_inner_quotes(text)
    # the JSON structure quotes must survive un-escaped
    assert out.count('\\"') == 0


def test_escape_inner_quotes_prose_comma_is_escaped():
    text = '{"a": "he said "hi", then left"}'
    out = _escape_inner_quotes(text)
    assert '\\"hi\\"' in out


# ── _fix_path_backslashes / _fix_triple_quoted_content (regex-callback units) ──

def test_fix_path_backslashes_doubles_single_backslashes():
    m = re.match(r'"path"\s*:\s*"((?:[^"\\]|\\.)*)"', '"path": "a\\b\\c"')
    assert _fix_path_backslashes(m) == '"path": "a\\\\b\\\\c"'


def test_fix_triple_quoted_content_converts_to_valid_json_string():
    text = '{"content": """line one\nline two"""}'
    out = _fix_triple_quoted_content(text)
    assert '"""' not in out
    # must now be parseable as a normal JSON string value
    result = json_module_loads_or_none(out)
    assert result is not None


def json_module_loads_or_none(text):
    import json
    try:
        return json.loads(text)
    except Exception:
        return None


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
