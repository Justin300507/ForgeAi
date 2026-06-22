"""
V12 — Continuous Product Evolution

Pipeline:
  Generate + Deploy (V11)
      ↓
  Collect Live Metrics
      ↓
  LLM Evolution Analysis
      ↓
  (if issues detected) Regenerate with improvement context
      ↓
  Redeploy improved version
      ↓
  Return: v1 URL + v2 URL + evolution summary + metric delta

This is the first version that autonomously improves a live product.
"""
import time
from typing import Any

from app.services.v11_orchestrator import generate_project_v11
from app.services.metrics_collector import collect_metrics
from app.services.evolution_service import generate_evolution_plan, build_evolved_idea


def generate_project_v12(
    idea: str,
    provider: str = "auto",
    deploy_provider: str = "railway",
    run_improvement_cycle: bool = False,
    skip_reviews: bool = True,
    metrics_requests: int = 5,
    skip_evolution: bool = False,
) -> dict[str, Any]:
    """
    V12 pipeline: generate → deploy → measure → evolve → redeploy.

    Args:
        idea:              plain-English project description
        provider:          LLM provider for generation
        deploy_provider:   deployment target (railway/render/flyio)
        run_improvement_cycle: run V7 rule evolution after generation
        skip_reviews:      skip QA/Security/Code/Performance reviews
        metrics_requests:  number of probe requests per endpoint for metrics
        skip_evolution:    if True, skip metrics collection and evolution (V11 only)
    """
    start = time.time()

    print(f"\n{'#'*70}")
    print(f"# V12 CONTINUOUS PRODUCT EVOLUTION")
    print(f"# Idea: {idea[:60]}")
    print(f"# LLM: {provider} | Deploy: {deploy_provider}")
    print(f"{'#'*70}")

    # ── Step 1: Generate + Deploy v1 with V11 ────────────────────────────
    print("\n[V12] Phase 1 — Generate and deploy initial version...")
    v1_result = generate_project_v11(
        idea=idea,
        provider=provider,
        deploy_provider=deploy_provider,
        run_improvement_cycle=run_improvement_cycle,
        skip_reviews=skip_reviews,
    )

    v1_url = v1_result.get("live_url")
    architecture = v1_result.get("architecture", {})

    evolution: dict[str, Any] = {"skipped": True, "reason": "not attempted"}
    metrics: dict[str, Any] = {"skipped": True}
    v2_result: dict[str, Any] | None = None

    if skip_evolution:
        evolution["reason"] = "skip_evolution=True"
    elif not v1_url:
        evolution["reason"] = "v1 deployment failed — no URL to measure"
        print("\n[V12] Skipping evolution — v1 deployment failed")
    else:
        # ── Step 2: Collect live metrics ─────────────────────────────────
        print(f"\n[V12] Phase 2 — Collecting live metrics from {v1_url}...")
        metrics = collect_metrics(
            url=v1_url,
            requests_per_endpoint=metrics_requests,
        )
        print(f"  Availability: {metrics['availability']:.0%}  "
              f"Avg: {metrics['avg_response_ms']:.0f}ms  "
              f"Errors: {metrics['error_rate']:.0%}")
        if metrics["issues"]:
            print(f"  Issues: {', '.join(metrics['issues'])}")

        # ── Step 3: LLM evolution analysis ───────────────────────────────
        print("\n[V12] Phase 3 — Running evolution analysis...")
        plan = generate_evolution_plan(
            idea=idea,
            metrics=metrics,
            architecture=architecture,
            provider=provider,
        )
        evolution = {
            "skipped": False,
            "plan": plan,
            "triggered_regen": False,
        }

        # ── Step 4: Regenerate + redeploy if issues found ─────────────────
        if plan.get("needs_improvement") and plan.get("regeneration_context"):
            print("\n[V12] Phase 4 — Regenerating improved version...")
            evolved_idea = build_evolved_idea(idea, plan)
            v2_result = generate_project_v11(
                idea=evolved_idea,
                provider=provider,
                deploy_provider=deploy_provider,
                run_improvement_cycle=False,
                skip_reviews=skip_reviews,
            )
            v2_url = v2_result.get("live_url")
            evolution["triggered_regen"] = True
            evolution["v2_url"] = v2_url

            if v2_url:
                print(f"\n[V12] Collecting v2 metrics for comparison...")
                v2_metrics = collect_metrics(url=v2_url, requests_per_endpoint=metrics_requests)
                evolution["v2_metrics"] = v2_metrics
                evolution["improvement_delta"] = _compute_delta(metrics, v2_metrics)
                print(f"  v1 avg: {metrics['avg_response_ms']:.0f}ms → "
                      f"v2 avg: {v2_metrics['avg_response_ms']:.0f}ms")
        else:
            print("\n[V12] Application performing well — no regeneration needed")

    elapsed = round(time.time() - start, 2)
    final_url = (
        evolution.get("v2_url")
        or v1_url
    )

    return {
        **v1_result,
        "pipeline": "v12",
        "live_url": final_url,
        "v12_extras": {
            "v1_url": v1_url,
            "v2_url": evolution.get("v2_url"),
            "final_url": final_url,
            "metrics": metrics,
            "evolution": evolution,
            "generation_count": 2 if evolution.get("triggered_regen") else 1,
        },
        "generation_time_seconds": elapsed,
    }


def _compute_delta(m1: dict, m2: dict) -> dict:
    """Compare two metrics dicts and return improvement delta."""
    def pct_change(old, new):
        if not old:
            return 0
        return round((new - old) / old * 100, 1)

    return {
        "availability_delta":   round(m2["availability"] - m1["availability"], 3),
        "avg_ms_delta":         round(m2["avg_response_ms"] - m1["avg_response_ms"], 1),
        "avg_ms_pct_change":    pct_change(m1["avg_response_ms"], m2["avg_response_ms"]),
        "error_rate_delta":     round(m2["error_rate"] - m1["error_rate"], 3),
        "issues_before":        len(m1.get("issues", [])),
        "issues_after":         len(m2.get("issues", [])),
        "improved":             m2["avg_response_ms"] < m1["avg_response_ms"] or
                                m2["error_rate"] < m1["error_rate"],
    }
