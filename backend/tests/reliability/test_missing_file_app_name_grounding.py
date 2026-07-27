"""
Regression test for missing_file_prompt.py's _find_app_name.

Reproduced live (habit_tracker, 2026-07-27): src/components/Layout.jsx was
missing, so the missing-file agent regenerated it -- but nothing in the
prompt told it what the app is actually called (_find_page_intent only
covers src/pages/, _find_resource_model_and_schema only covers
app/routes/*_routes.py). The LLM fell back to the generic placeholder
"My Application" for the header/footer text on every single page in the
deployed app, since Layout wraps every page. index.html's <title> always
carries the real app name (set by the static theme-builder template, never
LLM-generated), so it's a free, reliable grounding signal that just wasn't
being read for component files.

Run directly: python tests/reliability/test_missing_file_app_name_grounding.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.prompts.missing_file_prompt import _find_app_name, build_missing_file_prompt


def _make_project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="appnametest_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def _cleanup(root: Path):
    shutil.rmtree(root, ignore_errors=True)


_INDEX_HTML = (
    '<!doctype html>\n<html lang="en">\n  <head>\n'
    '    <meta charset="UTF-8" />\n'
    '    <title>Habit Tracker</title>\n'
    "  </head>\n  <body>\n    <div id=\"root\"></div>\n  </body>\n</html>\n"
)


def test_finds_real_app_name_from_index_html_title():
    root = _make_project({"index.html": _INDEX_HTML})
    name = _find_app_name(str(root))
    _cleanup(root)
    assert name == "Habit Tracker"


def test_returns_none_without_index_html():
    root = Path(tempfile.mkdtemp(prefix="appnametest_"))
    name = _find_app_name(str(root))
    _cleanup(root)
    assert name is None


def test_returns_none_without_project_path():
    assert _find_app_name(None) is None


def test_returns_none_when_title_tag_missing():
    root = _make_project({"index.html": "<html><head></head><body></body></html>"})
    name = _find_app_name(str(root))
    _cleanup(root)
    assert name is None


def test_prompt_includes_real_app_name_for_component_file():
    root = _make_project({"index.html": _INDEX_HTML})
    prompt = build_missing_file_prompt(
        "src/components/Layout.jsx",
        "Missing frontend import target: ../components/Layout",
        project_path=str(root),
    )
    _cleanup(root)
    assert "Habit Tracker" in prompt
    assert "My Application" in prompt  # named as the forbidden placeholder, not used as the name
    assert "never a" in prompt.lower() or "never" in prompt.lower()


def test_prompt_forbids_placeholder_endpoint_fetch_for_components():
    prompt = build_missing_file_prompt(
        "src/components/Layout.jsx",
        "Missing frontend import target: ../components/Layout",
        project_path=None,
    )
    assert "your-endpoint" in prompt.lower()
    assert "do not" in prompt.lower() or "do NOT" in prompt


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
