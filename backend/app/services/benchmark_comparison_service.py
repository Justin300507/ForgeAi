"""
V7 Benchmark Comparison Service

Compares ForgeAI performance across pipeline versions (V5, V6, V7)
using the dataset index. Detects regressions automatically and
measures improvement velocity.

Metrics tracked per version:
- avg_score: average Forge Score
- pass_rate: % of projects that passed runtime validation
- runtime_rate: % passing runtime smoke tests
- avg_time: average generation time
- cost_per_success: estimated LLM cost per successful project
- regression_risk: did a recent change drop avg_score by 5+?
"""
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VersionMetrics:
    version: str
    run_count: int
    avg_score: float
    pass_rate: float
    runtime_pass_rate: float
    avg_generation_time: float
    security_score: float | None
    performance_score: float | None
    maintainability_score: float | None
    top_failure_types: list[str] = field(default_factory=list)


@dataclass
class BenchmarkComparison:
    versions: dict[str, VersionMetrics]
    best_version: str
    regression_detected: bool
    regression_details: str
    improvement_from_v5: float | None    # score delta V5 → latest
    improvement_velocity: float          # avg score change per 10 runs
    regression_threshold: float = 5.0   # points drop = regression


def compare_versions(
    min_runs_per_version: int = 3,
) -> BenchmarkComparison:
    """
    Read the dataset index and compute per-version metrics.
    Returns a BenchmarkComparison with regression detection.
    """
    from app.services.failure_dataset_service import get_index

    entries = get_index(limit=500)

    by_version: dict[str, list[dict]] = {}
    for e in entries:
        v = e.get("version", "v5")
        by_version.setdefault(v, []).append(e)

    version_metrics: dict[str, VersionMetrics] = {}
    for version, runs in by_version.items():
        if len(runs) < min_runs_per_version:
            continue

        scores = [r.get("score", 0) for r in runs]
        avg_score = sum(scores) / len(scores)
        pass_rate = sum(1 for r in runs if r.get("runtime_passed")) / len(runs)

        # V6+ metrics
        sec_scores = [r.get("security_score") for r in runs if r.get("security_score") is not None]
        perf_scores = [r.get("performance_score") for r in runs if r.get("performance_score") is not None]
        maint_scores = [r.get("maintainability_score") for r in runs if r.get("maintainability_score") is not None]

        # Top failure types
        from collections import Counter
        error_types = [r.get("runtime_error_type") for r in runs if r.get("runtime_error_type")]
        top_failures = [k for k, _ in Counter(error_types).most_common(3)]

        version_metrics[version] = VersionMetrics(
            version=version,
            run_count=len(runs),
            avg_score=round(avg_score, 1),
            pass_rate=round(pass_rate, 3),
            runtime_pass_rate=round(pass_rate, 3),
            avg_generation_time=0.0,  # would need generation_time in index
            security_score=round(sum(sec_scores) / len(sec_scores), 1) if sec_scores else None,
            performance_score=round(sum(perf_scores) / len(perf_scores), 1) if perf_scores else None,
            maintainability_score=round(sum(maint_scores) / len(maint_scores), 1) if maint_scores else None,
            top_failure_types=top_failures,
        )

    # Determine best version by avg_score
    best_version = max(version_metrics, key=lambda v: version_metrics[v].avg_score) if version_metrics else "v5"

    # Regression detection: look at last 10 runs vs previous 10 runs of same version
    regression_detected = False
    regression_details = ""
    for version, runs in by_version.items():
        if len(runs) < 20:
            continue
        recent_10 = [r.get("score", 0) for r in runs[-10:]]
        prev_10 = [r.get("score", 0) for r in runs[-20:-10]]
        recent_avg = sum(recent_10) / len(recent_10)
        prev_avg = sum(prev_10) / len(prev_10)
        delta = prev_avg - recent_avg  # positive = regression
        if delta >= 5.0:
            regression_detected = True
            regression_details = (
                f"{version}: score dropped {delta:.1f} points "
                f"(recent avg: {recent_avg:.1f}, previous avg: {prev_avg:.1f})"
            )

    # Improvement velocity (score change per 10 runs, for latest version)
    improvement_velocity = 0.0
    all_runs_sorted = sorted(entries, key=lambda e: e.get("timestamp", ""))
    if len(all_runs_sorted) >= 20:
        first_10 = [r.get("score", 0) for r in all_runs_sorted[:10]]
        last_10 = [r.get("score", 0) for r in all_runs_sorted[-10:]]
        improvement_velocity = (sum(last_10) / len(last_10)) - (sum(first_10) / len(first_10))

    # V5 → latest improvement
    improvement_from_v5 = None
    if "v5" in version_metrics and "v7" in version_metrics:
        improvement_from_v5 = version_metrics["v7"].avg_score - version_metrics["v5"].avg_score
    elif "v5" in version_metrics and "v6" in version_metrics:
        improvement_from_v5 = version_metrics["v6"].avg_score - version_metrics["v5"].avg_score

    return BenchmarkComparison(
        versions=version_metrics,
        best_version=best_version,
        regression_detected=regression_detected,
        regression_details=regression_details,
        improvement_from_v5=improvement_from_v5,
        improvement_velocity=round(improvement_velocity, 1),
    )


def print_comparison_report(comparison: BenchmarkComparison) -> None:
    print(f"\n{'='*65}")
    print(f"  BENCHMARK COMPARISON REPORT")
    print(f"{'='*65}")

    for version, m in sorted(comparison.versions.items()):
        print(f"\n  {version.upper()} ({m.run_count} runs):")
        print(f"    Avg Score:      {m.avg_score:.1f}/100")
        print(f"    Pass Rate:      {m.pass_rate:.0%}")
        if m.security_score is not None:
            print(f"    Security:       {m.security_score:.1f}/100")
        if m.performance_score is not None:
            print(f"    Performance:    {m.performance_score:.1f}/100")
        if m.maintainability_score is not None:
            print(f"    Maintainability:{m.maintainability_score:.1f}/100")
        if m.top_failure_types:
            print(f"    Top Failures:   {', '.join(m.top_failure_types)}")

    print(f"\n  Best version:      {comparison.best_version.upper()}")
    if comparison.improvement_from_v5 is not None:
        sign = "+" if comparison.improvement_from_v5 >= 0 else ""
        print(f"  V5 → Latest:       {sign}{comparison.improvement_from_v5:.1f} points")
    print(f"  Improvement velocity: {comparison.improvement_velocity:+.1f} pts / 10 runs")

    if comparison.regression_detected:
        print(f"\n  ⚠️  REGRESSION DETECTED: {comparison.regression_details}")
    else:
        print(f"\n  No regressions detected")

    print("=" * 65)


def get_comparison_for_research() -> dict[str, Any]:
    """Return a serializable dict for the Research Agent."""
    comparison = compare_versions()
    return {
        v: {
            "run_count": m.run_count,
            "avg_score": m.avg_score,
            "pass_rate": m.pass_rate,
            "security_score": m.security_score,
            "performance_score": m.performance_score,
            "maintainability_score": m.maintainability_score,
            "top_failure_types": m.top_failure_types,
        }
        for v, m in comparison.versions.items()
    }
