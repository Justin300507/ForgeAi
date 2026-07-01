"""
ForgeAI Benchmark Runner

Runs all 100 prompts through the pipeline and measures:
  Compile Success, Runtime Success, Browser Success, Deployment Success,
  Generation Time, Cost, Retry Count

Usage (from backend/ with venv active):
    python run_benchmark.py --all                        # all 100 apps
    python run_benchmark.py --n 10                       # first 10
    python run_benchmark.py --difficulty beginner        # one tier only
    python run_benchmark.py --name todo                  # specific prompt
    python run_benchmark.py --dry-run                    # list prompts, don't generate
    python run_benchmark.py --resume --run-id <id>       # continue a previous run
    python run_benchmark.py --provider cerebras --n 20   # override provider
    python run_benchmark.py --label "after-auth-fix"     # tag the run
    python run_benchmark.py --deploy                     # also deploy to Render/Cloudflare
    python run_benchmark.py --pipeline v7                # use v7 instead of default
    python run_benchmark.py --max-cost 5.00              # stop if total cost > $5

Results:
    benchmark_results/<run-id>/
        results.jsonl    (one JSON line per app — written incrementally)
        report.json      (full aggregate report)
        report.md        (human-readable markdown)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
import uuid

# Force UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Always run from backend/
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

from pathlib import Path
from app.benchmark.loader import load_prompts, list_prompts, BenchmarkPrompt, BENCHMARKS_DIR
from app.benchmark.metrics import BenchmarkResult, BenchmarkReport
from app.benchmark.reporter import BenchmarkReporter, load_completed_ids
from app.providers.model_router import score_complexity, tier_from_score

RESULTS_DIR = Path("benchmark_results")


def load_failed_prompts(run_id: str, threshold: float = 75.0) -> list[BenchmarkPrompt]:
    """
    Read a previous run's results.jsonl and return BenchmarkPrompts for entries
    that failed (forge_score < threshold OR compile_success=False OR crashed).

    This lets you rerun only the prompts that need fixing, skipping the ones
    that already pass. Cheap: uses local files, no API calls.
    """
    results_path = RESULTS_DIR / run_id / "results.jsonl"
    if not results_path.exists():
        print(f"No results found for run-id '{run_id}' at {results_path}")
        return []

    failed: list[BenchmarkPrompt] = []
    total = 0

    with results_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            total += 1

            is_failure = (
                r.get("crashed", False)
                or not r.get("compile_success", True)
                or float(r.get("forge_score") or 0) < threshold
            )
            if is_failure:
                # Reconstruct the BenchmarkPrompt from the stored data
                failed.append(BenchmarkPrompt(
                    file       = r.get("prompt_file", "unknown"),
                    name       = r.get("name", "unknown"),
                    difficulty = r.get("difficulty", "unknown"),
                    idea       = r.get("idea", ""),
                ))

    passed = total - len(failed)
    print(f"\nLoaded run '{run_id}': {total} results, {passed} passed, {len(failed)} failed")
    print(f"Score threshold: {threshold}  (use --fail-threshold to change)\n")

    if not failed:
        print("All prompts passed — nothing to rerun.")
    else:
        print(f"Will rerun {len(failed)} failures:")
        for p in failed:
            print(f"  {p.short_name():<25} {p.idea[:60]}")
        print()

    return failed


def parse_args():
    p = argparse.ArgumentParser(description="ForgeAI Benchmark Runner")
    p.add_argument("--all",         action="store_true", help="Run all 100 prompts")
    p.add_argument("--n",           type=int,   default=None,  help="Max prompts to run")
    p.add_argument("--offset",      type=int,   default=0,     help="Skip first N prompts")
    p.add_argument("--difficulty",  type=str,   default=None,  help="beginner|intermediate|advanced|enterprise")
    p.add_argument("--name",        type=str,   default=None,  help="Filter by prompt name substring")
    p.add_argument("--provider",    type=str,   default="auto",help="LLM provider override")
    p.add_argument("--pipeline",    type=str,   default="v7",  help="v5|v6|v7|v15 (default: v7)")
    p.add_argument("--deploy",      action="store_true", help="Also deploy after generation")
    p.add_argument("--label",       type=str,   default="",    help="Tag this run (for comparison)")
    p.add_argument("--run-id",      type=str,   default=None,  help="Run ID (default: auto-generated)")
    p.add_argument("--resume",      action="store_true", help="Skip prompts already in run-id's results")
    p.add_argument("--dry-run",     action="store_true", help="List prompts without generating")
    p.add_argument("--max-cost",    type=float, default=None,  help="Stop if total USD exceeds this")
    p.add_argument("--version",     type=str,   default="v17", help="ForgeAI version tag for report")
    p.add_argument("--repeat",      type=int,   default=1,
                   help="Run each prompt N times (stability test — measures variance)")
    p.add_argument("--parallel",    type=int,   default=1,
                   help="Max concurrent generations (>1 uses parallel batch runner)")
    p.add_argument("--rerun-failures", dest="rerun_failures", action="store_true",
                   help="Rerun only the failed/low-scoring prompts from --run-id")
    p.add_argument("--fail-threshold", type=float, default=75.0,
                   help="Score below this counts as failure for --rerun-failures (default: 75)")
    p.add_argument("--heatmap",     action="store_true",
                   help="Print failure heatmap + stage timing for a run (use with --run-id)")
    p.add_argument("--all-runs",    action="store_true",
                   help="Aggregate heatmap across all historical runs")
    return p.parse_args()


def run_one(prompt: BenchmarkPrompt, provider: str, pipeline: str, deploy: bool) -> BenchmarkResult:
    """Run a single prompt through the generation pipeline and return metrics."""
    t0 = time.time()
    result = BenchmarkResult(
        prompt_file   = prompt.file,
        name          = prompt.name,
        difficulty    = prompt.difficulty,
        idea          = prompt.idea,
        weight        = prompt.weight,
        timestamp     = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        provider_used = provider,
    )

    # Complexity tier
    score, _ = score_complexity(prompt.idea)
    result.complexity_tier = tier_from_score(score)

    try:
        if pipeline == "v15":
            from app.core.pipeline import V15Pipeline
            pipe = V15Pipeline(deploy=deploy, deploy_to="both" if deploy else "none")
            out = pipe.run(idea=prompt.idea, provider=provider)
            _fill_from_v15(result, out)

        elif pipeline in ("v6", "v7"):
            # Use v7 by default — it has self-improvement
            from app.services.v7_orchestrator import generate_project_v7
            out = generate_project_v7(prompt.idea, provider=provider)
            _fill_from_v6v7(result, out)

        else:  # v5 / legacy
            from app.services.project_service import generate_project
            out = generate_project(prompt.idea, provider=provider)
            _fill_from_v5(result, out)

    except Exception as exc:
        result.crashed     = True
        result.crash_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        traceback.print_exc()

    result.generation_time_s = round(time.time() - t0, 1)
    return result


# ── Result extractors per pipeline version ────────────────────────────────────

def _fill_from_v15(result: BenchmarkResult, out: dict):
    result.forge_score  = float(out.get("forge_score", 0))
    result.fix_count    = int(out.get("fix_attempts", 0) or 0)

    # Success flags from timeline + deployment (V15's flat result shape)
    stages = out.get("timeline", []) or []
    stage_status = {s.get("stage"): s.get("status") for s in stages}
    result.compile_success    = stage_status.get("compile", "skipped") == "passed"
    result.runtime_success    = result.forge_score >= 60  # Runtime Startup is 20% weight; no raw flag exposed
    result.browser_success    = result.forge_score >= 80
    result.deployment_success = bool((out.get("deployment") or {}).get("success"))

    # Estimated cost from token usage
    result.estimated_cost_usd = float(out.get("estimated_cost", 0))

    # Failures — score_history has per-attempt scores, not raw diagnostics;
    # retry_history carries the strategy names tried
    retry_history = out.get("retry_history", []) or []
    result.failure_reasons = [str(r)[:80] for r in retry_history][:5]


def _fill_from_v6v7(result: BenchmarkResult, out: dict):
    forge  = out.get("forge_score", {}) or {}
    result.forge_score        = float(forge.get("score", 0) if isinstance(forge, dict) else forge)
    result.fix_count          = out.get("fix_count", 0) or 0

    val = out.get("validation", {}) or {}
    rt  = out.get("runtime", {}) or {}

    result.compile_success    = not val.get("errors") or len(val.get("errors", [])) == 0
    result.runtime_success    = rt.get("success", False)
    result.browser_success    = rt.get("playwright_passed", False)
    result.deployment_success = bool(out.get("render_url") or out.get("backend_url"))

    result.static_errors  = len(val.get("errors", []))
    result.failure_reasons = (val.get("errors", []) or [])[:5]

    # Estimate cost from token usage if tracked
    try:
        from app.utils.cost_tracker import get_session_cost_usd
        result.estimated_cost_usd = get_session_cost_usd()
    except Exception:
        pass


def _fill_from_v5(result: BenchmarkResult, out: dict):
    forge  = out.get("forge_score", {}) or {}
    result.forge_score     = float(forge.get("score", 0) if isinstance(forge, dict) else forge)
    result.compile_success = out.get("validation", {}).get("passed", False)

    rt = out.get("runtime") or {}
    result.runtime_success    = rt.get("success", False)
    result.endpoint_pass_rate = float(rt.get("endpoint_pass_rate", 0.0))

    journey = rt.get("journey") or {}
    if journey and not journey.get("skipped", True):
        result.crud_success = journey.get("success", False)

    # Fallback: pipeline_metrics
    pm = out.get("pipeline_metrics") or {}
    if pm.get("crud_tests") is not None:
        result.crud_success = bool(pm["crud_tests"])

    result.static_errors   = len(out.get("validation", {}).get("errors", []))
    result.failure_reasons = out.get("validation", {}).get("errors", [])[:5]

    # First-pass and intervention metrics
    result.first_pass_compile = out.get("first_pass_compile", False)
    result.first_pass_runtime = (
        result.first_pass_compile and result.runtime_success and result.fix_count == 0
    )
    result.needs_repair = out.get("needs_repair", result.fix_count > 0)

    # Stage timings
    st = out.get("stage_timings") or {}
    result.time_plan_s       = st.get("plan", 0.0)
    result.time_architect_s  = st.get("architect", 0.0)
    result.time_backend_s    = st.get("backend", 0.0)
    result.time_frontend_s   = st.get("frontend", 0.0)
    result.time_validation_s = st.get("validation_first", 0.0)
    result.time_repairs_s    = st.get("repairs", 0.0)
    result.time_runtime_s    = st.get("runtime", 0.0)

    # Cost breakdown
    cb = out.get("cost_breakdown") or {}
    result.cost_generation_usd  = cb.get("generation_usd", 0.0)
    result.cost_repairs_usd     = cb.get("repairs_usd", 0.0)

    # Repair efficiency
    re_ = out.get("repair_efficiency") or {}
    result.repair_files_changed = re_.get("files_changed", 0)

    try:
        from app.utils.cost_tracker import get_session_cost_usd
        result.estimated_cost_usd = get_session_cost_usd()
    except Exception:
        result.estimated_cost_usd = cb.get("total_usd", 0.0)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    if args.dry_run:
        list_prompts()
        return

    if args.heatmap:
        from app.benchmark.heatmap import (
            print_heatmap, print_stage_timing,
            print_repair_efficiency, print_score_split,
            _load_results, _load_all_results,
        )
        results = _load_all_results() if args.all_runs else _load_results(args.run_id)
        if results:
            print_heatmap(results)
            print_stage_timing(results)
            print_repair_efficiency(results)
            print_score_split(results)
        return

    # ── Rerun-failures mode ───────────────────────────────────────────────────
    if args.rerun_failures:
        if not args.run_id:
            print("--rerun-failures requires --run-id <previous-run-id>")
            return
        prompts = load_failed_prompts(args.run_id, threshold=args.fail_threshold)
        if not prompts:
            return
        # New run ID for this rerun so results don't overwrite the original
        run_id    = f"{time.strftime('%Y%m%d_%H%M')}_rerun_{args.label or 'fix'}"
        output_dir = RESULTS_DIR / run_id
    else:
        run_id = args.run_id or f"{time.strftime('%Y%m%d_%H%M')}_{args.label or 'bench'}"
        output_dir = RESULTS_DIR / run_id

        # Load prompts
        prompts = load_prompts(
            difficulty  = args.difficulty,
            name_filter = args.name,
            n           = args.n if not args.all else None,
            offset      = args.offset,
        )

        if not prompts:
            print("No prompts matched the given filters. Use --dry-run to list all.")
            return

        # Resume: skip already-completed prompts
        if args.resume:
            done_ids = load_completed_ids(output_dir / "results.jsonl")
            before = len(prompts)
            prompts = [p for p in prompts if p.file not in done_ids]
            print(f"Resume: skipping {before - len(prompts)} already-completed prompts")

    # Expand prompts for stability testing (--repeat N)
    if args.repeat > 1:
        print(f"Stability mode: each of {len(prompts)} prompt(s) will run {args.repeat}x")
        prompts = [p for p in prompts for _ in range(args.repeat)]

    reporter = BenchmarkReporter(
        output_dir = output_dir,
        run_id     = run_id,
        version    = args.version,
        provider   = args.provider,
        label      = args.label,
    )

    print(f"\nForgeAI Benchmark — {args.version}  [{run_id}]")
    print(f"Pipeline: {args.pipeline}  Provider: {args.provider}  Deploy: {args.deploy}")
    print(f"Prompts:  {len(prompts)}  Repeat: {args.repeat}x  Parallel: {args.parallel}")
    print(f"Output:   {output_dir}/")
    print(f"{'=' * 60}")
    print(f"  [C=Compile R=Runtime U=CRUD B=Browser D=Deploy x=fail]")
    print(f"{'=' * 60}")

    total_cost = 0.0

    # Parallel mode: use batch_runner
    if args.parallel > 1:
        from app.services.batch_runner import run_batch_parallel
        ideas = [p.idea for p in prompts]
        batch_results = run_batch_parallel(ideas, max_workers=args.parallel, provider=args.provider)
        for i, (prompt, br) in enumerate(zip(prompts, batch_results)):
            result = BenchmarkResult(
                prompt_file=prompt.file, name=prompt.name,
                difficulty=prompt.difficulty, idea=prompt.idea,
                weight=prompt.weight,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                provider_used=args.provider,
                compile_success=br.validation_passed,
                runtime_success=br.runtime_passed,
                crud_success=br.crud_passed,
                forge_score=br.forge_score,
                generation_time_s=br.duration_s,
                crashed=bool(br.error),
                crash_error=br.error,
                endpoint_pass_rate=br.pipeline_metrics.get("endpoint_pass_rate") or 0.0,
            )
            reporter.record(result)
            total_cost += result.estimated_cost_usd
            if args.max_cost and total_cost > args.max_cost:
                print(f"\n[STOP] Total cost ${total_cost:.3f} exceeded --max-cost ${args.max_cost:.2f}")
                break
    else:
        for idx, prompt in enumerate(prompts, 1):
            print(f"\n[{idx:3d}/{len(prompts)}] {prompt.short_name()} — {prompt.idea[:55]}...")
            result = run_one(prompt, args.provider, args.pipeline, args.deploy)
            reporter.record(result)
            total_cost += result.estimated_cost_usd
            if args.max_cost and total_cost > args.max_cost:
                print(f"\n[STOP] Total cost ${total_cost:.3f} exceeded --max-cost ${args.max_cost:.2f}")
                break

    report = reporter.finalize()

    # Stability report: if --repeat > 1, show variance per prompt
    if args.repeat > 1:
        _print_stability_report(report, args.repeat)


def _print_stability_report(report: BenchmarkReport, repeat: int) -> None:
    """
    After a --repeat N run, group results by prompt name and compute variance.
    Shows: per-prompt pass rate, score mean ± stddev.
    """
    import statistics
    from collections import defaultdict

    groups: dict[str, list[dict]] = defaultdict(list)
    for res in report.results:
        groups[res.get("name", "unknown")].append(res)

    print(f"\n{'=' * 70}")
    print(f"  STABILITY REPORT  (each prompt ran {repeat}x)")
    print(f"{'=' * 70}")
    print(f"  {'PROMPT':<28} {'C':>4} {'R':>4} {'U':>4} {'AVG':>6} {'STDEV':>6}")
    print(f"  {'-' * 60}")

    for name, runs in sorted(groups.items()):
        n = len(runs)
        compile_r = sum(1 for r in runs if r.get("compile_success")) / n
        runtime_r = sum(1 for r in runs if r.get("runtime_success")) / n
        crud_r    = sum(1 for r in runs if r.get("crud_success")) / n
        scores    = [r.get("forge_score", 0) for r in runs]
        avg_score = statistics.mean(scores)
        stdev     = statistics.stdev(scores) if n > 1 else 0.0
        print(
            f"  {name:<28} "
            f"{compile_r:>3.0%} "
            f"{runtime_r:>3.0%} "
            f"{crud_r:>3.0%} "
            f"{avg_score:>6.1f} "
            f"{stdev:>6.1f}"
        )

    print(f"{'=' * 70}")
    print(f"  Target: Compile ≥98%  Runtime ≥95%  CRUD ≥90%  STDEV ≤5")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
