"""
V15 Autonomous Self-Healing Pipeline

Architecture:
  1. Generate (V6: plan → architect → backend → frontend)
  2. Deterministic patches (param order, router names, auth, database)
  3. Verification (static → runtime → browser)
  4. Scoring (0–100 across 10 dimensions)
  5. Fix loop (up to 5 attempts with escalation) if score < 95
  6. Deploy (Cloudflare + Render) only if score ≥ 95
  7. Observability: full timeline + token usage + cost

Design principle: NEVER assume code works. Always execute → verify → score → repair.
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.core.context import (
    GenerationContext, QualityScore, StageStatus,
)
from app.core.events import EventBus, Events, get_default_bus, log_handler
from app.repair.orchestrator import FixOrchestrator
from app.retry.manager import RetryManager
from app.scoring.engine import ScoringEngine
from app.verification.engine import VerificationEngine


class V15Pipeline:
    """
    Plugin-based autonomous generation pipeline.

    All major subsystems are injected and can be swapped:
      - verification_engine: VerificationEngine (or subclass)
      - scoring_engine:      ScoringEngine (or subclass)
      - fix_orchestrator:    FixOrchestrator (or subclass)
      - retry_manager:       RetryManager (or subclass)
      - event_bus:           EventBus

    Adding a new verifier:
        pipeline.verification_engine.register(my_custom_verifier)

    Adding a new score dimension:
        pipeline.scoring_engine.add_dimension(my_dim_fn)
    """

    def __init__(
        self,
        verification_engine: Optional[VerificationEngine] = None,
        scoring_engine:      Optional[ScoringEngine]      = None,
        fix_orchestrator:    Optional[FixOrchestrator]    = None,
        retry_manager:       Optional[RetryManager]       = None,
        event_bus:           Optional[EventBus]            = None,
        deploy:              bool                          = True,
        deploy_to:           str                           = "both",  # "render"|"cloudflare"|"both"
    ):
        self.verification_engine = verification_engine or VerificationEngine()
        self.scoring_engine      = scoring_engine      or ScoringEngine()
        self.fix_orchestrator    = fix_orchestrator    or FixOrchestrator(
            self.verification_engine, self.scoring_engine
        )
        self.retry_manager       = retry_manager       or RetryManager(max_attempts=5)
        self.bus                 = event_bus            or get_default_bus()
        self.deploy              = deploy
        self.deploy_to           = deploy_to

        # Attach default console logger
        self.bus.on("*", log_handler)

    # ── Public entry point ────────────────────────────────────────────────────

    def run(
        self,
        idea:         str,
        provider:     str = "auto",
        output_dir:   Optional[str] = None,
        job_id:       Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Run the full V15 pipeline.

        Returns a summary dict compatible with the existing job API response format.
        """
        job_id = job_id or uuid.uuid4().hex

        # ── Stage 1: Generate ─────────────────────────────────────────────
        self.bus.emit(Events.STAGE_START, {"stage": "generation"})
        gen_start = time.time()

        # Smart model routing: choose provider based on idea complexity
        from app.providers.model_router import route as model_route
        routing = model_route(idea, stage="planning", provider=provider)
        effective_provider = routing.provider
        print(f"[V15] Model router: {routing.reasoning}")

        project_path, project_name, v6_result = self._generate(
            idea=idea,
            provider=effective_provider,
            output_dir=output_dir,
        )

        if project_path is None:
            self.bus.emit(Events.PIPELINE_DONE, {"status": "generation_failed"})
            return {"status": "error", "error": "Generation failed", "job_id": job_id}

        ctx = GenerationContext(
            job_id=job_id,
            idea=idea,
            project_path=project_path,
            project_name=project_name,
        )
        ctx.plan         = v6_result.get("plan")
        ctx.architecture = v6_result.get("architecture")
        ctx.current_provider = provider

        # Propagate token usage from V6
        v6_tokens = v6_result.get("token_usage", {})
        if v6_tokens:
            ctx.token_usage.add(
                "generation",
                v6_tokens.get("prompt_tokens", 0),
                v6_tokens.get("completion_tokens", 0),
                v6_tokens.get("estimated_cost_usd", 0.0),
            )

        gen_evt = ctx.begin_stage("generation")
        ctx.end_stage(gen_evt, StageStatus.PASSED,
                      duration_ms=(time.time() - gen_start) * 1000)
        self.bus.emit(Events.STAGE_END, {
            "stage": "generation", "status": "passed",
            "duration_ms": (time.time() - gen_start) * 1000,
        })

        # ── Stage 2: Deterministic patches ───────────────────────────────
        self._deterministic_patch(ctx)

        # ── Stage 3: Initial verification + scoring ───────────────────────
        attempt = 0
        self._verify_and_score(ctx, attempt)

        self.bus.emit(Events.SCORE_UPDATE, {
            "score": ctx.latest_score,
            "grade": ctx.current_score.grade if ctx.current_score else "F",
            "deployment_ready": ctx.is_deployment_ready,
        })

        # ── Stage 4: Fix loop (up to 5 attempts) ─────────────────────────
        rm = self.retry_manager

        # Early-stop: after N consecutive attempts with no score improvement,
        # stop escalating. Attempts 3-5 (module regen, model switch, full
        # architecture regen) are the most expensive LLM calls in the whole
        # pipeline, and the benchmark history shows that when attempts 1-2
        # both fail to move the score, the later attempts almost never do
        # either -- they just burn API credits.
        max_stalled = int(os.environ.get("FORGE_MAX_STALLED_FIX_ATTEMPTS", "2"))
        stalled = 0

        while not ctx.is_deployment_ready and not rm.exhausted:
            if stalled >= max_stalled:
                print(f"[V15] {stalled} consecutive fix attempts with no score "
                      f"improvement — stopping fix loop to save API credits "
                      f"(set FORGE_MAX_STALLED_FIX_ATTEMPTS to change)")
                break
            cfg = rm.next_strategy(ctx)
            if cfg is None:
                break

            self.bus.emit(Events.FIX_ATTEMPT, {
                "attempt": cfg.attempt,
                "strategy": cfg.strategy.value,
                "provider": cfg.provider,
            })

            fix_attempt = self.fix_orchestrator.run_attempt(ctx, cfg)
            rm.record_result(cfg, fix_attempt.score_before, fix_attempt.score_after or 0)

            if (fix_attempt.score_after or 0) > fix_attempt.score_before:
                stalled = 0
            else:
                stalled += 1

            self.bus.emit(Events.SCORE_UPDATE, {
                "score": ctx.latest_score,
                "grade": ctx.current_score.grade if ctx.current_score else "F",
                "deployment_ready": ctx.is_deployment_ready,
            })

            if fix_attempt.regression_detected:
                self.bus.emit(Events.REGRESSION, {
                    "attempt": cfg.attempt,
                    "score_delta": (fix_attempt.score_after or 0) - fix_attempt.score_before,
                })

            self.bus.emit(Events.FIX_DONE, {
                "attempt": cfg.attempt,
                "score_before": fix_attempt.score_before,
                "score_after":  fix_attempt.score_after,
                "files_modified": len(fix_attempt.files_modified),
            })

        # ── Stage 5: Deploy (only if score ≥ 95) ─────────────────────────
        deploy_result: dict = {"skipped": True}
        if ctx.is_deployment_ready and self.deploy:
            self.bus.emit(Events.DEPLOY_START, {
                "score": ctx.latest_score,
                "deploy_to": self.deploy_to,
            })
            deploy_result = self._deploy(ctx)
            ctx.deployment_result = deploy_result
            self.bus.emit(Events.DEPLOY_DONE, {
                "success":      deploy_result.get("success"),
                "backend_url":  deploy_result.get("backend_url"),
                "frontend_url": deploy_result.get("frontend_url"),
            })
        elif not ctx.is_deployment_ready:
            best = rm.best_score()
            print(f"\n[V15] Score {ctx.latest_score:.1f} < 95 — deployment skipped")
            print(f"[V15] Best score across attempts: {best:.1f}")
            deploy_result = {
                "skipped": True,
                "reason":  f"Score {ctx.latest_score:.1f} did not reach deployment threshold (95)",
            }

        self.bus.emit(Events.PIPELINE_DONE, {
            "status":       "deployed" if deploy_result.get("success") else "generated",
            "score":        ctx.latest_score,
            "fix_attempts": len(ctx.fix_attempts),
        })

        # ── Deployment confidence ──────────────────────────────────────────
        try:
            from app.confidence.engine import compute_from_context
            confidence = compute_from_context(ctx)
            confidence.display()
        except Exception:
            confidence = None

        # ── Record this run to the generation log ─────────────────────────
        try:
            from app.knowledge.failure_db import generation_log, GenerationRecord
            import hashlib
            arch_hash = hashlib.sha256(str(ctx.architecture)[:500].encode()).hexdigest()[:12]
            all_diags = getattr(ctx, "all_diagnostics", [])
            generation_log.record(GenerationRecord(
                idea=ctx.idea[:200],
                attempt_number=len(ctx.fix_attempts),
                final_score=ctx.latest_score,
                succeeded=ctx.is_deployment_ready,
                fix_count=len(ctx.fix_attempts),
                dominant_errors=[d.message[:80] for d in all_diags
                                  if getattr(d, "severity", None) and
                                  d.severity.value in ("critical", "high")][:5],
                architecture_hash=arch_hash,
            ))
        except Exception:
            pass

        # ── Record to Architecture Database if high-quality ───────────────
        try:
            from app.knowledge.arch_db import arch_db
            if ctx.is_deployment_ready and ctx.architecture and ctx.plan:
                arch = ctx.architecture.dict() if hasattr(ctx.architecture, "dict") else ctx.architecture
                plan = ctx.plan.dict() if hasattr(ctx.plan, "dict") else ctx.plan
                if isinstance(arch, dict) and isinstance(plan, dict):
                    arch_db.record(
                        idea=ctx.idea,
                        architecture=arch,
                        plan=plan,
                        score=ctx.latest_score,
                        generation_id=ctx.job_id,
                    )
        except Exception:
            pass

        # ── Build API response ─────────────────────────────────────────────
        return {
            **ctx.to_summary(),
            "v6_result":       v6_result,
            "retry_history":   rm.summary(),
            "deployment":      deploy_result,
            "project_path":    str(ctx.project_path),
            "status":          "done",
            "confidence":      confidence.__dict__ if confidence else None,
        }

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _generate(
        self,
        idea:       str,
        provider:   str,
        output_dir: Optional[str],
    ) -> tuple[Optional[Path], str, dict]:
        """
        Run V6 generation. If the Architecture Database has a proven template
        for this idea's category, inject it into the generation as a starting point.
        """
        # ── Architecture Database lookup ──────────────────────────────────
        arch_template_injection = ""
        try:
            from app.knowledge.arch_db import arch_db
            template = arch_db.lookup(idea, min_score=88.0)
            if template:
                arch_template_injection = arch_db.build_template_injection(template)
                print(f"[V15] Arch DB hit: reusing {template.category} template "
                      f"(score={template.score:.0f}, used {template.success_count}x)")
        except Exception as exc:
            print(f"[V15] Arch DB lookup skipped: {exc}")

        try:
            from app.services.v6_orchestrator import generate_project_v6
            kwargs: dict[str, Any] = {"idea": idea, "provider": provider}
            if output_dir:
                # generate_project_v6 has no output-path override — it always
                # writes under generated_projects/<project_name>.
                print(f"[V15] output_dir requested but not supported by v6_orchestrator — ignoring: {output_dir}")
            if arch_template_injection:
                kwargs["arch_template"] = arch_template_injection

            result = generate_project_v6(**kwargs)
            project_path_str = result.get("project_path") or ""
            project_name     = result.get("project_name") or "generated_project"
            if not project_path_str:
                return None, project_name, result
            return Path(project_path_str), project_name, result
        except Exception as exc:
            print(f"[V15] Generation error: {exc}")
            return None, "", {}

    def _deterministic_patch(self, ctx: GenerationContext):
        """Apply all deterministic patches before first verification."""
        evt = ctx.begin_stage("deterministic-patch")
        try:
            from app.services.deterministic_patcher import run_deterministic_patches
            from app.services.database_patcher import (
                patch_database_py, patch_model_field_mismatches, patch_add_missing_model_columns,
            )
            run_deterministic_patches(str(ctx.project_path))
            patch_database_py(str(ctx.project_path))
            patch_model_field_mismatches(str(ctx.project_path))
            patch_add_missing_model_columns(str(ctx.project_path))
            # Preflight registry: deterministic fixes that don't need LLM
            from app.repair.preflight import preflight
            preflight.run(ctx.project_path)
            ctx.end_stage(evt, StageStatus.PASSED)
        except Exception as exc:
            print(f"[V15] Deterministic patcher warning: {exc}")
            ctx.end_stage(evt, StageStatus.FAILED, error=str(exc))

    def _verify_and_score(self, ctx: GenerationContext, attempt: int):
        """Run full verification and compute score, updating ctx."""
        evt = ctx.begin_stage("verification")
        self.verification_engine.run(ctx)
        ctx.end_stage(evt, StageStatus.PASSED)  # always "runs"; individual stages track their own status

        score = self.scoring_engine.score(ctx, attempt_number=attempt)
        self.scoring_engine.print_report(score)

    def _deploy(self, ctx: GenerationContext) -> dict:
        """Deploy to Render (backend) + Cloudflare (frontend) via V14 helpers."""
        try:
            from app.services.v14_orchestrator import (
                _deploy_render,
                _deploy_cloudflare,
                _run_health_checks,
            )
            # Deploy backend
            render_result    = _deploy_render(str(ctx.project_path), ctx.project_name, ctx.idea)
            backend_url      = render_result.get("backend_url") or render_result.get("url")

            # Deploy frontend
            cloudflare_result = {"skipped": True}
            if self.deploy_to in ("cloudflare", "both"):
                cloudflare_result = _deploy_cloudflare(
                    str(ctx.project_path), ctx.project_name, backend_url
                )

            frontend_url = cloudflare_result.get("url")
            health       = _run_health_checks(backend_url, frontend_url)

            return {
                "success":      bool(backend_url),
                "backend_url":  backend_url,
                "frontend_url": frontend_url,
                "render":       render_result,
                "cloudflare":   cloudflare_result,
                "health":       health,
            }
        except Exception as exc:
            print(f"[V15] Deploy error: {exc}")
            return {"success": False, "error": str(exc)}
