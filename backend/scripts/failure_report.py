"""
Zero-cost failure/variance report from existing telemetry — no generation
calls, just reads backend/failure_memory/patterns.json and
project_history.jsonl. Run this instead of a canary when the question is
"what's our biggest recurring problem right now," not "does this app work."

Usage:
    python scripts/failure_report.py
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
FAILURE_DIR = _BACKEND_ROOT / "failure_memory"


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def print_failure_taxonomy():
    path = FAILURE_DIR / "patterns.json"
    if not path.exists():
        print("No patterns.json found.")
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    pats = data.get("patterns", {})
    total = sum(p["count"] for p in pats.values()) or 1
    rows = sorted(pats.items(), key=lambda x: -x[1]["count"])

    import sys
    sys.path.insert(0, str(_BACKEND_ROOT))
    from app.memory.failure_memory import stage_of

    print(f"\n{'='*70}\n  FAILURE TAXONOMY  ({total} recorded instances)\n{'='*70}")
    for name, p in rows:
        pct = 100 * p["count"] / total
        stage = p.get("stage") or stage_of(name)
        print(f"  [{stage:11s}] {name:30s} {p['count']:4d}  {pct:5.1f}%   last_seen={p['last_seen'][:10]}")


def print_variance_report():
    rows = _load_jsonl(FAILURE_DIR / "project_history.jsonl")
    if not rows:
        print("No project_history.jsonl found.")
        return

    scores = [r["forge_score"] for r in rows if "forge_score" in r]
    print(f"\n{'='*70}\n  VARIANCE REPORT  (N={len(rows)} generations)\n{'='*70}")
    print(f"  mean={statistics.mean(scores):.1f}  median={statistics.median(scores):.1f}  "
          f"stdev={statistics.pstdev(scores):.1f}  min={min(scores):.1f}  max={max(scores):.1f}")

    buckets = {">=80": 0, "60-79": 0, "40-59": 0, "<40": 0}
    for s in scores:
        if s >= 80:
            buckets[">=80"] += 1
        elif s >= 60:
            buckets["60-79"] += 1
        elif s >= 40:
            buckets["40-59"] += 1
        else:
            buckets["<40"] += 1
    print(f"  score buckets: {buckets}")

    for key in ("validation_passed", "runtime_passed", "crud_passed",
                "frontend_built", "deployment_success"):
        vals = [r.get(key) for r in rows if key in r]
        if not vals:
            continue
        true_n = sum(1 for v in vals if v is True)
        print(f"  {key:20s} {true_n}/{len(vals)} passed  ({100*true_n/len(vals):.0f}%)")

    print("\n  Worst 10 runs:")
    for r in sorted(rows, key=lambda r: r.get("forge_score", 0))[:10]:
        errs = r.get("static_errors") or []
        first_err = errs[0] if errs else "(no static errors logged)"
        print(f"    {r.get('forge_score', 0):5.1f}  {r.get('idea', '')[:45]:45s}  {first_err}")


def print_reliability_dashboard():
    """The V20 reliability dashboard — the numbers every experiment must
    move. Computation lives in app/memory/reliability_metrics.py (pure,
    unit-tested); this just loads telemetry and renders."""
    import sys
    sys.path.insert(0, str(_BACKEND_ROOT))
    from app.memory.reliability_metrics import (
        compute_reliability_metrics, render_dashboard,
        compute_prevention_rate, render_prevention_dashboard,
    )

    gen_entries = _load_jsonl(FAILURE_DIR / "generation_log.jsonl")
    canary_path = _BACKEND_ROOT / "benchmark_results" / "canary_history.json"
    canary_runs = []
    if canary_path.exists():
        try:
            canary_runs = json.loads(canary_path.read_text(encoding="utf-8")).get("runs", [])
        except Exception:
            pass
    print(render_dashboard(compute_reliability_metrics(gen_entries, canary_runs)))
    print()
    print(render_prevention_dashboard(compute_prevention_rate(gen_entries)))


if __name__ == "__main__":
    print_reliability_dashboard()
    print_failure_taxonomy()
    print_variance_report()
    print()
