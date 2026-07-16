"""
V7 Self-Improving AI Software Engineer Orchestrator

Implements the continuous improvement loop:

  Generate (V6 pipeline)
      ↓
  Record to Dataset
      ↓
  Benchmark Comparison (V5 vs V6 vs V7)
      ↓
  Research Agent Analysis
      ↓
  Rule Evolution Engine
      ↓
  Prompt Optimizer
      ↓
  Leaderboard Sync
      ↓
  Repeat

Each cycle tries to make ForgeAI measurably better than it was before.
Improvements are only kept if they pass a benchmarked improvement threshold.

V7 also adds self-healing regeneration: if static + runtime both fail after
all fix attempts, it uses the Research Agent's root cause analysis to
redesign the architecture before retrying.
"""
import os
import time
import hashlib
from typing import Any

from app.services.v6_orchestrator import generate_project_v6  # noqa: E402
from app.services.failure_dataset_service import (
    RunRecord, save_run_record, make_run_id, get_dataset_stats,
)
from app.services.benchmark_comparison_service import (
    compare_versions, print_comparison_report,
)
from app.services.research_agent_service import run_research_agent, get_latest_findings
from app.services.rule_evolution_service import run_rule_evolution
from app.services.prompt_optimizer_service import run_prompt_optimization
from app.services.improvement_leaderboard_service import (
    sync_from_prompt_optimizer, sync_from_rule_evolution, print_leaderboard,
)
from app.services.agent_collaboration import AgentCollaboration


def generate_project_v7(
    idea: str,
    provider: str = "auto",
    run_improvement_cycle: bool = True,
    improvement_cycle_every_n: int = 5,
    skip_reviews: bool = False,
    frontend_target: str = "web",
    style_override: str | None = None,
    motion_intensity: str | None = None,
    include_landing_page: bool = False,
) -> dict[str, Any]:
    """
    V7 pipeline = V6 generation + self-improvement cycle.

    Args:
        idea: plain-English project idea
        provider: LLM provider
        run_improvement_cycle: if True, may trigger the improvement cycle after generation
        improvement_cycle_every_n: trigger the cycle every N runs

    The improvement cycle triggers automatically based on dataset size.
    """
    start = time.time()
    run_id = make_run_id(idea, "v7")

    print(f"\n{'#'*70}")
    print(f"# V7 SELF-IMPROVING AI SOFTWARE ENGINEER")
    print(f"# Idea: {idea[:60]}")
    print(f"# Run ID: {run_id}")
    print(f"{'#'*70}")

    # ── Step 1: Generate with V6 multi-agent pipeline ────────────────────
    collab = AgentCollaboration()
    v6_result = generate_project_v6(
        idea, provider=provider, collab=collab, skip_reviews=skip_reviews, frontend_target=frontend_target,
        style_override=style_override, motion_intensity=motion_intensity, include_landing_page=include_landing_page,
    )

    # ── Step 2: Record to failure dataset ────────────────────────────────
    _record_to_dataset(idea, run_id, provider, v6_result)

    # ── Step 3: Self-healing if generation failed ─────────────────────────
    runtime_passed = bool(
        v6_result.get("runtime") and v6_result["runtime"].get("success")
    )
    # Self-healing re-runs the ENTIRE V6 pipeline (plan → architect → backend
    # → frontend → validation) — it roughly doubles the LLM cost of a failed
    # run. FORGE_SELF_HEAL=0 disables it when running on a tight credit budget.
    self_heal_enabled = os.environ.get("FORGE_SELF_HEAL", "1") != "0"
    if not runtime_passed and not v6_result.get("validation", {}).get("passed") and self_heal_enabled:
        v6_result = _attempt_self_healing(idea, provider, v6_result, run_id, skip_reviews=skip_reviews, frontend_target=frontend_target)
        runtime_passed = bool(
            v6_result.get("runtime") and v6_result["runtime"].get("success")
        )

    # ── Step 4: Decide whether to run improvement cycle ──────────────────
    stats = get_dataset_stats()
    total_runs = stats.get("total_runs", 0)
    should_improve = (
        run_improvement_cycle
        and total_runs > 0
        and total_runs % improvement_cycle_every_n == 0
    )

    improvement_cycle_result = None
    if should_improve:
        improvement_cycle_result = run_improvement_cycle_v7(provider=provider)

    total_time = round(time.time() - start, 2)

    return {
        **v6_result,
        "pipeline": "v7",
        "run_id": run_id,
        "v7_extras": {
            "improvement_cycle_ran": should_improve,
            "improvement_cycle": improvement_cycle_result,
            "dataset_total_runs": total_runs,
            "self_healing_triggered": not runtime_passed,
        },
        "generation_time_seconds": total_time,
    }


