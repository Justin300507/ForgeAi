"""
Experiment 057: regression tests for the confirmed Exp053 regression
(commit f7d4dca) -- generate_project_v6's runtime-fix retry loop
(Stage 12) called patch_model_field_mismatches() and 4 sibling
database_patcher functions bare, relying on a local import that used to
live directly in this function's own body. Exp053 moved that import into
a separately-scoped helper (_run_initial_deterministic_patches), leaving
this loop -- ~40 lines later, but still the SAME function -- with those
5 names unbound. Confirmed via Exp056's canary baseline: NameError in
4/5 runs, and independently, every failing run's own "Runtime Fixes"
LLM-call count stuck at exactly 1 despite max_runtime_attempts=3 allowing
up to 4.

Exp057's fix: widen the ALREADY-correctly-scoped `from
app.services.database_patcher import patch_database_py` (the same
if-block, proven correct by its own working use later in the same
function) to include the 5 missing names -- no new import statement, no
duplicate of the helper's own separately-scoped import.

Rather than mock generate_project_v6's entire ~1000-line body (product
manager -> architect -> tech lead -> backend/frontend gen -> validation
-> reviews, all before Stage 12 even starts), this file follows the
precedent already set in tests/adr002/test_orchestrator_wiring.py: a
lightweight, targeted check against the LLM/network-dependent whole
function is impractical, so exercise the exact scope being fixed
directly. Two layers:

  1. Structural checks against the REAL, CURRENT source (via
     inspect.getsource, not a hand-copied reproduction that could drift).
  2. A functional harness that extracts the real Stage 12 block's source
     text, wraps it in a synthetic function, and executes it for real
     against a controlled, mocked runtime-fix scenario -- proving actual
     Python name resolution works, not just that the right substring
     appears in the file. The database_patcher functions are NOT mocked
     at the exec level -- they're monkeypatched on the real
     app.services.database_patcher module first, so the extracted code's
     own `from app.services.database_patcher import (...)` statement
     resolves them for real, exercising the identical import mechanism
     Exp053 broke and Exp057 restored.

Run directly: python tests/reliability/test_runtime_fix_loop_scope.py
"""
import inspect
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import app.services.v6_orchestrator as v6_orchestrator
import app.services.database_patcher as database_patcher
import app.services.runtime_fix_service as runtime_fix_service
from app.services.v6_orchestrator import generate_project_v6, _run_initial_deterministic_patches


# ── 1. Structural checks against the real, current source ────────────────────

_FIVE_NAMES = [
    "patch_model_field_mismatches",
    "patch_add_missing_model_columns",
    "patch_add_missing_schema_fields",
    "patch_missing_required_constructor_kwargs",
    "patch_filter_dict_unpack_constructor_kwargs",
]


def _stage12_source() -> str:
    src = inspect.getsource(generate_project_v6)
    # generate_project_v6 has multiple `if validation["passed"]:` checks
    # (reviews, polish, Stage 12) -- anchor the search to the Stage 12
    # marker comment first so we grab the right one, not an earlier one.
    stage_marker = src.index("# Stage 12: Runtime Validation")
    start = src.index('if validation["passed"]:', stage_marker)
    end = src.index('print(f"  Runtime validation error: {re_err}")', start)
    end += len('print(f"  Runtime validation error: {re_err}")')
    return src[start:end]


def test_stage12_import_now_covers_all_five_names():
    block = _stage12_source()
    import_start = block.index("from app.services.database_patcher import")
    import_end = block.index(")", import_start) + 1
    import_stmt = block[import_start:import_end]
    for name in _FIVE_NAMES + ["patch_database_py"]:
        assert name in import_stmt, f"{name} missing from the widened Stage-12 import"


def test_stage12_uses_the_widened_import_not_a_second_new_one():
    # Exp057's own rule: no duplicate import statement. There must be
    # exactly ONE `from app.services.database_patcher import` in the
    # Stage-12 block (the widened one) -- not one for patch_database_py
    # and a second separate one for the other 5 names.
    block = _stage12_source()
    count = block.count("from app.services.database_patcher import")
    assert count == 1, f"expected exactly one database_patcher import in Stage 12, found {count}"


