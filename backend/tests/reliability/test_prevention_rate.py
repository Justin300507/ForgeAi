"""
Verifies the Deterministic Prevention Rate KPI (app/memory/reliability_metrics.py):
compute_prevention_rate / render_prevention_dashboard, and that
run_deterministic_patches' full return-value dict is JSON-serializable
(it flows straight into generation_log.jsonl via GenerationRecord).

Run directly: python tests/reliability/test_prevention_rate.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.memory.reliability_metrics import (
    compute_prevention_rate, render_prevention_dashboard,
    DETERMINISTIC_PREVENTION_CATEGORIES,
)


def test_empty_and_missing_prevention_counts_is_safe():
    m = compute_prevention_rate([{"succeeded": True}, {"succeeded": False}])
    assert m["total_preventions"] == 0
    assert m["generations_with_prevention"] == 0
    assert m["by_category"] == {}


def test_aggregates_across_generations_and_categorizes():
    entries = [
        {"prevention_counts": {"stage.import_closure": 3, "stage.symbol_closure": 2}},
        {"prevention_counts": {"stage.import_closure": 1, "_patch_missing_create_update_fields": 4}},
    ]
    m = compute_prevention_rate(entries)
    assert m["total_preventions"] == 10
    assert m["generations_with_prevention"] == 2
    assert m["by_category"]["Import validation"] == 4
    assert m["by_category"]["Symbol validation"] == 2
    assert m["by_category"]["Schema validator"] == 4
    assert m["raw_counts"]["stage.import_closure"] == 4


def test_zero_count_entries_do_not_count_as_prevention():
    m = compute_prevention_rate([{"prevention_counts": {"_patch_router_names": 0}}])
    assert m["total_preventions"] == 0
    assert m["generations_with_prevention"] == 0


def test_unmapped_key_falls_into_other_not_dropped():
    m = compute_prevention_rate([{"prevention_counts": {"brand_new_patcher_nobody_categorized_yet": 7}}])
    assert m["by_category"]["Other"] == 7
    assert m["total_preventions"] == 7


def test_window_limits_to_most_recent():
    entries = [{"prevention_counts": {"stage.compile": 1}} for _ in range(5)]
    m = compute_prevention_rate(entries, window=2)
    assert m["window"] == 2
    assert m["total_preventions"] == 2


def test_render_handles_empty_gracefully():
    out = render_prevention_dashboard(compute_prevention_rate([]))
    assert "DETERMINISTIC PREVENTION RATE" in out
    assert "pre-dates this metric" in out


def test_render_shows_categories_sorted_by_count():
    entries = [{"prevention_counts": {
        "stage.import_closure": 1, "_patch_missing_create_update_fields": 10,
    }}]
    out = render_prevention_dashboard(compute_prevention_rate(entries))
    schema_pos = out.index("Schema validator")
    import_pos = out.index("Import validation")
    assert schema_pos < import_pos  # higher count (10) listed before lower (1)


def test_run_deterministic_patches_return_is_json_serializable():
    """Confirms the actual patcher entrypoint (not just the metrics layer)
    returns something generation_log.jsonl can round-trip -- every value
    must be a plain int, since this dict is written into GenerationRecord
    and appended as a JSON line."""
    import tempfile
    import shutil
    from pathlib import Path
    from app.services.deterministic_patcher import run_deterministic_patches

    root = Path(tempfile.mkdtemp(prefix="prevention_smoke_"))
    (root / "app").mkdir(parents=True)
    try:
        counts = run_deterministic_patches(str(root))
        assert isinstance(counts, dict)
        assert all(isinstance(v, int) for v in counts.values()), (
            f"non-int values: {[(k, type(v)) for k, v in counts.items() if not isinstance(v, int)]}"
        )
        json.dumps(counts)  # must not raise
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_every_category_value_is_a_known_short_label():
    """Guards against a typo silently creating a near-duplicate category
    (e.g. 'Schema Validator' vs 'Schema validator')."""
    labels = set(DETERMINISTIC_PREVENTION_CATEGORIES.values())
    expected = {
        "Import validation", "Symbol validation", "Schema validator",
        "Entity validator", "Syntax validator", "Pydantic patcher",
        "Auth patcher", "Frontend patcher",
    }
    assert labels <= expected, f"unexpected category label(s): {labels - expected}"


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