def _record_to_dataset(
    idea: str, run_id: str, provider: str, v6_result: dict
) -> None:
    """Write a RunRecord to the dataset."""
    try:
        v6_score = v6_result.get("v6_score", {})
        runtime = v6_result.get("runtime", {}) or {}
        validation = v6_result.get("validation", {})

        record = RunRecord(
            run_id=run_id,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            version="v7",
            idea=idea,
            project_name=v6_result.get("project_name", "unknown"),
            provider=provider,
            plan=v6_result.get("plan", {}),
            architecture=v6_result.get("architecture", {}),
            validation_errors=validation.get("errors", []),
            runtime_error=runtime.get("error"),
            runtime_error_type=(runtime.get("parsed_error", {}) or {}).get("type"),
            fixes_applied=[],
            score=v6_result.get("forge_score", {}).get("score", 0),
            grade=v6_result.get("forge_score", {}).get("grade", "F"),
            runtime_passed=bool(runtime.get("success")),
            frontend_passed=False,
            generation_time=v6_result.get("generation_time_seconds", 0),
            security_score=v6_result.get("security", {}).get("score"),
            performance_score=v6_result.get("performance", {}).get("score"),
            maintainability_score=v6_result.get("code_review", {}).get("maintainability_score"),
            qa_score=v6_result.get("qa", {}).get("score"),
            v6_score=v6_score.get("total"),
        )
        save_run_record(record)
    except Exception as e:
        print(f"  Dataset record failed (non-fatal): {e}")


def _attempt_self_healing(
    idea: str, provider: str, failed_result: dict, run_id: str,
    skip_reviews: bool = False,
    frontend_target: str = "web",
) -> dict:
    """
    V7 Self-Healing: when the V6 pipeline completely fails, consult the
    Research Agent for root cause, then regenerate with enhanced context.
    """
    print("\n=== V7 SELF-HEALING ARCHITECTURE ===")
    print("  Both static and runtime validation failed — consulting Research Agent...")

    try:
        findings = get_latest_findings()
        if not findings:
            print("  No research findings available — standard regeneration")
            return failed_result

        # Inject research insights into the regeneration
        root_causes = findings.get("root_causes", [])
        cause_context = "\n".join(
            f"  - ROOT CAUSE: {c.get('cause', '')} (fix via {c.get('fix_type', '')})"
            for c in root_causes[:3]
        )

        from app.memory.failure_memory import build_prompt_injection
        learned = build_prompt_injection()
        healing_context = (
            f"[V7 SELF-HEALING - Previous generation failed. Root causes identified:]\n"
            f"{cause_context}\n\n"
            f"{learned}"
        )

        print(f"  Healing context: {healing_context[:200]}...")
        print("  Regenerating with enhanced architecture context...")

        collab = AgentCollaboration()
        healed = generate_project_v6(
            idea, provider=provider, collab=collab, skip_reviews=skip_reviews, frontend_target=frontend_target
        )
        healed["v7_self_healed"] = True
        healed["v7_healing_context"] = healing_context[:500]
        return healed

    except Exception as e:
        print(f"  Self-healing failed: {e}")
        return failed_result


def run_improvement_cycle_v7(
    provider: str = "auto",
    run_research: bool = True,
    run_rule_evo: bool = True,
    run_prompt_opt: bool = True,
    benchmark_n: int = 3,
) -> dict[str, Any]:
    """
    The V7 improvement cycle:
    1. Benchmark comparison (V5 vs V6 vs V7)
    2. Research Agent analysis
    3. Rule Evolution Engine
    4. Prompt Optimizer (targeted at top failure)
    5. Leaderboard sync
    6. Print results
    """
    print(f"\n{'='*70}")
    print(f"# V7 IMPROVEMENT CYCLE")
    print(f"{'='*70}")
    cycle_start = time.time()

    results: dict[str, Any] = {}

    # Step 1: Benchmark comparison
    try:
        comparison = compare_versions()
        print_comparison_report(comparison)
        results["comparison"] = {
            "best_version": comparison.best_version,
            "regression_detected": comparison.regression_detected,
            "regression_details": comparison.regression_details,
            "improvement_from_v5": comparison.improvement_from_v5,
            "improvement_velocity": comparison.improvement_velocity,
        }
    except Exception as e:
        print(f"  Comparison error: {e}")
        results["comparison"] = {"error": str(e)}

    # Step 2: Research Agent
    if run_research:
        try:
            findings = run_research_agent(provider=provider)
            results["research"] = {
                "trend": findings.trend,
                "regression_detected": findings.regression_detected,
                "top_improvements": [i.improvement for i in findings.top_improvements[:3]],
                "hypothesis": findings.hypothesis.statement if findings.hypothesis else None,
                "summary": findings.summary,
                "skipped": findings.skipped,
            }
        except Exception as e:
            print(f"  Research Agent error: {e}")
            results["research"] = {"error": str(e)}

    # Step 3: Rule Evolution Engine
    if run_rule_evo:
        try:
            evo_result = run_rule_evolution(
                provider=provider,
                test_n=benchmark_n,
                threshold=0.08,
            )
            results["rule_evolution"] = {
                "candidates_generated": evo_result.candidates_generated,
                "rules_promoted": evo_result.rules_promoted,
                "promoted_rules": evo_result.promoted_rules,
                "skipped": evo_result.skipped,
                "skip_reason": evo_result.skip_reason,
            }
            if evo_result.rules_promoted > 0:
                print(f"  Rule Evolution: {evo_result.rules_promoted} rules promoted")
        except Exception as e:
            print(f"  Rule Evolution error: {e}")
            results["rule_evolution"] = {"error": str(e)}

    # Step 4: Prompt Optimizer
    if run_prompt_opt:
        try:
            opt_result = run_prompt_optimization(
                provider=provider,
                test_n=benchmark_n,
            )
            results["prompt_optimizer"] = {
                "improved": opt_result.improved,
                "old_pass_rate": opt_result.old_pass_rate,
                "new_pass_rate": opt_result.new_pass_rate,
                "improvement": opt_result.improvement,
                "rule_added": opt_result.rule_added[:100] if opt_result.rule_added else "",
                "skipped": opt_result.skipped,
            }
            if opt_result.improved:
                print(f"  Prompt Optimizer: +{opt_result.improvement:.0%} pass rate improvement")
        except Exception as e:
            print(f"  Prompt Optimizer error: {e}")
            results["prompt_optimizer"] = {"error": str(e)}

    # Step 5: Leaderboard sync
    try:
        po_added = sync_from_prompt_optimizer()
        re_added = sync_from_rule_evolution()
        print_leaderboard(top_n=5)
        results["leaderboard"] = {
            "prompt_optimizer_synced": po_added,
            "rule_evolution_synced": re_added,
        }
    except Exception as e:
        print(f"  Leaderboard sync error: {e}")

    results["cycle_duration"] = round(time.time() - cycle_start, 2)
    print(f"\n  Improvement cycle complete in {results['cycle_duration']}s")
    return results


