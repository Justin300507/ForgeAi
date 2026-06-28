"""
ForgeBench-20 Version History

Tracks every benchmark run as a single entry in benchmark_results/history.json.
Produces the version-trend table and the "capability graph" over time.

Each entry:
  version, run_id, label, date,
  total, passed, weighted_score, weighted_pass_rate,
  avg_forge_score, total_cost_usd, cost_per_success,
  compile_rate, runtime_rate, crud_rate, avg_time_s

Usage (called automatically by BenchmarkReporter.finalize()):
    from app.benchmark.history import record_run, print_trend
    record_run(report)
    print_trend()
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.benchmark.metrics import BenchmarkReport

_HISTORY_PATH = Path(__file__).parent.parent.parent / "benchmark_results" / "history.json"


def record_run(report: "BenchmarkReport") -> None:
    """Append this run to the history file."""
    _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)

    history = _load()

    entry = {
        "version":            report.version,
        "run_id":             report.run_id,
        "label":              report.label,
        "date":               (report.completed_at or "")[:10],
        "total":              report.total_prompts,
        "passed":             sum(
            1 for r in report.results
            if r.get("compile_success") and r.get("runtime_success") and not r.get("crashed")
        ),
        "weighted_score":     report.weighted_score,
        "weighted_pass_rate": report.weighted_pass_rate,
        "avg_forge_score":    report.avg_forge_score,
        "compile_rate":       report.compile_success_rate,
        "runtime_rate":       report.runtime_success_rate,
        "crud_rate":          report.crud_success_rate,
        "first_pass_rate":    report.first_pass_compile_rate,
        "intervention_rate":  report.intervention_rate,
        "total_cost_usd":     report.total_cost_usd,
        "cost_per_success":   report.cost_per_success,
        "avg_time_s":         report.avg_generation_s,
        "provider":           report.provider,
    }

    # Avoid duplicate run_ids
    history = [h for h in history if h.get("run_id") != report.run_id]
    history.append(entry)

    _HISTORY_PATH.write_text(json.dumps(history, indent=2), encoding="utf-8")


def _load() -> list[dict]:
    if not _HISTORY_PATH.exists():
        return []
    try:
        return json.loads(_HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def print_trend(last_n: int = 10) -> None:
    """
    Print the version trend table — the "capability graph."

    ForgeBench-20 Version History (last 10 runs)
    ──────────────────────────────────────────────────────────────────────
    VERSION   DATE        SCORE   W-SCORE  PASS   COMPILE  RUNTIME  $/OK
    v15       2026-06-20   82.1    79.3    85%     90%      82%     $0.07
    v16       2026-06-22   87.4    85.1    90%     94%      88%     $0.06
    v17       2026-06-25   91.2    89.7    94%     97%      93%     $0.05
    ...
    """
    history = _load()
    if not history:
        print("No benchmark history yet. Run ForgeBench-20 first.")
        return

    recent = history[-last_n:]

    print(f"\n{'═' * 78}")
    print(f"  ForgeBench-20 History  (last {len(recent)} runs)")
    print(f"{'═' * 78}")
    print(f"  {'VERSION':<10} {'DATE':<12} {'SCORE':>6} {'W-SCORE':>8} {'PASS':>6} "
          f"{'COMPILE':>8} {'RUNTIME':>8} {'$/OK':>7}  LABEL")
    print(f"  {'-' * 76}")

    for h in recent:
        version   = str(h.get("version", "?"))[:10]
        date      = str(h.get("date", "?"))[:10]
        score     = h.get("avg_forge_score", 0)
        w_score   = h.get("weighted_score", 0)
        passed    = h.get("passed", 0)
        total     = h.get("total", 0)
        pass_pct  = f"{passed}/{total}"
        compile_r = h.get("compile_rate", 0)
        runtime_r = h.get("runtime_rate", 0)
        cost_ok   = h.get("cost_per_success", 0)
        label     = h.get("label", "")[:20]

        print(
            f"  {version:<10} {date:<12} {score:>6.1f} {w_score:>8.1f} {pass_pct:>6} "
            f"{compile_r:>7.0%} {runtime_r:>7.0%} ${cost_ok:>5.3f}  {label}"
        )

    print(f"{'═' * 78}")

    # Show delta from first to last if ≥2 entries
    if len(recent) >= 2:
        first, last = recent[0], recent[-1]
        delta_w   = last.get("weighted_score", 0) - first.get("weighted_score", 0)
        delta_c   = (last.get("cost_per_success", 0) - first.get("cost_per_success", 0))
        trend = "↑" if delta_w > 0 else ("↓" if delta_w < 0 else "→")
        print(f"  Trend: weighted score {trend} {delta_w:+.1f}  |  cost/success Δ ${delta_c:+.3f}")
        print(f"{'═' * 78}")
    print()


def get_history() -> list[dict]:
    """Return all history entries sorted oldest-first."""
    return sorted(_load(), key=lambda h: h.get("date", ""))
