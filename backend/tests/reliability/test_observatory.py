"""
Verifies compute_observatory (app/memory/reliability_metrics.py) -- the
Observatory cockpit's single data source. Deliberately pure aggregation
over already-tested functions (compute_reliability_metrics,
compute_prevention_rate) plus regression_count and canary_history.json;
these tests focus on the NEW logic (trend delta, now-vs-historically
failure divergence, canary health derivation), not re-testing what's
already covered elsewhere.

Run directly: python tests/reliability/test_observatory.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.memory.reliability_metrics import compute_observatory


def _entry(succeeded, fix_count=0, dominant_errors=None, regression_count=0):
    return {
        "succeeded": succeeded, "fix_count": fix_count,
        "dominant_errors": dominant_errors or [],
        "regression_count": regression_count,
        "final_score": 90.0 if succeeded else 40.0,
    }


def test_empty_telemetry_is_safe():
    obs = compute_observatory([], [])
    assert obs["first_try_success_rate"] is None
    assert obs["first_try_trend"] is None
    assert obs["canary_health"] == "Unknown"
    assert obs["regression_alerts"] == 0


def test_trend_compares_current_window_to_previous_window():
    # Previous window (30 entries): all fail -> 0% first-try
    # Current window (30 entries): all succeed, 0 fixes -> 100% first-try
    prev = [_entry(False) for _ in range(30)]
    curr = [_entry(True, fix_count=0) for _ in range(30)]
    obs = compute_observatory(prev + curr, [], window=30)
    assert obs["first_try_success_rate"] == 100.0
    assert obs["first_try_trend"] == 100.0  # 100% - 0% improvement


def test_regression_alerts_sums_within_window_only():
    entries = [_entry(True, regression_count=1) for _ in range(5)]
    entries += [_entry(True, regression_count=3) for _ in range(5)]
    obs = compute_observatory(entries, [], window=5)  # only last 5 count
    assert obs["regression_alerts"] == 15  # 5 entries x 3 each


def test_top_failure_now_vs_historically_can_diverge():
    # All-time: JourneyCRUDFailure dominates (20 entries)
    # Recent window: AuthDrift is now the top failure (5 entries)
    historically = [_entry(False, dominant_errors=["[JourneyCRUDFailure] x"]) for _ in range(20)]
    now = [_entry(False, dominant_errors=["[AuthOwnershipDrift] y"]) for _ in range(5)]
    obs = compute_observatory(historically + now, [], window=5)
    assert obs["top_failure_now"] != obs["top_failure_historically"]


def test_canary_health_healthy_when_all_scores_good():
    runs = [{"label": "x", "timestamp": "2026-07-01T00:00:00", "results": [
        {"forge_score": 90, "crashed": False}, {"forge_score": 85, "crashed": False},
    ]}]
    obs = compute_observatory([], runs)
    assert obs["canary_health"] == "Healthy"


def test_canary_health_unhealthy_on_crash():
    runs = [{"label": "x", "timestamp": "2026-07-01T00:00:00", "results": [
        {"forge_score": 90, "crashed": False}, {"forge_score": 0, "crashed": True},
    ]}]
    obs = compute_observatory([], runs)
    assert obs["canary_health"] == "Unhealthy"


def test_canary_health_degraded_on_low_but_not_crashed_score():
    runs = [{"label": "x", "timestamp": "2026-07-01T00:00:00", "results": [
        {"forge_score": 60, "crashed": False}, {"forge_score": 85, "crashed": False},
    ]}]
    obs = compute_observatory([], runs)
    assert obs["canary_health"] == "Degraded"


def test_prevention_and_regression_use_the_same_window():
    entries = [_entry(True, regression_count=1) for _ in range(10)]
    obs = compute_observatory(entries, [], window=3)
    assert obs["window"] == 3
    assert obs["regression_alerts"] == 3


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