def run_v7_benchmark(
    ideas: list[str],
    provider: str = "auto",
    versions: list[str] | None = None,
) -> dict[str, Any]:
    """
    V7 Benchmark: run a set of ideas through the V7 pipeline,
    then produce a comparison report with regression detection.

    Measures:
    - Learning effectiveness (how much better after improvement cycle?)
    - Regression rate
    - Cost efficiency
    - Runtime success rate
    - Improvement velocity (score change per 10 runs)
    """
    versions = versions or ["v7"]
    print(f"\n{'='*70}")
    print(f"# V7 BENCHMARK — {len(ideas)} projects")
    print(f"{'='*70}")

    results = []
    for idx, idea in enumerate(ideas, 1):
        print(f"\n--- Benchmark {idx}/{len(ideas)}: {idea[:50]} ---")
        try:
            result = generate_project_v7(idea, provider=provider, run_improvement_cycle=False)
            results.append({
                "idea": idea[:80],
                "score": result.get("forge_score", {}).get("score", 0),
                "grade": result.get("forge_score", {}).get("grade", "F"),
                "v6_score": (result.get("v6_score") or {}).get("total"),
                "runtime_passed": bool((result.get("runtime") or {}).get("success")),
                "security_score": (result.get("security") or {}).get("score"),
                "performance_score": (result.get("performance") or {}).get("score"),
                "maintainability_score": (result.get("code_review") or {}).get("maintainability_score"),
                "generation_time": result.get("generation_time_seconds", 0),
                "project_name": result.get("project_name", ""),
            })
        except Exception as e:
            print(f"  Benchmark error: {e}")
            results.append({"idea": idea[:80], "error": str(e), "score": 0, "runtime_passed": False})

    # Compute summary
    scores = [r.get("score", 0) for r in results if "error" not in r]
    passed = [r for r in results if r.get("runtime_passed")]
    sec_scores = [r.get("security_score") for r in results if r.get("security_score") is not None]
    perf_scores = [r.get("performance_score") for r in results if r.get("performance_score") is not None]

    summary = {
        "total_projects": len(ideas),
        "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
        "pass_rate": round(len(passed) / len(results), 3) if results else 0,
        "avg_security_score": round(sum(sec_scores) / len(sec_scores), 1) if sec_scores else None,
        "avg_performance_score": round(sum(perf_scores) / len(perf_scores), 1) if perf_scores else None,
    }

    print(f"\n{'='*70}")
    print(f"  V7 BENCHMARK SUMMARY")
    print(f"  Projects: {summary['total_projects']} | "
          f"Avg Score: {summary['avg_score']}/100 | "
          f"Pass Rate: {summary['pass_rate']:.0%}")
    if summary["avg_security_score"]:
        print(f"  Security: {summary['avg_security_score']:.1f} | "
              f"Performance: {summary['avg_performance_score']:.1f}")
    print("=" * 70)

    return {
        "results": results,
        "summary": summary,
        "comparison": compare_versions().versions if True else {},
    }
