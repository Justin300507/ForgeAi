"""
Verifies compute_reliability_timeline / compute_experiment_attribution /
confidence_from_evidence (app/memory/reliability_metrics.py) -- the
"data quality, not features" additions requested after Observatory
shipped: a per-milestone timeline and a before/after delta per labeled
canary transition, both confidence-labeled by evidence size rather than
delta magnitude (a big swing can mean a real fix or a provider-quota
confound just as easily -- this project's own experiment log documents
both).

Run directly: python tests/reliability/test_experiment_attribution.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.memory.reliability_metrics import (
    confidence_from_evidence, compute_reliability_timeline, compute_experiment_attribution,
)


def _run(label, timestamp, scores, crashed_count=0):
    results = [{"forge_score": s, "crashed": False} for s in scores]
    results += [{"forge_score": 0, "crashed": True} for _ in range(crashed_count)]
    return {"label": label, "timestamp": timestamp, "results": results}


def test_confidence_thresholds():
    assert confidence_from_evidence(0) == "Low"
    assert confidence_from_evidence(9) == "Low"
    assert confidence_from_evidence(10) == "Medium"
    assert confidence_from_evidence(29) == "Medium"
    assert confidence_from_evidence(30) == "High"
    assert confidence_from_evidence(1000) == "High"


def test_timeline_skips_runs_with_only_crashes():
    runs = [
        _run("all-crashed", "2026-07-01T00:00:00", [], crashed_count=3),
        _run("has-data", "2026-07-02T00:00:00", [80, 90]),
    ]
    timeline = compute_reliability_timeline(runs)
    assert len(timeline) == 1
    assert timeline[0]["label"] == "has-data"


def test_timeline_averages_only_non_crashed_scores():
    runs = [_run("mixed", "2026-07-01T00:00:00", [80, 90], crashed_count=1)]
    timeline = compute_reliability_timeline(runs)
    assert timeline[0]["avg_score"] == 85.0
    assert timeline[0]["n"] == 2  # crashed result excluded from both avg and n


def test_attribution_computes_consecutive_deltas():
    runs = [
        _run("m0", "2026-07-01T00:00:00", [40, 50, 60]),   # avg 50
        _run("m1", "2026-07-02T00:00:00", [60, 70, 80]),   # avg 70
        _run("m2", "2026-07-03T00:00:00", [50, 60, 70]),   # avg 60
    ]
    attribution = compute_experiment_attribution(runs)
    assert len(attribution) == 2
    # newest first
    assert attribution[0]["label"] == "m2"
    assert attribution[0]["before"] == 70.0
    assert attribution[0]["after"] == 60.0
    assert attribution[0]["delta"] == -10.0
    assert attribution[0]["direction"] == "regressed"
    assert attribution[1]["label"] == "m1"
    assert attribution[1]["delta"] == 20.0
    assert attribution[1]["direction"] == "improved"


def test_attribution_flat_delta_is_neither_improved_nor_regressed():
    runs = [
        _run("m0", "2026-07-01T00:00:00", [70]),
        _run("m1", "2026-07-02T00:00:00", [70]),
    ]
    attribution = compute_experiment_attribution(runs)
    assert attribution[0]["direction"] == "flat"
    assert attribution[0]["delta"] == 0.0


def test_attribution_confidence_is_evidence_based_not_delta_based():
    """A huge delta with only 3 apps of evidence is still Low confidence --
    confidence must not scale with |delta|."""
    runs = [
        _run("m0", "2026-07-01T00:00:00", [10, 10, 10]),
        _run("m1", "2026-07-02T00:00:00", [99, 99, 99]),  # +89 delta, still n=3
    ]
    attribution = compute_experiment_attribution(runs)
    assert attribution[0]["delta"] == 89.0
    assert attribution[0]["confidence"] == "Low"


def test_attribution_limit_returns_most_recent_n():
    runs = [_run(f"m{i}", f"2026-07-{i+1:02d}T00:00:00", [50 + i]) for i in range(10)]
    attribution = compute_experiment_attribution(runs, limit=3)
    assert len(attribution) == 3
    assert attribution[0]["label"] == "m9"  # most recent first


def test_empty_canary_runs_produce_empty_results():
    assert compute_reliability_timeline([]) == []
    assert compute_experiment_attribution([]) == []


def test_single_run_produces_no_attribution():
    runs = [_run("only-one", "2026-07-01T00:00:00", [80])]
    assert compute_experiment_attribution(runs) == []


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
