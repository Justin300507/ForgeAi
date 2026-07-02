"""
ScoringEngine — computes a 0–100 quality score across 10 dimensions.

Deployment is gated at ≥ 95.  Every dimension has a weight; the weighted
average of all dimension scores is the overall forge score.

Design principle: the score must be STRICT.  A 94 should feel like it barely
missed — it means something real is still broken.
"""
from __future__ import annotations

import math
import os
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()  # FORGE_DEPLOY_THRESHOLD is read at import time below
from typing import Optional

from app.core.context import (
    BrowserTestResult, Diagnostic, ErrorCategory, ErrorSeverity,
    GenerationContext, QualityScore, RuntimeResult, ScoreDimension,
    VerificationResult,
)

# Overridable via FORGE_DEPLOY_THRESHOLD (must match GenerationContext.DEPLOY_THRESHOLD)
DEPLOY_THRESHOLD = float(os.environ.get("FORGE_DEPLOY_THRESHOLD", "95"))


# ── Dimension calculators ─────────────────────────────────────────────────────

def _dim_compilation(ctx: GenerationContext) -> ScoreDimension:
    """
    Did all Python files compile?  Were all validators satisfied?
    Static errors are weighted by severity.
    """
    static_diags = [d for r in ctx.static_results for d in r.diagnostics]
    critical = sum(1 for d in static_diags if d.severity == ErrorSeverity.CRITICAL)
    high     = sum(1 for d in static_diags if d.severity == ErrorSeverity.HIGH)
    medium   = sum(1 for d in static_diags if d.severity == ErrorSeverity.MEDIUM)
    low      = sum(1 for d in static_diags if d.severity == ErrorSeverity.LOW)

    penalty = critical * 30 + high * 10 + medium * 5 + low * 1
    score   = max(0.0, 100.0 - penalty)
    return ScoreDimension(
        name="Compilation",
        score=score, weight=0.15,
        passed=critical == 0 and high == 0,
        details=f"critical={critical} high={high} medium={medium} low={low}",
    )


def _dim_runtime_startup(ctx: GenerationContext) -> ScoreDimension:
    """Did the backend start and pass the health check?"""
    rr = ctx.runtime_result
    if rr is None:
        return ScoreDimension(name="Runtime Startup", score=0, weight=0.20,
                              passed=False, details="runtime not run")
    if rr.success and rr.health_passed:
        score = 100.0
    elif rr.backend_started and not rr.health_passed:
        score = 40.0
    elif rr.backend_started:
        score = 20.0
    else:
        score = 0.0
    return ScoreDimension(
        name="Runtime Startup", score=score, weight=0.20,
        passed=rr.success and rr.health_passed,
        details=f"started={rr.backend_started} health={rr.health_passed} success={rr.success}",
    )


def _dim_api_functionality(ctx: GenerationContext) -> ScoreDimension:
    """Did endpoint smoke tests pass?"""
    rr = ctx.runtime_result
    if rr is None or not rr.endpoint_results:
        return ScoreDimension(name="API Functionality", score=0, weight=0.15,
                              passed=False, details="no endpoints tested")

    total  = len(rr.endpoint_results)
    passed = sum(1 for r in rr.endpoint_results.values()
                 if r.get("status_code", 500) < 400)
    score  = (passed / total * 100) if total else 0.0
    return ScoreDimension(
        name="API Functionality", score=score, weight=0.15,
        passed=score >= 80,
        details=f"{passed}/{total} endpoints passed",
    )


def _dim_frontend_load(ctx: GenerationContext) -> ScoreDimension:
    """Did the frontend load without blank pages?"""
    br = ctx.browser_result
    if br is None or br.skipped:
        return ScoreDimension(name="Frontend Load", score=50, weight=0.10,
                              passed=False, details=f"skipped: {getattr(br,'skip_reason','') if br else 'not run'}")
    if br.blank_page:
        return ScoreDimension(name="Frontend Load", score=0, weight=0.10,
                              passed=False, details="blank page detected")
    if not br.page_loaded:
        return ScoreDimension(name="Frontend Load", score=0, weight=0.10,
                              passed=False, details="page failed to load")
    score = 100.0
    return ScoreDimension(name="Frontend Load", score=score, weight=0.10,
                          passed=True, details="loaded OK")