def test_helper_extraction_remains_intact():
    # Exp053's own extraction (the thing this experiment must NOT undo)
    # is still present: _run_initial_deterministic_patches still exists,
    # still has its own separately-scoped import of the same 5 names
    # (needed for ITS OWN execution, independent of Stage 12's fix), and
    # generate_project_v6 still calls it for Stage 1.
    helper_src = inspect.getsource(_run_initial_deterministic_patches)
    for name in _FIVE_NAMES:
        assert name in helper_src, f"helper extraction lost {name}"
    assert "from app.services.database_patcher import" in helper_src

    gen_src = inspect.getsource(generate_project_v6)
    assert "_n_field_fixes = _run_initial_deterministic_patches(project_path)" in gen_src


def test_only_stage12_and_helper_import_these_names_not_a_third_site():
    # Confirms the fix stayed minimal -- no accidental third copy of this
    # import was introduced elsewhere in the file.
    full_src = inspect.getsource(v6_orchestrator)
    occurrences = full_src.count("patch_model_field_mismatches")
    # Helper's own call + helper's own import + Stage-12's import + Stage-12's
    # call + this test file's own docstring doesn't count (different file).
    # In v6_orchestrator.py itself: helper def uses it twice (import + call),
    # Stage 12 uses it twice (import + call) = 4.
    assert occurrences == 4, (
        f"expected exactly 4 occurrences of patch_model_field_mismatches in "
        f"v6_orchestrator.py (helper import+call, Stage-12 import+call), found {occurrences} "
        f"-- a third copy may have been introduced"
    )


# ── 2. Functional harness: execute the real Stage 12 block for real ──────────

def _build_stage12_harness():
    """
    Extracts generate_project_v6's real Stage 12 source, wraps it in a
    synthetic function taking the loop's free variables as parameters,
    and compiles it. Returns the callable.
    """
    block = _stage12_source()
    dedented = "\n".join(line[4:] if line.startswith("    ") else line for line in block.splitlines())
    harness_src = (
        "def _stage12_harness(validation, project_path, architecture, provider, _llm, "
        "validate_runtime, write_fix, run_deterministic_patches, _sanitize_path, os):\n"
        "    runtime_result = None\n"
    )
    for line in dedented.splitlines():
        harness_src += ("    " + line if line.strip() else line) + "\n"
    harness_src += "    return runtime_result, _llm\n"
    namespace = {}
    exec(compile(harness_src, "<stage12_harness>", "exec"), namespace)
    return namespace["_stage12_harness"]


def _make_validate_runtime(fail_count: int, always_fail: bool = False, vary_signature: bool = True):
    """
    fail_count: number of leading calls that report failure before success
    (ignored if always_fail=True). vary_signature=True makes each failure's
    journey detail different so the stagnation guard doesn't trip early --
    vary_signature=False deliberately returns an IDENTICAL signature every
    time, to exercise the stagnation guard itself.
    """
    calls = {"n": 0}

    def _validate_runtime(project_path, architecture=None):
        n = calls["n"]
        calls["n"] += 1
        if not always_fail and n >= fail_count:
            return {"success": True, "journey": {"steps": []}}
        detail = f"attempt-{n}" if vary_signature else "same-failure-every-time"
        return {
            "success": False,
            "journey": {"steps": [{"name": "Register", "passed": False, "detail": detail}]},
        }

    return _validate_runtime, calls


