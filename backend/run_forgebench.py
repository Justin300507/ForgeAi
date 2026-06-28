"""
ForgeBench — Open Benchmark for AI App Generators

Measures how reliably an AI code generator can produce working, deployable apps
from plain-English descriptions. Designed to be run against any generator.

DEFAULT: runs against ForgeAI locally.
CUSTOM:  implement the Generator interface to benchmark any other system.

Usage:
    # Benchmark ForgeAI (default)
    python run_forgebench.py

    # Run a specific suite
    python run_forgebench.py --suite golden       # ForgeBench-20 (standard)
    python run_forgebench.py --suite realtime     # ForgeBench-RealTime-20
    python run_forgebench.py --suite ai           # ForgeBench-AI-20
    python run_forgebench.py --suite enterprise   # ForgeBench-Enterprise-20

    # Run all 4 suites
    python run_forgebench.py --suite all

    # Show KPI dashboard only (no generation)
    python run_forgebench.py --dashboard

    # Rerun only previous failures
    python run_forgebench.py --suite golden --rerun-failures --run-id <id>

    # Show benchmark history trend
    python run_forgebench.py --trend

    # Benchmark a custom generator (implement the adapter)
    python run_forgebench.py --generator my_adapter.MyAdapter

ForgeBench Scoring:
    Weighted Score   = sum(forge_score * weight) / sum(weights)
    Pass Rate        = apps that compile AND run / total
    Cost/Success     = total USD / successful apps
    Intervention %   = apps that needed ≥1 repair / total

Published results: https://github.com/your-repo/forgebench-results
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
import traceback
from abc import ABC, abstractmethod
from pathlib import Path

# Always run from backend/
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.benchmark.loader import load_prompts, BenchmarkPrompt, BENCHMARKS_DIR
from app.benchmark.metrics import BenchmarkResult, compute_report
from app.benchmark.reporter import BenchmarkReporter
from app.benchmark.kpi_dashboard import print_dashboard, print_trend as kpi_trend

RESULTS_DIR = Path("benchmark_results")

SUITE_MAP = {
    "golden":     "golden",
    "realtime":   "forgebench_realtime",
    "ai":         "forgebench_ai",
    "enterprise": "forgebench_enterprise",
}

SUITE_LABELS = {
    "golden":     "ForgeBench-20",
    "realtime":   "ForgeBench-RealTime-20",
    "ai":         "ForgeBench-AI-20",
    "enterprise": "ForgeBench-Enterprise-20",
}


# ── Generator Interface ────────────────────────────────────────────────────────

class Generator(ABC):
    """
    Implement this to benchmark any AI app generator against ForgeBench.

    Input:  idea (str) — plain-English description of the app
    Output: GeneratorResult

    Implement generate() to call your generator and return the result.
    ForgeAI's adapter is ForgeAIGenerator (used by default).
    """

    @abstractmethod
    def generate(self, idea: str) -> "GeneratorResult":
        ...

    def name(self) -> str:
        return self.__class__.__name__


class GeneratorResult:
    def __init__(
        self,
        compile_success:    bool  = False,
        runtime_success:    bool  = False,
        crud_success:       bool  = False,
        deployment_success: bool  = False,
        forge_score:        float = 0.0,
        fix_count:          int   = 0,
        first_pass_compile: bool  = False,
        needs_repair:       bool  = False,
        endpoint_pass_rate: float = 0.0,
        estimated_cost_usd: float = 0.0,
        generation_time_s:  float = 0.0,
        error:              str   = "",
    ):
        self.compile_success    = compile_success
        self.runtime_success    = runtime_success
        self.crud_success       = crud_success
        self.deployment_success = deployment_success
        self.forge_score        = forge_score
        self.fix_count          = fix_count
        self.first_pass_compile = first_pass_compile
        self.needs_repair       = needs_repair
        self.endpoint_pass_rate = endpoint_pass_rate
        self.estimated_cost_usd = estimated_cost_usd
        self.generation_time_s  = generation_time_s
        self.error              = error


class ForgeAIGenerator(Generator):
    """Default: runs ForgeAI's local pipeline."""

    def __init__(self, provider: str = "auto"):
        self.provider = provider

    def name(self) -> str:
        return f"ForgeAI ({self.provider})"

    def generate(self, idea: str) -> GeneratorResult:
        t0 = time.time()
        try:
            from app.services.project_service import generate_project
            out = generate_project(idea, provider=self.provider)

            forge = out.get("forge_score") or {}
            score = float(forge.get("score", 0) if isinstance(forge, dict) else forge)
            rt    = out.get("runtime") or {}
            journey = rt.get("journey") or {}

            try:
                from app.utils.cost_tracker import get_session_cost_usd
                cost = get_session_cost_usd()
            except Exception:
                cost = 0.0

            return GeneratorResult(
                compile_success    = out.get("validation", {}).get("passed", False),
                runtime_success    = rt.get("success", False),
                crud_success       = bool(journey.get("success") and not journey.get("skipped")),
                forge_score        = score,
                fix_count          = out.get("stats", {}).get("fix_attempts", 0),
                first_pass_compile = out.get("first_pass_compile", False),
                needs_repair       = out.get("needs_repair", False),
                endpoint_pass_rate = float(rt.get("endpoint_pass_rate", 0)),
                estimated_cost_usd = cost,
                generation_time_s  = round(time.time() - t0, 1),
            )
        except Exception as exc:
            return GeneratorResult(
                error             = f"{type(exc).__name__}: {exc}",
                generation_time_s = round(time.time() - t0, 1),
            )


# ── Runner ─────────────────────────────────────────────────────────────────────