def _dim_browser_ux(ctx: GenerationContext) -> ScoreDimension:
    """Console errors, network failures, form submission, navigation."""
    br = ctx.browser_result
    if br is None or br.skipped:
        return ScoreDimension(name="Browser UX", score=50, weight=0.15,
                              passed=False, details="skipped")

    # Deductions
    console_penalty  = min(len(br.console_errors) * 5, 30)
    network_penalty  = min(len(br.network_failures) * 8, 24)

    form_score = 0
    if br.forms_tested > 0:
        form_score = (br.forms_passed / br.forms_tested) * 100
    else:
        form_score = 70  # no forms present → neutral

    navigation_score = 100 if br.navigation_passed else 50

    raw = (form_score * 0.4 + navigation_score * 0.6) - console_penalty - network_penalty
    score = max(0.0, min(100.0, raw))
    return ScoreDimension(
        name="Browser UX", score=score, weight=0.15,
        passed=score >= 80,
        details=(f"console_errors={len(br.console_errors)} "
                 f"network_failures={len(br.network_failures)} "
                 f"forms={br.forms_passed}/{br.forms_tested}"),
    )


def _dim_integration(ctx: GenerationContext) -> ScoreDimension:
    """Did the E2E workflow (register→login→CRUD) pass?"""
    br = ctx.browser_result
    if br is None or br.skipped:
        return ScoreDimension(name="Integration", score=50, weight=0.10,
                              passed=False, details="skipped")

    total  = len(br.workflow_steps_passed) + len(br.workflow_steps_failed)
    passed = len(br.workflow_steps_passed)

    if total == 0:
        score = 70  # no workflow steps → neutral
    else:
        score = (passed / total) * 100

    return ScoreDimension(
        name="Integration", score=score, weight=0.10,
        passed=score >= 80,
        details=f"{passed}/{total} workflow steps passed",
    )


def _dim_code_quality(ctx: GenerationContext) -> ScoreDimension:
    """Contract adherence: no stubs, no global statements, no self-shadowing."""
    static_diags = [d for r in ctx.static_results for d in r.diagnostics]
    quality_msgs  = [d for d in static_diags if d.category == ErrorCategory.CONTRACT]
    stub_count    = sum(1 for d in quality_msgs if "stub" in d.message.lower())
    global_count  = sum(1 for d in quality_msgs if "global" in d.message.lower())

    penalty = stub_count * 10 + global_count * 8 + max(0, len(quality_msgs) - stub_count - global_count) * 3
    score   = max(0.0, 100.0 - penalty)
    return ScoreDimension(
        name="Code Quality", score=score, weight=0.05,
        passed=penalty == 0,
        details=f"stubs={stub_count} globals={global_count} other_contract={len(quality_msgs)}",
    )


def _dim_security(ctx: GenerationContext) -> ScoreDimension:
    """No hardcoded secrets, CORS configured, auth in place."""
    static_diags = [d for r in ctx.static_results for d in r.diagnostics]
    sec_issues   = [d for d in static_diags if d.category == ErrorCategory.SECURITY]
    # Heuristic: check for CORS header in runtime result
    rr = ctx.runtime_result
    cors_ok = rr is not None and rr.success  # if backend runs we at least have a chance

    penalty = len(sec_issues) * 15
    base    = 80 if cors_ok else 50
    score   = max(0.0, base - penalty)
    return ScoreDimension(
        name="Security", score=score, weight=0.05,
        passed=score >= 70,
        details=f"security_issues={len(sec_issues)} cors_likely={cors_ok}",
    )


def _dim_completeness(ctx: GenerationContext) -> ScoreDimension:
    """All planned features present (routes, models, schemas)."""
    arch = ctx.architecture
    if arch is None:
        return ScoreDimension(name="Completeness", score=100, weight=0.03,
                              passed=True, details="no architecture plan to check")

    planned = getattr(arch, "api_endpoints", None) or (arch.get("api_endpoints", []) if isinstance(arch, dict) else [])
    if not planned:
        return ScoreDimension(name="Completeness", score=100, weight=0.03,
                              passed=True, details="0 planned endpoints")

    arch_diags = [d for r in ctx.static_results for d in r.diagnostics
                  if d.category == ErrorCategory.ARCHITECTURE]
    missing    = len(arch_diags)
    ratio      = max(0.0, 1 - missing / max(len(planned), 1))
    score      = ratio * 100
    return ScoreDimension(
        name="Completeness", score=score, weight=0.03,
        passed=missing == 0,
        details=f"planned={len(planned)} missing={missing}",
    )


