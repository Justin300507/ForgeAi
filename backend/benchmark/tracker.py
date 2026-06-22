"""
Score tracker for the ForgeAI V3 benchmark suite.

Stores runs in benchmark/results.json. Each run records per-app scores,
pass rates, and metadata so regressions are detectable across code changes.
"""
import json
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path

RESULTS_FILE = Path(__file__).parent / "results.json"


def _load() -> dict:
    if RESULTS_FILE.exists():
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"runs": []}


def _save(data: dict) -> None:
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def record_run(
    results: list[dict],
    provider: str = "cerebras",
    label: str = "",
    version: str = "v3",
) -> dict:
    """
    Persist a completed benchmark run.

    `results` is a list of per-app dicts with keys:
        idea, score, grade, validation_passed, runtime_passed,
        frontend_build_passed, error (optional)
    """
    scores = [r["score"] for r in results]
    passed = [r for r in results if r["score"] == 100]

    summary = {
        "count": len(scores),
        "mean_score": round(statistics.mean(scores), 1) if scores else 0,
        "median_score": round(statistics.median(scores), 1) if scores else 0,
        "min_score": min(scores) if scores else 0,
        "max_score": max(scores) if scores else 0,
        "perfect_count": len(passed),
        "perfect_rate": round(len(passed) / len(scores) * 100, 1) if scores else 0,
        "validation_pass_rate": round(
            sum(1 for r in results if r.get("validation_passed")) / len(results) * 100, 1
        ) if results else 0,
        "runtime_pass_rate": round(
            sum(1 for r in results if r.get("runtime_passed")) / len(results) * 100, 1
        ) if results else 0,
        "frontend_build_pass_rate": round(
            sum(1 for r in results if r.get("frontend_build_passed")) / len(results) * 100, 1
        ) if results else 0,
    }

    run = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": version,
        "provider": provider,
        "label": label,
        "summary": summary,
        "results": results,
    }

    data = _load()
    data["runs"].append(run)
    _save(data)
    print(f"\nRun recorded → {RESULTS_FILE}")
    return run


def compare_to_baseline(run: dict) -> dict | None:
    """
    Compare `run` against the previous run (baseline).
    Returns a regression report, or None if no baseline exists.
    """
    data = _load()
    runs = data.get("runs", [])
    if len(runs) < 2:
        return None

    baseline = runs[-2]["summary"]
    current = run["summary"]

    regressions = []
    improvements = []

    for key in ("mean_score", "perfect_rate", "validation_pass_rate", "runtime_pass_rate"):
        delta = current[key] - baseline[key]
        if delta < -2:
            regressions.append(f"{key}: {baseline[key]} → {current[key]} (↓{abs(delta):.1f})")
        elif delta > 2:
            improvements.append(f"{key}: {baseline[key]} → {current[key]} (↑{delta:.1f})")

    return {
        "regressions": regressions,
        "improvements": improvements,
        "baseline_timestamp": runs[-2]["timestamp"],
    }


def print_summary(run: dict) -> None:
    s = run["summary"]
    print("\n" + "=" * 60)
    print(f"BENCHMARK SUMMARY  [{run['timestamp'][:10]}]  {run.get('label', '')}")
    print("=" * 60)
    print(f"  Apps tested      : {s['count']}")
    print(f"  Mean score       : {s['mean_score']}/100")
    print(f"  Median score     : {s['median_score']}/100")
    print(f"  Perfect (100/100): {s['perfect_count']}/{s['count']}  ({s['perfect_rate']}%)")
    print(f"  Validation pass  : {s['validation_pass_rate']}%")
    print(f"  Runtime pass     : {s['runtime_pass_rate']}%")
    print(f"  Frontend build   : {s['frontend_build_pass_rate']}%")


def print_regression_report(report: dict) -> None:
    if not report:
        print("\n(No baseline to compare against)")
        return
    print(f"\nCompared to baseline from {report['baseline_timestamp'][:10]}:")
    if report["regressions"]:
        print("  REGRESSIONS:")
        for r in report["regressions"]:
            print(f"    ✗ {r}")
    if report["improvements"]:
        print("  IMPROVEMENTS:")
        for i in report["improvements"]:
            print(f"    ✓ {i}")
    if not report["regressions"] and not report["improvements"]:
        print("  No significant changes.")
