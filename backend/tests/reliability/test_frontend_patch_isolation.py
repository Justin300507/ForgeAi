"""
Experiment 055: regression tests for per-repair failure isolation inside
run_frontend_patches (deterministic_patcher.py).

CONFIRMED BUG (before this experiment, verified by direct reproduction
against the pre-fix code via `git stash`): run_frontend_patches's 14-call
sequence had NO exception isolation between calls. One raising patcher
aborted every remaining frontend patcher AND propagated uncaught out of
run_frontend_patches itself -- including to main.py::_resync_frontend,
which calls it with no try/except at all (a single bad frontend patcher
could 500 the entire "Check & Fix deployed app" resync).

This mirrors Exp053's `_run_patch_isolated` fix for the ~40-call backend
sequence, applied to the 14-call frontend sequence Exp053 itself flagged
as the remaining gap (docs/REPAIR_ARCHITECTURE.md §6).

Run directly: python tests/reliability/test_frontend_patch_isolation.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import app.services.deterministic_patcher as dp
from app.services.deterministic_patcher import (
    run_frontend_patches,
    _run_frontend_patches_detailed,
    _run_frontend_patch_isolated,
    FrontendPatchResult,
)

_EXPECTED_ORDER = [
    "_patch_frontend_package_json",
    "_patch_vite_root_proxy_and_api_base",
    "_patch_static_auth_header_to_interceptor",
    "_patch_disallowed_icon_packages",
    "_patch_invalid_lucide_icons",
    "_patch_missing_icon_imports",
    "_patch_frontend_auth_field_names",
    "_patch_frontend_signup_password_key",
    "_patch_stale_status_on_error",
    "_patch_unsafe_optional_chain_before_array_method",
    "_patch_response_data_used_as_bare_array",
    "_patch_response_data_assumed_wrapped",
    "_patch_hidden_loading_status",
    "_patch_pagination_component",
    "_patch_broken_template_literal_classnames",
    "patch_ensure_auth_pages",
]


def _empty_project(td: str) -> Path:
    project = Path(td)
    (project / "frontend" / "src").mkdir(parents=True)
    return project


# ── _run_frontend_patch_isolated (the isolation primitive itself) ────────────

def test_isolated_call_records_success_result():
    results = []
    n = _run_frontend_patch_isolated(results, "fake", lambda p: 3, Path("."))
    assert n == 3
    assert len(results) == 1
    r = results[0]
    assert r.name == "fake"
    assert r.success is True
    assert r.count == 3
    assert r.skipped is False
    assert r.exception is None
    assert r.duration_ms >= 0


def test_isolated_call_as_bool_converts_truthy_to_one():
    results = []
    n = _run_frontend_patch_isolated(results, "fake", lambda p: True, Path("."), as_bool=True)
    assert n == 1
    assert results[0].count == 1


def test_isolated_call_as_bool_converts_falsy_to_zero():
    results = []
    n = _run_frontend_patch_isolated(results, "fake", lambda p: False, Path("."), as_bool=True)
    assert n == 0
    assert results[0].count == 0


def test_isolated_call_none_return_becomes_zero_not_as_bool():
    # Matches the `or 0` convention every other dispatch mechanism in this
    # codebase already uses (Exp053's _run_patch_isolated, RepairRegistry).
    results = []
    n = _run_frontend_patch_isolated(results, "fake", lambda p: None, Path("."))
    assert n == 0
    assert results[0].success is True  # returning None is not a failure


def test_isolated_call_catches_exception_and_records_failure():
    results = []

    def boom(p):
        raise ValueError("simulated")

    n = _run_frontend_patch_isolated(results, "fake_boom", boom, Path("."))
    assert n == 0
    assert len(results) == 1
    r = results[0]
    assert r.success is False
    assert r.count == 0
    assert "ValueError" in r.exception
    assert "simulated" in r.exception


def test_isolated_call_does_not_raise():
    # The whole point: calling code must never see the exception.
    results = []
    _run_frontend_patch_isolated(results, "fake_boom", lambda p: 1 / 0, Path("."))  # no raise


# ── _run_frontend_patches_detailed (the real 16-call sequence) ───────────────

def test_detailed_runs_all_16_in_documented_order_on_clean_project():
    with tempfile.TemporaryDirectory() as td:
        project = _empty_project(td)
        n, results = _run_frontend_patches_detailed(project)
        assert len(results) == 16
        assert [r.name for r in results] == _EXPECTED_ORDER
        assert all(r.success for r in results), "all 16 must succeed cleanly on a near-empty project"
        assert n == sum(r.count for r in results)


def test_public_entry_point_matches_detailed_total():
    # run_frontend_patches() must keep returning the exact same int it
    # always did -- byte-for-byte compatible with its two existing callers.
    with tempfile.TemporaryDirectory() as td:
        project = _empty_project(td)
        n_public = run_frontend_patches(project)
        n_detailed, _results = _run_frontend_patches_detailed(project)
        assert n_public == n_detailed


def test_one_raising_patcher_does_not_stop_the_rest():
    original = dp._patch_frontend_auth_field_names
    try:
        dp._patch_frontend_auth_field_names = lambda p: (_ for _ in ()).throw(RuntimeError("mid-sequence crash"))
        with tempfile.TemporaryDirectory() as td:
            project = _empty_project(td)
            n, results = dp._run_frontend_patches_detailed(project)
        assert len(results) == 16, "all 16 must still run despite one raising"
        assert [r.name for r in results] == _EXPECTED_ORDER, "ordering must be preserved exactly"
        failed = [r for r in results if not r.success]
        assert len(failed) == 1
        assert failed[0].name == "_patch_frontend_auth_field_names"
        assert "RuntimeError" in failed[0].exception
        succeeded = [r for r in results if r.success]
        assert len(succeeded) == 15, "the other 15 must still have run and succeeded"
    finally:
        dp._patch_frontend_auth_field_names = original


def test_public_entry_point_no_longer_raises_when_one_patcher_crashes():
    # CONFIRMED via `git stash` against the pre-fix code: this exact
    # scenario used to propagate the RuntimeError uncaught out of
    # run_frontend_patches -- which main.py::_resync_frontend calls with
    # no try/except, so this is the difference between "Check & Fix"
    # working and 500ing entirely.
    original = dp._patch_stale_status_on_error
    try:
        dp._patch_stale_status_on_error = lambda p: (_ for _ in ()).throw(RuntimeError("boom"))
        with tempfile.TemporaryDirectory() as td:
            project = _empty_project(td)
            n = dp.run_frontend_patches(project)  # must not raise
        assert isinstance(n, int)
    finally:
        dp._patch_stale_status_on_error = original


def test_multiple_failures_all_isolated_independently():
    originals = {
        "_patch_disallowed_icon_packages": dp._patch_disallowed_icon_packages,
        "_patch_pagination_component": dp._patch_pagination_component,
        "patch_ensure_auth_pages": dp.patch_ensure_auth_pages,
    }
    try:
        dp._patch_disallowed_icon_packages = lambda p: (_ for _ in ()).throw(ValueError("first"))
        dp._patch_pagination_component = lambda p: (_ for _ in ()).throw(TypeError("second"))
        dp.patch_ensure_auth_pages = lambda p: (_ for _ in ()).throw(KeyError("third"))
        with tempfile.TemporaryDirectory() as td:
            project = _empty_project(td)
            n, results = dp._run_frontend_patches_detailed(project)
        assert len(results) == 16, "all 16 must still be attempted"
        assert [r.name for r in results] == _EXPECTED_ORDER
        failed_names = {r.name for r in results if not r.success}
        assert failed_names == {
            "_patch_disallowed_icon_packages",
            "_patch_pagination_component",
            "patch_ensure_auth_pages",
        }
        succeeded = [r for r in results if r.success]
        assert len(succeeded) == 13
    finally:
        for name, fn in originals.items():
            setattr(dp, name, fn)


def test_skipped_patchers_are_none_today_but_field_exists():
    # No call in this sequence has a gating condition today (confirmed via
    # docs/REPAIR_GRAPH.md's audit: no ordering/skip comments among the 14
    # calls) -- documents that fact as a running assertion, and locks in
    # that the field exists on every result for when one is added.
    with tempfile.TemporaryDirectory() as td:
        project = _empty_project(td)
        _n, results = _run_frontend_patches_detailed(project)
        assert all(r.skipped is False for r in results)
        assert all(hasattr(r, "skipped") for r in results)


def test_frontend_patch_result_is_a_plain_dataclass_with_expected_fields():
    r = FrontendPatchResult(name="x", success=True, count=1, duration_ms=0.5)
    assert r.skipped is False
    assert r.exception is None


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
