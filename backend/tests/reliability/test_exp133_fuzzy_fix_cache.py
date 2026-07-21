"""
Experiment 133 (FixCache Fuzzy Matching, shadow mode): regression tests for
the composite-key near-duplicate lookup tier added to
app/knowledge/failure_db.py and its shadow-mode wiring in
app/repair/orchestrator.py.

See docs/superpowers/specs/2026-07-21-fixcache-fuzzy-matching-design.md for
the full design and the proxy-analysis evidence behind the "strip only
digits, never quoted identifiers" normalization rule enforced below.

Run directly: python tests/reliability/test_exp133_fuzzy_fix_cache.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import app.knowledge.failure_db as fdb
from app.core.context import Diagnostic, ErrorCategory, ErrorSeverity


def _isolated_cache(tmpdir: str) -> Path:
    """Point FixCache's storage path at a throwaway file for this test, and
    rebuild the singleton's state so no other test's data leaks in."""
    path = Path(tmpdir) / "repair_db.json"
    fdb._CACHE_PATH = path
    return path


def test_old_entries_load_without_migration():
    with tempfile.TemporaryDirectory() as td:
        cache_path = _isolated_cache(td)
        old_entry = {
            "fix_hash": "abc123",
            "fix_content": {"app/utils/auth.py": "old content"},
            "success_count": 3,
            "first_seen": "2026-01-01T00:00:00Z",
            "last_seen": "2026-01-02T00:00:00Z",
            "source_idea": "todo app",
        }
        cache_path.write_text(json.dumps({"abc123": old_entry}), encoding="utf-8")
        cache = fdb.FixCache()
        cf = fdb.CachedFix(**cache._data["abc123"])
        assert cf.success_count == 3
        assert cf.fix_content == {"app/utils/auth.py": "old content"}
        # New Exp133 fields must default cleanly for pre-existing entries
        assert cf.category == ""
        assert cf.file_basename == ""
        assert cf.composite_hash == ""
        assert cf.normalized_signature == ""
        assert cf.files_changed == []
        assert cf.imports_added == []
        assert cf.symbols_added == []


def test_normalization_strips_only_digits():
    assert fdb._normalize_message("Edit entity: 405") == "Edit entity: <N>"
    assert fdb._normalize_message("Edit entity: 422") == "Edit entity: <N>"
    assert fdb._normalize_message("Transform failed with 6 errors") == \
        fdb._normalize_message("Transform failed with 9 errors")


def test_normalization_preserves_dangerous_identifiers():
    # Exp133 proxy analysis (generation_log.jsonl, 278 runs): stripping quoted
    # identifiers merged 3 of 5 collisions into failures needing DIFFERENT
    # fixes. These two must never normalize to the same string.
    a = fdb._normalize_message(
        "[AttributeError] AttributeError: type object 'Response' has no attribute 'create'")
    b = fdb._normalize_message(
        "[AttributeError] AttributeError: type object 'Donation' has no attribute 'create'")
    assert a != b, "quoted class names must remain distinguishable"

    c = fdb._normalize_message("Undefined symbol 'User' in app/routes/stats_routes.py")
    d = fdb._normalize_message("Undefined symbol 'func' in app/routes/stats_routes.py")
    assert c != d, "quoted symbol names must remain distinguishable"


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
