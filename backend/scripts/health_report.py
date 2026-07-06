"""
ForgeAI Health Report — zero-cost summary of the latest canary run.

Reads backend/benchmark_results/canary_history.json (written by
run_canary.py) plus failure_memory/patterns.json for the top failure
classes. No generation calls — run this after every canary/benchmark to
see where the next bottleneck is before spending more credits.

Usage:
    python scripts/health_report.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_HISTORY_PATH = _BACKEND_ROOT / "benchmark_results" / "canary_history.json"
_PATTERNS_PATH = _BACKEND_ROOT / "failure_memory" / "patterns.json"

# dimension name (as ScoringEngine emits it) -> health-report row label
_DIMENSION_ROWS = [
    ("Compilation",     "Build Success"),
    ("Frontend Load",   "Frontend Build"),
    ("Runtime Startup", "Runtime"),
    ("Integration",     "CRUD"),
    ("Browser UX",      "Browser"),
]


def _pct(passed: int, total: int) -> float:
    return round(100.0 * passed / total, 1) if total else float("nan")


def _dim_lookup(dimensions: list[dict], name: str) -> dict | None:
    return next((d for d in dimensions if d.get("name") == name), None)


def main():
    if not _HISTORY_PATH.exists():
        print(f"No canary history at {_HISTORY_PATH} — run scripts/run_canary.py first.")
        return

    history = json.loads(_HISTORY_PATH.read_text(encoding="utf-8"))
    if not history.get("runs"):
        print("canary_history.json has no runs yet.")
        return

    run = history["runs"][-1]
    results = run["results"]
    label = run.get("label") or "unlabeled"

    print(f"\n{'='*60}\n  ForgeAI Health Report  ({label})\n{'='*60}")

    scores = [r.get("forge_score", 0.0) for r in results]
    overall = round(sum(scores) / len(scores), 1) if scores else 0.0
    print(f"\nOverall Success (avg forge score): {overall}%\n{'-'*60}")

    for dim_name, row_label in _DIMENSION_ROWS:
        passed = 0
        total = 0
        for r in results:
            dims = r.get("dimensions") or []
            d = _dim_lookup(dims, dim_name)
            if d is None or d.get("score") is None:  # N/A / didn't run
                continue
            total += 1
            if d.get("passed"):
                passed += 1
        if total == 0:
            print(f"{row_label:<20} N/A (no runs with this dimension recorded)")
        else:
            print(f"{row_label:<20} {_pct(passed, total)}%  ({passed}/{total} apps)")

    deployed = sum(1 for r in results if r.get("deployed"))
    print(f"{'Deployment':<20} {_pct(deployed, len(results))}%  ({deployed}/{len(results)} apps)")

    # Repair success: fraction of fix-loop attempts (across all apps in this
    # run) whose delta was positive. None/missing retry_history (older runs,
    # before this field existed, or a run that never entered the fix loop)
    # is skipped rather than counted as a failure.
    repair_attempts = 0
    repair_wins = 0
    for r in results:
        for attempt in (r.get("retry_history") or []):
            repair_attempts += 1
            if attempt.get("delta", 0) > 0:
                repair_wins += 1
    if repair_attempts:
        print(f"{'Repair Success':<20} {_pct(repair_wins, repair_attempts)}%  "
              f"({repair_wins}/{repair_attempts} fix attempts improved score)")
    else:
        print(f"{'Repair Success':<20} N/A (no fix attempts recorded)")

    # Confidence quality: average confidence.pct across apps that have one.
    # Requires the confidence-engine fix (cbb46fb) -- runs before that commit
    # will show N/A here since compute_from_context() always raised.
    conf_pcts = [r["confidence"]["pct"] for r in results
                 if r.get("confidence") and "pct" in r["confidence"]]
    if conf_pcts:
        print(f"{'Confidence Quality':<20} {round(sum(conf_pcts)/len(conf_pcts), 1)}%  "
              f"(avg over {len(conf_pcts)} apps)")
    else:
        print(f"{'Confidence Quality':<20} N/A (no confidence data in this run)")

    print(f"{'-'*60}\nTop Failure Classes (from failure_memory/patterns.json, all-time):")
    if _PATTERNS_PATH.exists():
        data = json.loads(_PATTERNS_PATH.read_text(encoding="utf-8"))
        pats = data.get("patterns", {})
        total_failures = sum(p["count"] for p in pats.values()) or 1
        top = sorted(pats.items(), key=lambda x: -x[1]["count"])[:3]
        for i, (name, p) in enumerate(top, 1):
            pct = 100 * p["count"] / total_failures
            print(f"  {i}. {name} — {p['count']} instances ({pct:.0f}% of all recorded failures)")
    else:
        print("  (no patterns.json found)")
    print()


if __name__ == "__main__":
    main()
