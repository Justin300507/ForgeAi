"""
Experiment 053 (Repair Pipeline Consolidation), Task 5: repair_project()
duplicated the main generation flow's "Stage 1" deterministic-patch
sequence (Experiment 051's finding). Confirmed near-byte-identical by
direct comparison, extracted into
v6_orchestrator._run_initial_deterministic_patches, called from both
generate_project_v6 and repair_project().

Stages 2 (architecture repair) and 3 (runtime fix loop) were investigated
too and found to have REAL behavioral divergence -- NOT extracted, see
docs/REPAIR_ARCHITECTURE.md. These tests only cover Stage 1, the one
piece actually consolidated.

Run directly: python tests/reliability/test_repair_stage1_consolidation.py
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.v6_orchestrator import _run_initial_deterministic_patches


def test_calls_run_deterministic_patches_and_all_six_database_patchers_in_order():
    calls = []

    def track(name):
        def fn(*args, **kwargs):
            calls.append(name)
            return 0
        return fn

    with mock.patch("app.services.v6_orchestrator.run_deterministic_patches", side_effect=track("run_deterministic_patches")), \
         mock.patch("app.services.database_patcher.patch_database_py", side_effect=track("patch_database_py")), \
         mock.patch("app.services.database_patcher.patch_model_field_mismatches", side_effect=track("patch_model_field_mismatches")), \
         mock.patch("app.services.database_patcher.patch_add_missing_model_columns", side_effect=track("patch_add_missing_model_columns")), \
         mock.patch("app.services.database_patcher.patch_add_missing_schema_fields", side_effect=track("patch_add_missing_schema_fields")), \
         mock.patch("app.services.database_patcher.patch_missing_required_constructor_kwargs", side_effect=track("patch_missing_required_constructor_kwargs")), \
         mock.patch("app.services.database_patcher.patch_filter_dict_unpack_constructor_kwargs", side_effect=track("patch_filter_dict_unpack_constructor_kwargs")), \
         mock.patch("app.services.v6_orchestrator.ensure_app_jsx", return_value=False):
        _run_initial_deterministic_patches("/fake/project")

    # Exact order matters -- this is the same sequence both call sites
    # relied on before the extraction; a reorder here would be a real
    # behavior change even though every individual call still happens.
    assert calls == [
        "run_deterministic_patches",
        "patch_database_py",
        "patch_model_field_mismatches",
        "patch_add_missing_model_columns",
        "patch_add_missing_schema_fields",
        "patch_missing_required_constructor_kwargs",
        "patch_filter_dict_unpack_constructor_kwargs",
    ]


def test_returns_field_mismatch_fix_count():
    with mock.patch("app.services.v6_orchestrator.run_deterministic_patches"), \
         mock.patch("app.services.database_patcher.patch_database_py"), \
         mock.patch("app.services.database_patcher.patch_model_field_mismatches", return_value=4), \
         mock.patch("app.services.database_patcher.patch_add_missing_model_columns"), \
         mock.patch("app.services.database_patcher.patch_add_missing_schema_fields"), \
         mock.patch("app.services.database_patcher.patch_missing_required_constructor_kwargs"), \
         mock.patch("app.services.database_patcher.patch_filter_dict_unpack_constructor_kwargs"), \
         mock.patch("app.services.v6_orchestrator.ensure_app_jsx", return_value=False):
        result = _run_initial_deterministic_patches("/fake/project")
    assert result == 4


def test_returns_zero_not_none_when_patcher_returns_none():
    with mock.patch("app.services.v6_orchestrator.run_deterministic_patches"), \
         mock.patch("app.services.database_patcher.patch_database_py"), \
         mock.patch("app.services.database_patcher.patch_model_field_mismatches", return_value=None), \
         mock.patch("app.services.database_patcher.patch_add_missing_model_columns"), \
         mock.patch("app.services.database_patcher.patch_add_missing_schema_fields"), \
         mock.patch("app.services.database_patcher.patch_missing_required_constructor_kwargs"), \
         mock.patch("app.services.database_patcher.patch_filter_dict_unpack_constructor_kwargs"), \
         mock.patch("app.services.v6_orchestrator.ensure_app_jsx", return_value=False):
        result = _run_initial_deterministic_patches("/fake/project")
    assert result == 0


def test_scaffolds_app_jsx_when_missing():
    with mock.patch("app.services.v6_orchestrator.run_deterministic_patches"), \
         mock.patch("app.services.database_patcher.patch_database_py"), \
         mock.patch("app.services.database_patcher.patch_model_field_mismatches", return_value=0), \
         mock.patch("app.services.database_patcher.patch_add_missing_model_columns"), \
         mock.patch("app.services.database_patcher.patch_add_missing_schema_fields"), \
         mock.patch("app.services.database_patcher.patch_missing_required_constructor_kwargs"), \
         mock.patch("app.services.database_patcher.patch_filter_dict_unpack_constructor_kwargs"), \
         mock.patch("app.services.v6_orchestrator.ensure_app_jsx", return_value=True) as m_scaffold:
        _run_initial_deterministic_patches("/fake/project")
    m_scaffold.assert_called_once_with("/fake/project")


def test_both_call_sites_in_source_now_use_the_shared_helper():
    # Structural guard: confirms the consolidation actually replaced BOTH
    # call sites' inline duplication, not just added a new unused helper.
    import inspect
    from app.services import v6_orchestrator
    src = inspect.getsource(v6_orchestrator)
    assert src.count("_run_initial_deterministic_patches(project_path)") == 2, (
        "expected exactly 2 call sites (generate_project_v6 + repair_project) "
        "to use the shared helper -- if this count changed, either a call "
        "site regressed back to inline duplication, or a new one appeared "
        "that should also be reviewed"
    )


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