def _run_harness(validate_runtime_fn, tmp_project):
    harness = _build_stage12_harness()
    fix_calls = {"generate_runtime_fix": 0, "write_fix": 0, "run_deterministic_patches": 0}

    def _generate_runtime_fix(runtime_result, project_path, provider):
        fix_calls["generate_runtime_fix"] += 1
        return {"path": "app/routes/x.py", "content": "# fix"}

    def _write_fix(project_path, rt_fix):
        fix_calls["write_fix"] += 1

    def _run_deterministic_patches(project_path):
        fix_calls["run_deterministic_patches"] += 1

    patcher_calls = {name: 0 for name in _FIVE_NAMES + ["patch_database_py"]}

    def _make_counting_stub(name):
        def _stub(project_path):
            patcher_calls[name] += 1
            return 0
        return _stub

    with patch.object(runtime_fix_service, "generate_runtime_fix", _generate_runtime_fix), \
         patch.object(database_patcher, "patch_database_py", _make_counting_stub("patch_database_py")), \
         patch.object(database_patcher, "patch_model_field_mismatches",
                       _make_counting_stub("patch_model_field_mismatches")), \
         patch.object(database_patcher, "patch_add_missing_model_columns",
                       _make_counting_stub("patch_add_missing_model_columns")), \
         patch.object(database_patcher, "patch_add_missing_schema_fields",
                       _make_counting_stub("patch_add_missing_schema_fields")), \
         patch.object(database_patcher, "patch_missing_required_constructor_kwargs",
                       _make_counting_stub("patch_missing_required_constructor_kwargs")), \
         patch.object(database_patcher, "patch_filter_dict_unpack_constructor_kwargs",
                       _make_counting_stub("patch_filter_dict_unpack_constructor_kwargs")):
        runtime_result, _llm = harness(
            validation={"passed": True},
            project_path=tmp_project,
            architecture={},
            provider="auto",
            _llm={"runtime_fixes": 0},
            validate_runtime=validate_runtime_fn,
            write_fix=_write_fix,
            run_deterministic_patches=_run_deterministic_patches,
            _sanitize_path=lambda p: p,
            os=os,
        )
    return runtime_result, _llm, fix_calls, patcher_calls


def test_no_name_error_and_all_iterations_execute_when_always_failing():
    # CONFIRMED pre-fix behavior (Exp056): this exact scenario crashed
    # with NameError on the FIRST fix-and-recheck cycle, leaving
    # validate_runtime called only once and every cleanup patcher at 0
    # calls. Post-fix: all 4 intended validate_runtime calls happen
    # (max_runtime_attempts=3 -> range(4)), and the cleanup patchers run
    # on each of the 3 non-final iterations.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        validate_fn, calls = _make_validate_runtime(fail_count=999, always_fail=True, vary_signature=True)
        runtime_result, _llm, fix_calls, patcher_calls = _run_harness(validate_fn, tmp)

        assert calls["n"] == 4, f"expected all 4 validate_runtime calls (range(max_runtime_attempts+1)), got {calls['n']}"
        assert runtime_result["success"] is False
        # Fix-and-cleanup runs on r_attempt 0,1,2 (NOT on the final r_attempt==max_runtime_attempts,
        # which breaks before generating a fix -- see the loop's own `if r_attempt == max_runtime_attempts: break`).
        assert fix_calls["generate_runtime_fix"] == 3
        assert fix_calls["write_fix"] == 3
        assert fix_calls["run_deterministic_patches"] == 3
        for name in _FIVE_NAMES + ["patch_database_py"]:
            # patch_database_py is called once up front (outside the loop) PLUS once per
            # loop iteration with a fix -- 1 + 3 = 4. The other 5 are loop-only -- 3.
            expected = 4 if name == "patch_database_py" else 3
            assert patcher_calls[name] == expected, f"{name}: expected {expected} calls, got {patcher_calls[name]}"
        assert _llm["runtime_fixes"] == 3


def test_no_name_error_with_success_on_third_attempt():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        validate_fn, calls = _make_validate_runtime(fail_count=2, vary_signature=True)
        runtime_result, _llm, fix_calls, patcher_calls = _run_harness(validate_fn, tmp)

        assert runtime_result["success"] is True
        assert calls["n"] == 3, "loop must break immediately once validate_runtime reports success"
        assert fix_calls["generate_runtime_fix"] == 2, "fix only generated on the 2 failing attempts, not after success"
        assert patcher_calls["patch_model_field_mismatches"] == 2


def test_stagnation_guard_still_stops_early_unchanged():
    # This logic was NOT touched by Exp057 -- proving it survives exactly
    # as before confirms the fix didn't change retry termination behavior.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        validate_fn, calls = _make_validate_runtime(fail_count=999, always_fail=True, vary_signature=False)
        runtime_result, _llm, fix_calls, patcher_calls = _run_harness(validate_fn, tmp)

        # r_attempt 0: no prior signature -> proceeds, generates 1 fix.
        # r_attempt 1: identical signature -> stagnation guard breaks BEFORE generating a 2nd fix.
        assert calls["n"] == 2, f"expected exactly 2 validate_runtime calls before stagnation guard triggers, got {calls['n']}"
        assert fix_calls["generate_runtime_fix"] == 1
        assert runtime_result["success"] is False


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
