"""
Benchmark metrics data models.

BenchmarkResult:  one app generation attempt
BenchmarkReport:  aggregate across many results, including weighted score
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class BenchmarkResult:
    # Identity
    prompt_file:    str
    name:           str
    difficulty:     str
    idea:           str
    run_id:         str   = ""
    weight:         float = 1.0   # prompt weight (from weights.json; default 1)

    # Outcome flags
    compile_success:    bool  = False
    runtime_success:    bool  = False
    crud_success:       bool  = False
    browser_success:    bool  = False
    deployment_success: bool  = False
    crashed:            bool  = False
    endpoint_pass_rate: float = 0.0

    # Scores
    forge_score:     float = 0.0
    static_errors:   int   = 0
    runtime_errors:  int   = 0
    browser_errors:  int   = 0

    # First-pass metrics (before any repair attempts)
    first_pass_compile:   bool  = False
    first_pass_runtime:   bool  = False   # runtime passed on first static-pass attempt
    needs_repair:         bool  = False   # any fix was applied (proxy for manual intervention)

    # Stage timings (seconds per pipeline stage)
    time_plan_s:        float = 0.0
    time_architect_s:   float = 0.0
    time_backend_s:     float = 0.0
    time_frontend_s:    float = 0.0
    time_validation_s:  float = 0.0
    time_repairs_s:     float = 0.0
    time_runtime_s:     float = 0.0

    # Cost breakdown
    cost_generation_usd: float = 0.0   # cost through backend/frontend (before repairs)
    cost_repairs_usd:    float = 0.0   # cost of repair LLM calls
    repair_files_changed: int  = 0     # unique files touched by repair

    # Cost / speed
    generation_time_s:    float = 0.0
    estimated_cost_usd:   float = 0.0
    fix_count:            int   = 0
    provider_used:        str   = ""
    complexity_tier:      str   = ""

    # Failure detail
    failure_reasons: list[str] = field(default_factory=list)
    crash_error:     str       = ""
    timestamp:       str       = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "BenchmarkResult":
        return BenchmarkResult(**{k: v for k, v in d.items() if k in BenchmarkResult.__dataclass_fields__})

    def one_line(self) -> str:
        icons = [
            "C" if self.compile_success else "x",
            "R" if self.runtime_success else "x",
            "U" if self.crud_success    else "x",
            "B" if self.browser_success else "x",
            "D" if self.deployment_success else "x",
        ]
        status = "".join(icons)
        w = f" w={self.weight:.0f}" if self.weight != 1.0 else ""
        return (f"  [{status}] {self.difficulty[:3]:3s} {self.name:<28} "
                f"score={self.forge_score:5.1f}{w}  "
                f"ep={self.endpoint_pass_rate:.0%}  "
                f"fixes={self.fix_count}  "
                f"time={self.generation_time_s:4.0f}s  "
                f"${self.estimated_cost_usd:.3f}")


@dataclass
class DifficultyStats:
    difficulty:        str
    total:             int   = 0
    compile_rate:      float = 0.0
    runtime_rate:      float = 0.0
    browser_rate:      float = 0.0
    deployment_rate:   float = 0.0
    avg_score:         float = 0.0
    avg_time_s:        float = 0.0
    avg_cost_usd:      float = 0.0
    avg_fixes:         float = 0.0


@dataclass
class BenchmarkReport:
    run_id:        str
    version:       str   = "v19"
    provider:      str   = "auto"
    label:         str   = ""
    started_at:    str   = ""
    completed_at:  str   = ""
    total_prompts: int   = 0
    completed:     int   = 0
    crashed:       int   = 0

    # Top-line success rates (0.0–1.0)
    compile_success_rate:    float = 0.0
    runtime_success_rate:    float = 0.0
    crud_success_rate:       float = 0.0
    browser_success_rate:    float = 0.0
    deployment_success_rate: float = 0.0
    avg_endpoint_pass_rate:  float = 0.0

    # Scores
    avg_forge_score:    float = 0.0
    weighted_score:     float = 0.0   # sum(forge_score*weight)/sum(weights) — ForgeBench-20 headline
    weighted_pass_rate: float = 0.0   # sum(passed*weight)/sum(weights)

    # First-pass and intervention metrics
    first_pass_compile_rate: float = 0.0  # % that compiled without any fix
    first_pass_runtime_rate: float = 0.0  # % that passed runtime without any repair
    intervention_rate:       float = 0.0  # % of apps that needed ≥1 repair (needs_repair=True)

    # Cost efficiency
    avg_generation_s:    float = 0.0
    total_cost_usd:      float = 0.0
    avg_cost_usd:        float = 0.0
    cost_per_success:    float = 0.0   # total_cost / successful_apps
    avg_fix_count:       float = 0.0

    by_difficulty: list[dict] = field(default_factory=list)
    results:       list[dict] = field(default_factory=list)


def compute_report(
    results:    list[BenchmarkResult],
    run_id:     str,
    version:    str = "v19",
    provider:   str = "auto",
    label:      str = "",
    started_at: str = "",
) -> BenchmarkReport:
    if not results:
        return BenchmarkReport(run_id=run_id, started_at=started_at,
                               completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    done = [r for r in results if not r.crashed]
    n    = len(done)

    def rate(flag: str) -> float:
        if not n: return 0.0
        return sum(1 for r in done if getattr(r, flag)) / n

    # Weighted score: each prompt contributes weight * forge_score to the sum
    total_weight = sum(r.weight for r in done) or 1.0
    w_score      = sum(r.forge_score * r.weight for r in done) / total_weight
    w_pass_rate  = sum(r.weight for r in done if r.compile_success and r.runtime_success) / total_weight

    # Cost per successful app
    successful    = sum(1 for r in done if r.compile_success and r.runtime_success)
    total_cost    = sum(r.estimated_cost_usd for r in results)
    cost_per_ok   = round(total_cost / successful, 4) if successful else 0.0

    from app.benchmark.loader import DIFFICULTY_ORDER
    by_diff: list[dict] = []
    for diff in DIFFICULTY_ORDER:
        bucket = [r for r in done if r.difficulty == diff]
        if not bucket:
            continue
        nb = len(bucket)
        by_diff.append(asdict(DifficultyStats(
            difficulty      = diff,
            total           = nb,
            compile_rate    = round(sum(1 for r in bucket if r.compile_success) / nb, 3),
            runtime_rate    = round(sum(1 for r in bucket if r.runtime_success) / nb, 3),
            browser_rate    = round(sum(1 for r in bucket if r.browser_success) / nb, 3),
            deployment_rate = round(sum(1 for r in bucket if r.deployment_success) / nb, 3),
            avg_score       = round(sum(r.forge_score for r in bucket) / nb, 1),
            avg_time_s      = round(sum(r.generation_time_s for r in bucket) / nb, 1),
            avg_cost_usd    = round(sum(r.estimated_cost_usd for r in bucket) / nb, 4),
            avg_fixes       = round(sum(r.fix_count for r in bucket) / nb, 2),
        )))

    return BenchmarkReport(
        run_id                   = run_id,
        version                  = version,
        provider                 = provider,
        label                    = label,
        started_at               = started_at,
        completed_at             = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        total_prompts            = len(results),
        completed                = n,
        crashed                  = len(results) - n,
        compile_success_rate     = round(rate("compile_success"), 3),
        runtime_success_rate     = round(rate("runtime_success"), 3),
        crud_success_rate        = round(rate("crud_success"), 3),
        browser_success_rate     = round(rate("browser_success"), 3),
        deployment_success_rate  = round(rate("deployment_success"), 3),
        avg_endpoint_pass_rate   = round(sum(r.endpoint_pass_rate for r in done) / n if n else 0, 3),
        avg_forge_score          = round(sum(r.forge_score for r in done) / n if n else 0, 1),
        weighted_score           = round(w_score, 1),
        weighted_pass_rate       = round(w_pass_rate, 3),
        first_pass_compile_rate  = round(sum(1 for r in done if r.first_pass_compile) / n if n else 0, 3),
        first_pass_runtime_rate  = round(sum(1 for r in done if r.first_pass_runtime) / n if n else 0, 3),
        intervention_rate        = round(sum(1 for r in done if r.needs_repair) / n if n else 0, 3),
        avg_generation_s         = round(sum(r.generation_time_s for r in done) / n if n else 0, 1),
        total_cost_usd           = round(total_cost, 4),
        avg_cost_usd             = round(sum(r.estimated_cost_usd for r in done) / n if n else 0, 4),
        cost_per_success         = cost_per_ok,
        avg_fix_count            = round(sum(r.fix_count for r in done) / n if n else 0, 2),
        by_difficulty            = by_diff,
        results                  = [r.to_dict() for r in results],
    )