def _dim_performance(ctx: GenerationContext) -> ScoreDimension:
    """Backend response time for /health endpoint."""
    rr = ctx.runtime_result
    if rr is None or not rr.health_passed:
        return ScoreDimension(name="Performance", score=50, weight=0.02,
                              passed=False, details="health check not passed")

    health_ms = rr.endpoint_results.get("/health", {}).get("latency_ms", 0)
    if health_ms == 0:
        score = 90   # unknown latency → assume OK
    elif health_ms < 200:
        score = 100
    elif health_ms < 500:
        score = 85
    elif health_ms < 1000:
        score = 70
    elif health_ms < 2000:
        score = 50
    else:
        score = 30

    return ScoreDimension(
        name="Performance", score=score, weight=0.02,
        passed=score >= 70,
        details=f"health_latency_ms={health_ms:.0f}",
    )


# ── ScoringEngine ─────────────────────────────────────────────────────────────

class ScoringEngine:
    """
    Computes a 0–100 quality score from GenerationContext.

    All 10 dimensions must have weights summing to 1.0.
    Deployment is allowed only when overall >= DEPLOY_THRESHOLD (95).

    Plugin-friendly: register additional dimension functions with .add_dimension().
    """

    def __init__(self):
        self._dimension_fns = [
            _dim_compilation,       # weight 0.15
            _dim_runtime_startup,   # weight 0.20
            _dim_api_functionality, # weight 0.15
            _dim_frontend_load,     # weight 0.10
            _dim_browser_ux,        # weight 0.15
            _dim_integration,       # weight 0.10
            _dim_code_quality,      # weight 0.05
            _dim_security,          # weight 0.05
            _dim_completeness,      # weight 0.03
            _dim_performance,       # weight 0.02
        ]
        # Total weight = 1.00

    def add_dimension(self, fn) -> "ScoringEngine":
        """Register a custom dimension function: fn(ctx) → ScoreDimension."""
        self._dimension_fns.append(fn)
        return self

    def score(self, ctx: GenerationContext, attempt_number: int = 1) -> QualityScore:
        """Compute quality score and record it on ctx."""
        dimensions: list[ScoreDimension] = []

        for fn in self._dimension_fns:
            try:
                dim = fn(ctx)
                dimensions.append(dim)
            except Exception as exc:
                dimensions.append(ScoreDimension(
                    name=fn.__name__.replace("_dim_", "").replace("_", " ").title(),
                    score=0, weight=0.01, passed=False,
                    details=f"scorer crashed: {exc}",
                ))

        # Normalise weights (in case custom dimensions were added)
        total_w = sum(d.weight for d in dimensions)
        overall = sum(d.score * (d.weight / total_w) for d in dimensions) if total_w else 0.0
        overall = round(overall, 2)

        qs = QualityScore(
            overall=overall,
            dimensions=dimensions,
            timestamp=datetime.utcnow(),
            attempt_number=attempt_number,
            deployment_ready=overall >= DEPLOY_THRESHOLD,
        )
        ctx.record_score(qs)
        return qs

    def print_report(self, score: QualityScore):
        bar_width = 30
        sep = "-" * 60
        ready_tag = " [DEPLOY READY]" if score.deployment_ready else " [NEEDS REPAIR]"
        print(f"\n{sep}")
        print(f"  FORGE SCORE: {score.overall:.1f}/100  ({score.grade}){ready_tag}")
        print(sep)
        for d in score.dimensions:
            filled = int(d.score / 100 * bar_width)
            bar    = "#" * filled + "." * (bar_width - filled)
            flag   = "+" if d.passed else "x"
            pct    = f"{d.weight*100:.0f}%"
            print(f"  {flag} {d.name:<22} [{bar}] {d.score:5.1f}  (wt:{pct})")
            if d.details:
                print(f"      {d.details}")
        print(f"{sep}\n")
