"""
Experiment 053 (Repair Pipeline Consolidation), Task 6: one repair
function's exception must not stop unrelated repairs from running.

Confirmed gap from Experiment 051's audit: run_deterministic_patches's
~40-call sequential list had no per-call exception isolation, unlike
preflight.py's PreflightRegistry (which wraps every fix in its own
try/except). An unhandled exception partway through the sequence used to
abort every remaining patcher -- including unrelated ones. Fixed via
_run_patch_isolated, a small per-call exception boundary.

These tests prove two things directly, not by inspection:
1. The happy path is byte-for-byte unchanged (covered by the full existing
   test_*.py suite passing unmodified -- these tests focus on the new
   failure-isolation behavior specifically).
2. A patcher that raises no longer takes down the rest of the sequence,
   and the run_deterministic_patches call itself still returns normally.

Run directly: python tests/reliability/test_repair_failure_isolation.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import _run_patch_isolated, run_deterministic_patches


def test_isolated_call_records_zero_and_swallows_exception():
    counts = {}

    def boom(root):
        raise RuntimeError("simulated patcher crash")

    _run_patch_isolated(counts, "some_patcher", boom, "fake_root")  # must not raise
    assert counts["some_patcher"] == 0


def test_isolated_call_preserves_successful_return_value():
    counts = {}

    def works(root):
        return 3

    _run_patch_isolated(counts, "some_patcher", works, "fake_root")
    assert counts["some_patcher"] == 3


def test_isolated_call_preserves_or_zero_semantics_for_none_return():
    # Existing behavior (pre-Exp053) was `fn(root) or 0` -- a patcher
    # returning None (many do, e.g. _patch_main_fk_imports) must still
    # record 0, not None or a crash.
    counts = {}

    def returns_none(root):
        return None

    _run_patch_isolated(counts, "some_patcher", returns_none, "fake_root")
    assert counts["some_patcher"] == 0


def test_isolated_call_passes_through_args_and_kwargs():
    counts = {}
    received = {}

    def fn(root, extra=None):
        received["root"] = root
        received["extra"] = extra
        return 1

    _run_patch_isolated(counts, "k", fn, "the_root", extra="x")
    assert received == {"root": "the_root", "extra": "x"}
    assert counts["k"] == 1


def test_one_raising_patcher_does_not_stop_run_deterministic_patches():
    # End-to-end: make ONE real patcher in the actual sequence raise, and
    # confirm run_deterministic_patches still completes, still returns a
    # dict, and still ran patchers AFTER the one that raised.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "app" / "models").mkdir(parents=True)
        (root / "app" / "routes").mkdir(parents=True)
        (root / "app" / "schemas").mkdir(parents=True)

        with mock.patch(
            "app.services.deterministic_patcher._patch_strip_relationships",
            side_effect=RuntimeError("simulated crash mid-sequence"),
        ):
            # Must not raise -- this is the actual regression this task fixes.
            counts = run_deterministic_patches(str(root))

        assert isinstance(counts, dict)
        assert counts["_patch_strip_relationships"] == 0
        # A patcher that runs LATER in the sequence than the one that
        # crashed must still have executed (present in counts at all,
        # not just absent because the function bailed out early).
        assert "_patch_dangling_foreign_keys" in counts
        assert "_total_modified" in counts


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
