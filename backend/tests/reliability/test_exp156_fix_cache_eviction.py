"""
Exp156 (habit_tracker, 2026-07-31): a cached fix stored before the "only
cache on confirmed success" gate existed (or one whose context-dependent
content regresses in a DIFFERENT project than it was proven in) gets
replayed verbatim forever -- FixCache.lookup() has no expiry and store()
only ever overwrites with a newer fix, never removes a bad one.

Confirmed live: app/schemas/stats.py's cached fix contained `Field(None,
min_length=1, default=None)` -- default supplied both positionally and
by keyword, a hard TypeError at import time. Every single repair attempt
replayed it via cache HIT, regressed, reverted, then replayed the
identical broken content again next attempt -- 3 consecutive attempts,
zero improvement, stuck at 41.9/F, same non-convergence shape as Exp146
but via a different mechanism (a cache that never forgets a mistake,
not an LLM re-guessing).

Run directly: python tests/reliability/test_exp156_fix_cache_eviction.py
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
    path = Path(tmpdir) / "repair_db.json"
    fdb._CACHE_PATH = path
    return path


def _diag(message: str) -> Diagnostic:
    return Diagnostic(
        error_id=message,
        category=ErrorCategory.RUNTIME,
        severity=ErrorSeverity.HIGH,
        source="test",
        message=message,
        file_path="app/schemas/stats.py",
    )


def test_evict_removes_a_stored_entry():
    with tempfile.TemporaryDirectory() as td:
        _isolated_cache(td)
        cache = fdb.FixCache()
        diags = [_diag("Runtime crash: TypeError: got multiple values for argument 'default'")]
        cache.store(diags, {"app/schemas/stats.py": "broken content"}, idea="habit tracker")

        assert cache.lookup(diags) is not None
        assert cache.evict(diags) is True
        assert cache.lookup(diags) is None


def test_evict_persists_across_reload():
    """The whole point is the NEXT job (a fresh FixCache() instance
    loading from disk) must not see the evicted entry either."""
    with tempfile.TemporaryDirectory() as td:
        _isolated_cache(td)
        cache = fdb.FixCache()
        diags = [_diag("Runtime crash: TypeError: got multiple values for argument 'default'")]
        cache.store(diags, {"app/schemas/stats.py": "broken content"}, idea="habit tracker")
        cache.evict(diags)

        reloaded = fdb.FixCache()
        assert reloaded.lookup(diags) is None


def test_evict_on_never_cached_diagnostics_is_a_safe_noop():
    with tempfile.TemporaryDirectory() as td:
        _isolated_cache(td)
        cache = fdb.FixCache()
        diags = [_diag("Runtime crash: something never seen before")]
        assert cache.evict(diags) is False


def test_evict_does_not_disturb_other_entries():
    with tempfile.TemporaryDirectory() as td:
        _isolated_cache(td)
        cache = fdb.FixCache()
        bad_diags = [_diag("Runtime crash: the poisoned one")]
        good_diags = [_diag("Runtime crash: a genuinely different, unrelated fact")]
        cache.store(bad_diags, {"app/schemas/stats.py": "broken"}, idea="app a")
        cache.store(good_diags, {"app/schemas/other.py": "fine"}, idea="app b")

        cache.evict(bad_diags)
        assert cache.lookup(bad_diags) is None
        assert cache.lookup(good_diags) is not None


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
