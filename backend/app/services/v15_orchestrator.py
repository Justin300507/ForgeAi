"""
V15 Orchestrator — top-level entry point for the autonomous self-healing pipeline.

Exposes:
  generate_project_v15(idea, provider, deploy, deploy_to) → dict

Wires together the V15Pipeline with:
  - VerificationEngine (static + runtime + browser)
  - ScoringEngine (10 dimensions, 95-gate)
  - FixOrchestrator (holistic, regression-protected)
  - RetryManager (5-level escalation)
  - Event bus (console + WebSocket streaming)
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from app.core.context import GenerationContext
from app.core.events import EventBus, Events
from app.core.pipeline import V15Pipeline
from app.repair.orchestrator import FixOrchestrator
from app.retry.manager import RetryManager
from app.scoring.engine import ScoringEngine
from app.verification.engine import VerificationEngine


def generate_project_v15(
    idea:       str,
    provider:   str = "auto",
    deploy:     bool = True,
    deploy_to:  str = "both",    # "render" | "cloudflare" | "both"
    job_id:     Optional[str] = None,
    log_fn:     Optional[Any] = None,  # callable(str) for WebSocket streaming
) -> dict[str, Any]:
    """
    Run the full V15 autonomous generation pipeline.

    Pipeline:
      1. V6 generation (plan → architect → backend → frontend)
      2. Deterministic patches
      3. Verification (static → runtime → browser/playwright)
      4. Quality scoring (0–100, 10 dimensions)
      5. Fix loop (up to 5 attempts, escalating strategy)
      6. Deploy if score ≥ 95
      7. Return structured result with full observability

    Args:
        idea:      Plain-English project description
        provider:  LLM provider ("auto" | "cerebras" | "groq" | "openrouter" | "gemini")
        deploy:    Whether to deploy to cloud (Render + Cloudflare)
        deploy_to: Which cloud targets ("both" | "render" | "cloudflare")
        job_id:    Optional job identifier for tracking
        log_fn:    Optional streaming callback (WebSocket broadcast)

    Returns:
        {
            "status":          "done" | "error"
            "job_id":          str
            "project_name":    str
            "forge_score":     float (0-100)
            "grade":           str ("A+" | "A" | ... | "F")
            "deployed":        bool
            "backend_url":     str | None
            "frontend_url":    str | None
            "fix_attempts":    int
            "total_tokens":    int
            "estimated_cost":  float (USD)
            "elapsed_s":       float
            "score_history":   list[{attempt, score, grade}]
            "timeline":        list[{stage, status, duration_ms}]
            "retry_history":   list[{attempt, strategy, score_before, score_after, delta}]
        }
    """
    start   = time.time()
    job_id  = job_id or uuid.uuid4().hex

    print(f"\n{'#'*70}")
    print(f"# FORGEAI V15 — Autonomous Self-Healing Platform")
    print(f"# Idea:   {idea[:65]}")
    print(f"# Job ID: {job_id}")
    print(f"# Deploy: {deploy} → {deploy_to}")
    print(f"{'#'*70}")

    # ── Build event bus with optional WebSocket streaming ─────────────────
    bus = EventBus()

    if log_fn:
        def _stream(payload: dict):
            event = payload.get("event", "")
            if event == Events.STAGE_START:
                log_fn(f"[V15] ▶ {payload.get('stage','?')} …")
            elif event == Events.STAGE_END:
                log_fn(f"[V15] {'✓' if payload.get('status')=='passed' else '✕'} "
                       f"{payload.get('stage','?')} [{payload.get('status','?')}]")
            elif event == Events.SCORE_UPDATE:
                log_fn(f"[V15] Score: {payload.get('score',0):.1f} ({payload.get('grade','?')})"
                       + (" 🚀 DEPLOY READY" if payload.get('deployment_ready') else " 🔧 needs repair"))
            elif event == Events.FIX_ATTEMPT:
                log_fn(f"[V15] Fix attempt {payload.get('attempt','?')}/5 — {payload.get('strategy','?')}")
            elif event == Events.DEPLOY_DONE:
                log_fn(f"[V15] Deployed! Backend: {payload.get('backend_url','?')}")
            elif event == Events.PIPELINE_DONE:
                log_fn(f"[V15] Pipeline complete: {payload.get('status','?')} score={payload.get('score',0):.1f}")
        bus.on("*", _stream)

    # ── Build pipeline with injected components ───────────────────────────
    verification_engine = VerificationEngine(run_runtime=True, run_browser=True)
    scoring_engine      = ScoringEngine()
    fix_orchestrator    = FixOrchestrator(verification_engine, scoring_engine)
    retry_manager       = RetryManager(max_attempts=5)

    pipeline = V15Pipeline(
        verification_engine = verification_engine,
        scoring_engine      = scoring_engine,
        fix_orchestrator    = fix_orchestrator,
        retry_manager       = retry_manager,
        event_bus           = bus,
        deploy              = deploy,
        deploy_to           = deploy_to,
    )

    # ── Run ───────────────────────────────────────────────────────────────
    result = pipeline.run(idea=idea, provider=provider, job_id=job_id)
    result["elapsed_s"] = round(time.time() - start, 2)

    _print_final_report(result)
    return result


def _print_final_report(result: dict):
    """Print a concise deployment summary."""
    score    = result.get("forge_score", 0)
    grade    = result.get("grade", "F")
    deployed = result.get("deployed", False)
    elapsed  = result.get("elapsed_s", 0)
    fixes    = result.get("fix_attempts", 0)
    cost     = result.get("estimated_cost", 0)
    tokens   = result.get("total_tokens", 0)

    print(f"\n{'='*70}")
    print(f"  V15 FINAL REPORT")
    print(f"{'='*70}")
    print(f"  Project:       {result.get('project_name','?')}")
    print(f"  Forge Score:   {score:.1f}/100  ({grade})")
    print(f"  Deployed:      {'YES' if deployed else 'NO'}")
    if result.get("backend_url"):
        print(f"  Backend URL:   {result['backend_url']}")
    if result.get("frontend_url"):
        print(f"  Frontend URL:  {result['frontend_url']}")
    print(f"  Fix Attempts:  {fixes}/5")
    print(f"  Total Tokens:  {tokens:,}")
    print(f"  Est. Cost:     ${cost:.4f}")
    print(f"  Total Time:    {elapsed}s")

    score_hist = result.get("score_history", [])
    if score_hist:
        trajectory = " → ".join(f"{s['score']:.0f}" for s in score_hist)
        print(f"  Score Track:   {trajectory}")

    print(f"{'='*70}\n")
