"""
ForgeBench Failure Heatmap + Stage Timing + Repair Efficiency

Reads a run's results.jsonl and produces three views:

1. Failure Heatmap     — per-app success rate across runs
2. Stage Timing        — avg seconds per pipeline stage (shows the bottleneck)
3. Repair Efficiency   — avg fixes, files changed, cost of repairs vs. generation

Usage:
    python -m app.benchmark.heatmap                        # latest run
    python -m app.benchmark.heatmap --run-id <run-id>      # specific run
    python -m app.benchmark.heatmap --all-runs             # aggregate across all runs
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

RESULTS_DIR = Path(__file__).parent.parent.parent / "benchmark_results"


def _load_results(run_id: str | None = None) -> list[dict]:
    if run_id:
        path = RESULTS_DIR / run_id / "results.jsonl"
        if not path.exists():
            print(f"No results at {path}")
            return []
        return _read_jsonl(path)

    # Latest run
    runs = sorted(
        [d for d in RESULTS_DIR.iterdir() if (d / "results.jsonl").exists()],
        key=lambda d: d.name,
        reverse=True,
    )
    if not runs:
        print("No benchmark runs found. Run ForgeBench-20 first.")
        return []
    path = runs[0] / "results.jsonl"
    print(f"Using latest run: {runs[0].name}")
    return _read_jsonl(path)


def _load_all_results() -> list[dict]:
    results = []
    for run_dir in RESULTS_DIR.iterdir():
        path = run_dir / "results.jsonl"
        if path.exists():
            results.extend(_read_jsonl(path))
    return results


def _read_jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def print_heatmap(results: list[dict]) -> None:
    """
    Per-app failure heatmap. Shows compile/runtime/CRUD pass rates.
    Groups by prompt name, sorted by weighted composite (failures worst-first).
    """
    by_app: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_app[r.get("name", "?")].append(r)

    print(f"\n{'═' * 74}")
    print(f"  Failure Heatmap  ({len(results)} results, {len(by_app)} apps)")
    print(f"{'═' * 74}")
    print(f"  {'APP':<28} {'W':>3} {'RUNS':>5} {'COMPILE':>8} {'RUNTIME':>8} {'CRUD':>6}  {'STATUS'}")
    print(f"  {'-' * 70}")

    rows = []
    for name, runs in by_app.items():
        n         = len(runs)
        weight    = runs[0].get("weight", 1.0)
        compile_r = sum(1 for r in runs if r.get("compile_success")) / n
        runtime_r = sum(1 for r in runs if r.get("runtime_success")) / n
        crud_r    = sum(1 for r in runs if r.get("crud_success")) / n
        # composite failure score (lower = worse)
        composite = compile_r * 0.4 + runtime_r * 0.4 + crud_r * 0.2
        rows.append((composite, weight, name, n, compile_r, runtime_r, crud_r))

    rows.sort(key=lambda x: (x[0], -x[1]))  # worst first, then by weight

    for composite, weight, name, n, compile_r, runtime_r, crud_r in rows:
        if composite >= 0.95:
            status = "✓ solid"
        elif composite >= 0.80:
            status = "↗ good"
        elif composite >= 0.60:
            status = "⚠ weak"
        else:
            status = "✗ failing"
        print(
            f"  {name:<28} {weight:>3.0f} {n:>5} "
            f"{compile_r:>7.0%} {runtime_r:>8.0%} {crud_r:>6.0%}  {status}"
        )

    print(f"{'═' * 74}")
    # Highlight the top 3 weakest
    weak = [(n, c) for c, w, n, *_ in rows if c < 0.80]
    if weak:
        print(f"  Focus: {', '.join(n for n, _ in weak[:3])}")
    print()


def print_stage_timing(results: list[dict]) -> None:
    """Average time per pipeline stage across all results."""
    stage_fields = [
        ("plan",        "time_plan_s",       "Planner"),
        ("architect",   "time_architect_s",  "Architect"),
        ("backend",     "time_backend_s",    "Backend Gen"),
        ("frontend",    "time_frontend_s",   "Frontend Gen"),
        ("validation",  "time_validation_s", "Validation"),
        ("repairs",     "time_repairs_s",    "Repairs"),
        ("runtime",     "time_runtime_s",    "Runtime"),
    ]

    has_timing = any(r.get("time_plan_s", 0) > 0 for r in results)
    if not has_timing:
        print("  [Stage timing not available — data predates instrumentation]")
        return

    total_by_stage: dict[str, float] = defaultdict(float)
    count_by_stage: dict[str, int]   = defaultdict(int)
    for r in results:
        for _, field, _ in stage_fields:
            v = r.get(field, 0) or 0
            if v > 0:
                total_by_stage[field] += v
                count_by_stage[field] += 1

    print(f"\n{'═' * 52}")
    print(f"  Stage Timing Breakdown  (avg across {len(results)} runs)")
    print(f"{'═' * 52}")
    print(f"  {'STAGE':<18} {'AVG TIME':>10}  {'BAR'}")
    print(f"  {'-' * 48}")

    max_time = max(
        (total_by_stage[f] / count_by_stage[f] for _, f, _ in stage_fields
         if count_by_stage[f] > 0),
        default=1.0
    )

    grand_total = 0.0
    for _, field, label in stage_fields:
        if count_by_stage[field] == 0:
            continue
        avg = total_by_stage[field] / count_by_stage[field]
        grand_total += avg
        bar_len = int(avg / max_time * 30)
        bar = "█" * bar_len + "░" * (30 - bar_len)
        print(f"  {label:<18} {avg:>8.1f}s  {bar}")

    print(f"  {'─' * 48}")
    print(f"  {'TOTAL':<18} {grand_total:>8.1f}s")
    print(f"{'═' * 52}")
    # Highlight bottleneck
    slowest = max(
        ((total_by_stage[f] / count_by_stage[f], lbl)
         for _, f, lbl in stage_fields if count_by_stage[f] > 0),
        default=(0, "none")
    )
    print(f"  Bottleneck: {slowest[1]} ({slowest[0]:.1f}s avg)")
    print()


def print_repair_efficiency(results: list[dict]) -> None:
    """Repair pipeline efficiency metrics."""
    repaired   = [r for r in results if r.get("needs_repair")]
    unrepaired = [r for r in results if not r.get("needs_repair")]
    n = len(results)
    if not n:
        return

    intervention_rate = len(repaired) / n

    print(f"\n{'═' * 52}")
    print(f"  Repair Efficiency  ({n} runs)")
    print(f"{'═' * 52}")
    print(f"  Intervention rate      {intervention_rate:>8.0%}   ({len(repaired)}/{n} apps needed repair)")

    if repaired:
        avg_fixes       = sum(r.get("fix_count", 0) for r in repaired) / len(repaired)
        avg_files       = sum(r.get("repair_files_changed", 0) for r in repaired) / len(repaired)
        success_after   = sum(1 for r in repaired if r.get("compile_success")) / len(repaired)
        repair_cost_avg = sum(r.get("cost_repairs_usd", 0) for r in repaired) / len(repaired)
        gen_cost_avg    = sum(r.get("cost_generation_usd", 0) for r in results) / n
        print(f"  Avg repairs per app    {avg_fixes:>8.1f}")
        print(f"  Avg files changed      {avg_files:>8.1f}")
        print(f"  Success after repair   {success_after:>8.0%}")
        print(f"  Cost: generation       ${gen_cost_avg:>7.4f}  ← per app")
        print(f"  Cost: repairs          ${repair_cost_avg:>7.4f}  ← per app needing repair")
        repair_pct = repair_cost_avg / (gen_cost_avg + repair_cost_avg) * 100 if gen_cost_avg else 0
        print(f"  Repair cost share      {repair_pct:>7.0f}%   of total cost")

    print(f"{'═' * 52}")
    if intervention_rate > 0.5:
        print(f"  ⚠ >50% of apps need repair — generator quality is the bottleneck")
    elif len(repaired) > 0:
        avg_fixes = sum(r.get("fix_count", 0) for r in repaired) / len(repaired)
        if avg_fixes > 2:
            print(f"  ⚠ avg {avg_fixes:.1f} repairs/app — consider improving first-pass generation")
    print()


def print_score_split(results: list[dict]) -> None:
    """
    Engineering Score vs Product Score split.

    Engineering = compile + runtime + CRUD (measurable)
    Product     = browser + deployment + forge_score quality (UX/completeness signal)
    """
    n = len(results)
    if not n:
        return

    eng_scores  = []
    prod_scores = []
    for r in results:
        c = 1 if r.get("compile_success") else 0
        rt = 1 if r.get("runtime_success") else 0
        crud = 1 if r.get("crud_success") else 0
        br = 1 if r.get("browser_success") else 0
        dep = 1 if r.get("deployment_success") else 0
        score = r.get("forge_score", 0) or 0

        eng_scores.append((c + rt + crud) / 3 * 100)
        # Product score weights browser + deploy + overall score quality
        prod_scores.append((br * 25 + dep * 25 + (score / 100) * 50))

    avg_eng  = sum(eng_scores) / n
    avg_prod = sum(prod_scores) / n

    def _bar(v, width=24):
        filled = int(v / 100 * width)
        return "█" * filled + "░" * (width - filled)

    print(f"\n{'═' * 52}")
    print(f"  Engineering vs Product Score")
    print(f"{'═' * 52}")
    print(f"  Engineering Score   {avg_eng:>6.1f}/100  [{_bar(avg_eng)}]")
    print(f"  (compile + runtime + CRUD)")
    print()
    print(f"  Product Score       {avg_prod:>6.1f}/100  [{_bar(avg_prod)}]")
    print(f"  (browser + deploy + overall quality)")
    print(f"{'═' * 52}")
    gap = avg_eng - avg_prod
    if gap > 15:
        print(f"  Gap: {gap:.0f}pts — code works but UX/deploy needs work")
    elif gap < -10:
        print(f"  Gap: {abs(gap):.0f}pts — product quality exceeds engineering reliability")
    else:
        print(f"  Engineering and Product scores are balanced")
    print()


def main():
    args = sys.argv[1:]
    run_id    = None
    all_runs  = False

    for i, arg in enumerate(args):
        if arg == "--run-id" and i + 1 < len(args):
            run_id = args[i + 1]
        elif arg == "--all-runs":
            all_runs = True

    if all_runs:
        results = _load_all_results()
        print(f"Loaded {len(results)} results across all runs")
    else:
        results = _load_results(run_id)

    if not results:
        return

    print_heatmap(results)
    print_stage_timing(results)
    print_repair_efficiency(results)
    print_score_split(results)


if __name__ == "__main__":
    main()