def run_suite(
    suite_key: str,
    generator: Generator,
    version:   str,
    label:     str,
    run_id:    str | None = None,
    rerun_failures: bool = False,
    prev_run_id: str | None = None,
    fail_threshold: float = 75.0,
) -> None:
    difficulty = SUITE_MAP.get(suite_key, suite_key)
    suite_label = SUITE_LABELS.get(suite_key, suite_key)

    if rerun_failures and prev_run_id:
        from run_benchmark import load_failed_prompts
        prompts = load_failed_prompts(prev_run_id, threshold=fail_threshold)
    else:
        prompts = load_prompts(difficulty=difficulty)

    if not prompts:
        print(f"No prompts found for suite '{suite_key}' (difficulty='{difficulty}')")
        print(f"Expected in: {BENCHMARKS_DIR / difficulty}/")
        return

    effective_run_id = run_id or f"{time.strftime('%Y%m%d_%H%M')}_{suite_key}"
    output_dir = RESULTS_DIR / effective_run_id

    reporter = BenchmarkReporter(
        output_dir = output_dir,
        run_id     = effective_run_id,
        version    = version,
        provider   = generator.name(),
        label      = label or suite_label,
    )

    print(f"\n{'═' * 62}")
    print(f"  {suite_label}  —  {version}")
    print(f"  Generator: {generator.name()}")
    print(f"  Prompts: {len(prompts)}")
    print(f"{'═' * 62}")

    for idx, prompt in enumerate(prompts, 1):
        print(f"\n[{idx:2d}/{len(prompts)}] {prompt.name:<28} (w={prompt.weight:.0f})")
        t0 = time.time()
        result_obj = generator.generate(prompt.idea)

        result = BenchmarkResult(
            prompt_file         = prompt.file,
            name                = prompt.name,
            difficulty          = prompt.difficulty,
            idea                = prompt.idea,
            weight              = prompt.weight,
            timestamp           = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            provider_used       = generator.name(),
            compile_success     = result_obj.compile_success,
            runtime_success     = result_obj.runtime_success,
            crud_success        = result_obj.crud_success,
            deployment_success  = result_obj.deployment_success,
            forge_score         = result_obj.forge_score,
            fix_count           = result_obj.fix_count,
            first_pass_compile  = result_obj.first_pass_compile,
            needs_repair        = result_obj.needs_repair,
            endpoint_pass_rate  = result_obj.endpoint_pass_rate,
            estimated_cost_usd  = result_obj.estimated_cost_usd,
            generation_time_s   = result_obj.generation_time_s,
            crashed             = bool(result_obj.error),
            crash_error         = result_obj.error,
        )
        reporter.record(result)

    reporter.finalize()


def load_custom_generator(dotted_path: str, provider: str) -> Generator:
    """Load a custom Generator subclass by dotted module.Class path."""
    parts = dotted_path.rsplit(".", 1)
    if len(parts) != 2:
        raise ValueError(f"--generator must be 'module.ClassName', got: {dotted_path}")
    mod = importlib.import_module(parts[0])
    cls = getattr(mod, parts[1])
    return cls(provider=provider) if "provider" in cls.__init__.__code__.co_varnames else cls()


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="ForgeBench — Open Benchmark for AI App Generators",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--suite",      default="golden",
                   help="Suite to run: golden|realtime|ai|enterprise|all")
    p.add_argument("--provider",   default="auto",
                   help="LLM provider (ForgeAI only): cerebras|groq|openrouter|auto")
    p.add_argument("--generator",  default=None,
                   help="Custom generator class (module.ClassName) — overrides --provider")
    p.add_argument("--version",    default="v19",
                   help="Version label for history tracking")
    p.add_argument("--label",      default="",  help="Human-readable run label")
    p.add_argument("--run-id",     default=None, help="Override run ID")
    p.add_argument("--rerun-failures", action="store_true",
                   help="Rerun only failures from a previous run")
    p.add_argument("--fail-threshold", type=float, default=75.0,
                   help="Score below this = failure for --rerun-failures")
    p.add_argument("--dashboard",  action="store_true",
                   help="Show KPI dashboard (no generation)")
    p.add_argument("--heatmap",    action="store_true",
                   help="Show failure heatmap + stage timing + repair efficiency (no generation)")
    p.add_argument("--trend",      action="store_true",
                   help="Show version history trend (no generation)")
    p.add_argument("--list",       action="store_true",
                   help="List available suites and prompt counts")
    p.add_argument("--all-runs",   action="store_true",
                   help="Aggregate heatmap across all historical runs")
    return p.parse_args()


def main():
    args = parse_args()

    if args.dashboard:
        print_dashboard()
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

    if args.trend:
        kpi_trend()
        return

    if args.list:
        print(f"\nForgeBench Suites:")
        for key, diff in SUITE_MAP.items():
            prompts = load_prompts(difficulty=diff)
            label   = SUITE_LABELS[key]
            print(f"  {key:<12} → {label:<28} ({len(prompts)} prompts)")
        total = sum(len(load_prompts(difficulty=d)) for d in SUITE_MAP.values())
        print(f"\n  Total: {total} prompts across {len(SUITE_MAP)} suites")
        return

    # Build generator
    if args.generator:
        generator = load_custom_generator(args.generator, args.provider)
    else:
        generator = ForgeAIGenerator(provider=args.provider)

    suites = list(SUITE_MAP.keys()) if args.suite == "all" else [args.suite]

    for suite in suites:
        run_suite(
            suite_key       = suite,
            generator       = generator,
            version         = args.version,
            label           = args.label,
            run_id          = args.run_id if len(suites) == 1 else None,
            rerun_failures  = args.rerun_failures,
            prev_run_id     = args.run_id if args.rerun_failures else None,
            fail_threshold  = args.fail_threshold,
        )

    # Always show KPI dashboard at the end
    print_dashboard()


if __name__ == "__main__":
    main()
