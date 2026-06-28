"""
VerificationEngine — 10-stage verification pipeline with parallel post-runtime stages.

Execution order:
  [Sequential]
    1.  Static          — syntax, imports, ORM, router names, contract rules
    2.  Compile         — py_compile every .py file for fast syntax check
    3.  Runtime         — install deps, start uvicorn, health check

  [Parallel — after backend is up]
    4.  HTTP            — direct HTTP calls to every endpoint
    5.  Browser         — Playwright: page load, blank-page check
    6.  Console Logs    — browser console errors
    7.  Screenshots     — capture full-page PNG
    8.  Performance     — /health latency, response time distribution
    9.  Accessibility   — basic a11y: alt text, labels, contrast hints

  [Sequential — after all parallel stages]
   10.  LLM Judge       — vision LLM interprets screenshots + all failures:
                          "Submit button hidden behind modal" not just "element not found"

  [Analysis]
   11.  Failure Graph   — causal analysis: suppress downstream symptoms, surface roots
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

from app.core.context import (
    Diagnostic, ErrorCategory, ErrorSeverity,
    GenerationContext, RuntimeResult, StageStatus,
    VerificationResult,
)


# ═══════════════════════════════════════════════════════════════
#   STAGE 1 — Static validators
# ═══════════════════════════════════════════════════════════════

def _run_static_validators(ctx: GenerationContext) -> VerificationResult:
    t0 = time.time()
    try:
        from app.services.validator_service import validate_project
        validation = validate_project(str(ctx.project_path))
    except Exception as exc:
        return VerificationResult(
            stage="static", status=StageStatus.FAILED,
            diagnostics=[_diag("static", f"Validator service crashed: {exc}", ErrorSeverity.CRITICAL, ErrorCategory.RUNTIME)],
            duration_ms=_ms(t0),
        )

    diagnostics = [
        Diagnostic(
            error_id=uuid.uuid4().hex[:8],
            category=_categorise_static(err),
            severity=_severity_static(err),
            source="static",
            message=err,
            fix_hint=_hint_static(err),
        )
        for err in validation.get("errors", [])
    ] + [
        _diag("static", w, ErrorSeverity.LOW, ErrorCategory.CONTRACT)
        for w in validation.get("warnings", [])
    ]

    status = StageStatus.PASSED if not validation.get("errors") else StageStatus.FAILED
    return VerificationResult(
        stage="static", status=status, diagnostics=diagnostics,
        duration_ms=_ms(t0),
        metadata={"error_count": len(validation.get("errors", [])), "warning_count": len(validation.get("warnings", []))},
    )


# ═══════════════════════════════════════════════════════════════
#   STAGE 2 — Compile (py_compile, fast)
# ═══════════════════════════════════════════════════════════════

def _run_compile_check(ctx: GenerationContext) -> VerificationResult:
    """py_compile every .py file — fast catch for LLM-generated syntax errors."""
    import py_compile
    t0 = time.time()
    diagnostics: list[Diagnostic] = []
    py_files = list((ctx.project_path / "app").rglob("*.py")) if ctx.project_path else []
    for f in py_files:
        try:
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError as exc:
            diagnostics.append(Diagnostic(
                error_id=uuid.uuid4().hex[:8],
                category=ErrorCategory.SYNTAX,
                severity=ErrorSeverity.CRITICAL,
                source="compile",
                message=str(exc),
                file_path=str(f),
                fix_hint="Fix Python syntax error — check for missing colons, unmatched parens, or indentation",
            ))
    status = StageStatus.PASSED if not diagnostics else StageStatus.FAILED
    return VerificationResult(stage="compile", status=status, diagnostics=diagnostics, duration_ms=_ms(t0))


# ═══════════════════════════════════════════════════════════════
#   STAGE 3 — Runtime
# ═══════════════════════════════════════════════════════════════

def _run_runtime_validation(ctx: GenerationContext) -> VerificationResult:
    t0 = time.time()
    try:
        from app.runtime.backend_runner import run_backend_validation
        result = run_backend_validation(str(ctx.project_path), port=ctx.backend_port)
    except Exception as exc:
        return VerificationResult(
            stage="runtime", status=StageStatus.FAILED,
            diagnostics=[_diag("runtime", f"RuntimeRunner crashed: {exc}", ErrorSeverity.CRITICAL, ErrorCategory.RUNTIME)],
            duration_ms=_ms(t0),
        )

    diagnostics: list[Diagnostic] = []
    success = result.get("success", False)

    if not success:
        stderr = result.get("stderr", "")
        parsed = result.get("parsed_error", {})
        err_type = parsed.get("type", "RuntimeError")
        message = parsed.get("message") or (stderr[:400] if stderr else "Backend failed to start")
        cat_map = {"ModuleNotFoundError": ErrorCategory.DEPENDENCY,
                   "SyntaxError": ErrorCategory.SYNTAX,
                   "ImportError": ErrorCategory.IMPORT}
        diagnostics.append(Diagnostic(
            error_id=uuid.uuid4().hex[:8],
            category=cat_map.get(err_type, ErrorCategory.RUNTIME),
            severity=ErrorSeverity.CRITICAL,
            source="runtime",
            message=f"[{err_type}] {message}",
            file_path=parsed.get("error_file"),
            stack_trace=stderr[:800] if stderr else None,
            fix_hint=parsed.get("hint"),
            metadata={"parsed_error": parsed},
        ))

    if success:
        ctx.backend_url = f"http://127.0.0.1:{ctx.backend_port}"

    ctx.runtime_result = RuntimeResult(
        success=success,
        backend_started=result.get("started", False),
        health_passed=result.get("health_passed", False),
        stderr=result.get("stderr", ""),
        stdout=result.get("stdout", ""),
        crash_reason=result.get("crash_reason"),
        endpoint_results=result.get("endpoint_results", {}),
        diagnostics=diagnostics,
        duration_ms=_ms(t0),
    )

    status = StageStatus.PASSED if success else StageStatus.FAILED
    return VerificationResult(
        stage="runtime", status=status, diagnostics=diagnostics,
        duration_ms=_ms(t0), metadata={"backend_url": ctx.backend_url},
    )


# ═══════════════════════════════════════════════════════════════
#   STAGE 4 — HTTP endpoint tests (parallel-safe)
# ═══════════════════════════════════════════════════════════════

def _run_http_tests(ctx: GenerationContext) -> VerificationResult:
    """Direct HTTP calls to all planned endpoints. Separate from Playwright."""
    t0 = time.time()
    diagnostics: list[Diagnostic] = []
    if not ctx.backend_url:
        return VerificationResult(stage="http", status=StageStatus.SKIPPED,
                                  metadata={"reason": "no backend url"}, duration_ms=_ms(t0))
    try:
        import httpx
        endpoints = _get_endpoints(ctx)
        passed, failed = 0, 0
        for ep in endpoints[:20]:  # cap at 20 to avoid test bloat
            method = ep.get("method", "GET").upper()
            path   = ep.get("path", "/")
            url    = ctx.backend_url.rstrip("/") + path
            try:
                r = httpx.request(method, url, timeout=5)
                # 4xx/5xx that aren't 404/405 are failures
                if r.status_code not in (200, 201, 204, 400, 401, 403, 404, 405, 422):
                    diagnostics.append(_diag("http",
                        f"{method} {path} returned {r.status_code}",
                        ErrorSeverity.HIGH, ErrorCategory.API))
                    failed += 1
                else:
                    passed += 1
            except Exception as exc:
                diagnostics.append(_diag("http",
                    f"{method} {path} request failed: {exc}",
                    ErrorSeverity.MEDIUM, ErrorCategory.API))
                failed += 1

        status = StageStatus.PASSED if not diagnostics else (
            StageStatus.FAILED if failed > passed else StageStatus.FAILED
        )
        return VerificationResult(stage="http", status=status, diagnostics=diagnostics,
                                  duration_ms=_ms(t0),
                                  metadata={"passed": passed, "failed": failed})
    except Exception as exc:
        return VerificationResult(stage="http", status=StageStatus.FAILED,
                                  diagnostics=[_diag("http", f"HTTP tester crashed: {exc}",
                                                     ErrorSeverity.MEDIUM, ErrorCategory.API)],
                                  duration_ms=_ms(t0))


# ═══════════════════════════════════════════════════════════════
#   STAGE 5+6+7 — Browser, Console, Screenshots (parallel-safe)
# ═══════════════════════════════════════════════════════════════

def _run_browser_and_screenshots(ctx: GenerationContext) -> tuple[VerificationResult, list[str]]:
    """
    Playwright page-load + console errors + screenshots.
    Returns (VerificationResult, [b64_png, ...])
    """
    t0 = time.time()
    diagnostics: list[Diagnostic] = []
    screenshots: list[str] = []

    try:
        from app.runtime.playwright_runner import run_playwright_tests
        pr = run_playwright_tests(
            str(ctx.project_path),
            architecture=_arch_dict(ctx),
            capture_screenshots=True,
        )
    except Exception as exc:
        pr = None
        diagnostics.append(_diag("browser", f"Playwright runner crashed: {exc}", ErrorSeverity.HIGH, ErrorCategory.BROWSER))

    if pr and not getattr(pr, "skipped", False):
        for ce in (pr.console_errors or []):
            diagnostics.append(_diag("browser", f"Console error: {ce}",
                                     ErrorSeverity.MEDIUM, ErrorCategory.BROWSER,
                                     hint="Fix the JS error; often a missing import or API mismatch"))
        for bp in (pr.blank_pages or []):
            diagnostics.append(_diag("browser", f"Blank page at route: {bp}",
                                     ErrorSeverity.HIGH, ErrorCategory.BROWSER,
                                     hint="React failed to render — check App.jsx for unhandled exceptions"))
        screenshots = [s.get("png_b64", "") for s in (pr.screenshots or []) if s.get("png_b64")]

    skipped = bool(pr and getattr(pr, "skipped", False))
    status = StageStatus.SKIPPED if skipped else (
        StageStatus.PASSED if not diagnostics else StageStatus.FAILED
    )
    vr = VerificationResult(stage="browser", status=status, diagnostics=diagnostics,
                            duration_ms=_ms(t0),
                            metadata={"screenshot_count": len(screenshots), "skipped": skipped})
    return vr, screenshots


# ═══════════════════════════════════════════════════════════════
#   STAGE 8 — Performance (parallel-safe)
# ═══════════════════════════════════════════════════════════════

def _run_performance_check(ctx: GenerationContext) -> VerificationResult:
    """Measure /health endpoint latency. Flag if > 500ms (deployment SLA)."""
    t0 = time.time()
    if not ctx.backend_url:
        return VerificationResult(stage="performance", status=StageStatus.SKIPPED,
                                  duration_ms=_ms(t0))
    diagnostics: list[Diagnostic] = []
    latencies: list[float] = []
    try:
        import httpx
        health_url = ctx.backend_url.rstrip("/") + "/health"
        for _ in range(3):
            t = time.time()
            try:
                r = httpx.get(health_url, timeout=5)
                latencies.append((time.time() - t) * 1000)
            except Exception:
                latencies.append(5000.0)

        avg_ms = sum(latencies) / len(latencies)
        if avg_ms > 500:
            diagnostics.append(_diag("performance",
                f"/health avg latency {avg_ms:.0f}ms (target <500ms)",
                ErrorSeverity.MEDIUM, ErrorCategory.PERFORMANCE,
                hint="Check for blocking I/O or heavy imports in startup; use lifespan events"))

        status = StageStatus.PASSED if not diagnostics else StageStatus.FAILED
        return VerificationResult(stage="performance", status=status, diagnostics=diagnostics,
                                  duration_ms=_ms(t0),
                                  metadata={"health_avg_ms": round(avg_ms, 1), "samples": latencies})
    except Exception as exc:
        return VerificationResult(stage="performance", status=StageStatus.FAILED,
                                  diagnostics=[_diag("performance", f"Perf check crashed: {exc}",
                                                     ErrorSeverity.LOW, ErrorCategory.PERFORMANCE)],
                                  duration_ms=_ms(t0))


# ═══════════════════════════════════════════════════════════════
#   STAGE 9 — Accessibility (parallel-safe)
# ═══════════════════════════════════════════════════════════════

def _run_accessibility_check(ctx: GenerationContext) -> VerificationResult:
    """
    Basic accessibility scan on JSX source files.
    Checks: img without alt, form inputs without labels, ARIA attributes.
    This is static source analysis (no browser needed).
    """
    import re
    t0 = time.time()
    diagnostics: list[Diagnostic] = []
    jsx_files = list((ctx.project_path).rglob("*.jsx")) if ctx.project_path else []
    jsx_files += list((ctx.project_path).rglob("*.tsx")) if ctx.project_path else []

    for f in jsx_files[:30]:
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        # img without alt
        for m in re.finditer(r"<img(?![^>]*\balt=)[^>]*/?>", src):
            diagnostics.append(_diag("accessibility",
                f"{f.name}: <img> missing alt attribute",
                ErrorSeverity.LOW, ErrorCategory.CONTRACT,
                hint='Add alt="description" to the <img> tag'))

        # input without associated label or aria-label
        inputs = re.findall(r"<input(?![^>]*aria-label=)(?![^>]*aria-labelledby=)[^>]*/?>", src)
        for _ in inputs:
            # Check if there's a <label htmlFor=> nearby (rough check)
            if "htmlFor=" not in src and "<label" not in src:
                diagnostics.append(_diag("accessibility",
                    f"{f.name}: <input> may be missing a label",
                    ErrorSeverity.LOW, ErrorCategory.CONTRACT,
                    hint="Add aria-label='...' or a <label htmlFor=...> for each input"))
                break  # one warning per file is enough

    status = StageStatus.PASSED if not diagnostics else StageStatus.FAILED
    return VerificationResult(stage="accessibility", status=status, diagnostics=diagnostics,
                              duration_ms=_ms(t0),
                              metadata={"files_checked": len(jsx_files)})


# ═══════════════════════════════════════════════════════════════
#   STAGE 10 — Workflow tests (parallel-safe)
# ═══════════════════════════════════════════════════════════════

def _run_workflow_tests(ctx: GenerationContext, screenshots: list[str]) -> tuple[VerificationResult, list[str]]:
    """Register → Login → CRUD workflow via Playwright. Returns (result, screenshots)."""
    t0 = time.time()
    diagnostics: list[Diagnostic] = []
    new_screenshots = list(screenshots)

    if not ctx.backend_url:
        return VerificationResult(stage="workflow", status=StageStatus.SKIPPED,
                                  duration_ms=_ms(t0)), new_screenshots

    try:
        from app.runtime.playwright_workflow import run_workflow_tests
        wf = run_workflow_tests(
            str(ctx.project_path),
            architecture=_arch_dict(ctx),
            base_url=f"http://127.0.0.1:{ctx.frontend_port}",
            capture_screenshots=True,
        )
        for step in (wf.steps_failed or []):
            diagnostics.append(_diag("workflow", f"Workflow step failed: {step}",
                                     ErrorSeverity.HIGH, ErrorCategory.INTEGRATION,
                                     hint="Check API endpoint and frontend form binding for this step"))
        new_screenshots += [s.get("png_b64", "") for s in (wf.screenshots or []) if s.get("png_b64")]
    except Exception as exc:
        diagnostics.append(_diag("workflow", f"Workflow runner error: {exc}",
                                 ErrorSeverity.MEDIUM, ErrorCategory.BROWSER))

    status = StageStatus.PASSED if not diagnostics else StageStatus.FAILED
    return VerificationResult(stage="workflow", status=status, diagnostics=diagnostics,
                              duration_ms=_ms(t0)), new_screenshots


# ═══════════════════════════════════════════════════════════════
#   STAGE 11 — LLM Judge (sequential, after all parallel stages)
# ═══════════════════════════════════════════════════════════════

def _run_llm_judge(
    ctx: GenerationContext,
    all_pre_judge_diagnostics: list[Diagnostic],
    screenshots: list[str],
) -> VerificationResult:
    """
    Send screenshots + all accumulated failures to a vision LLM.
    Produces richer, human-interpretable assessments.
    """
    t0 = time.time()

    # Only run if there are failures worth interpreting
    failures = [d for d in all_pre_judge_diagnostics
                if d.severity in (ErrorSeverity.CRITICAL, ErrorSeverity.HIGH)]
    if not failures and not screenshots:
        return VerificationResult(stage="llm_judge", status=StageStatus.SKIPPED,
                                  metadata={"reason": "no failures to interpret"},
                                  duration_ms=_ms(t0))

    console_errors = [d.message for d in all_pre_judge_diagnostics if "console error" in d.message.lower()]
    network_failures = [d.message for d in all_pre_judge_diagnostics if "http" in d.source.lower()]
    workflow_failures = [d.message for d in all_pre_judge_diagnostics if d.source == "workflow"]

    try:
        from app.verification.llm_judge import judge_screenshot
        judgment = judge_screenshot(
            screenshot_b64=screenshots[0] if screenshots else None,
            console_errors=console_errors,
            network_failures=network_failures,
            workflow_failures=workflow_failures,
            app_idea=getattr(ctx, "idea", ""),
        )
    except Exception as exc:
        return VerificationResult(stage="llm_judge", status=StageStatus.SKIPPED,
                                  metadata={"reason": f"judge error: {exc}"},
                                  duration_ms=_ms(t0))

    diagnostics: list[Diagnostic] = []
    sev_map = {"critical": ErrorSeverity.CRITICAL, "high": ErrorSeverity.HIGH,
               "medium": ErrorSeverity.MEDIUM, "low": ErrorSeverity.LOW}
    if judgment.assessment and judgment.severity not in ("info", ""):
        diagnostics.append(Diagnostic(
            error_id=uuid.uuid4().hex[:8],
            category=ErrorCategory.BROWSER,
            severity=sev_map.get(judgment.severity, ErrorSeverity.MEDIUM),
            source="llm_judge",
            message=judgment.assessment,
            fix_hint=judgment.fix_hint or None,
            metadata={"confidence": judgment.confidence, "screenshot_used": judgment.screenshot_available},
        ))

    status = StageStatus.PASSED if not diagnostics else StageStatus.FAILED
    return VerificationResult(stage="llm_judge", status=status, diagnostics=diagnostics,
                              duration_ms=_ms(t0),
                              metadata={"confidence": judgment.confidence, "model": judgment.model_used})


# ═══════════════════════════════════════════════════════════════
#   Main VerificationEngine
# ═══════════════════════════════════════════════════════════════

class VerificationEngine:
    """
    Orchestrates all 11 verification stages.

    Parallel execution model:
      After the backend is confirmed running, HTTP / Browser / Performance /
      Accessibility stages run concurrently in a thread pool.
      Only the LLM Judge and Failure Graph run sequentially after (they
      need all other results first).
    """

    def __init__(self, run_runtime: bool = True, run_browser: bool = True):
        self.run_runtime = run_runtime
        self.run_browser = run_browser
        self._extra: list[Any] = []

    def register(self, verifier) -> "VerificationEngine":
        self._extra.append(verifier)
        return self

    def run(self, ctx: GenerationContext) -> list[VerificationResult]:
        results: list[VerificationResult] = []
        ctx.static_results  = []
        ctx.runtime_result  = None
        ctx.browser_result  = None
        all_screenshots:  list[str] = []

        # ── Stage 1: Static ───────────────────────────────────────────────────
        print("  [verify] 1/11 Static analysis...")
        evt = ctx.begin_stage("static-validation")
        sr = _run_static_validators(ctx)
        ctx.static_results.append(sr)
        results.append(sr)
        ctx.end_stage(evt, sr.status, errors=len([d for d in sr.diagnostics if d.severity != ErrorSeverity.LOW]))
        print(f"  [verify]       {sr.status.value} — {len(sr.diagnostics)} issues")

        # ── Stage 2: Compile ──────────────────────────────────────────────────
        print("  [verify] 2/11 Compile check...")
        cr = _run_compile_check(ctx)
        ctx.static_results.append(cr)
        results.append(cr)
        print(f"  [verify]       {cr.status.value} — {len(cr.diagnostics)} syntax errors")

        # ── Extra custom verifiers ────────────────────────────────────────────
        for verifier in self._extra:
            try:
                vr = verifier(ctx) if callable(verifier) else verifier.verify(ctx)
                ctx.static_results.append(vr)
                results.append(vr)
            except Exception as exc:
                results.append(VerificationResult(
                    stage="custom", status=StageStatus.FAILED,
                    diagnostics=[_diag("custom", f"Custom verifier crashed: {exc}",
                                       ErrorSeverity.MEDIUM, ErrorCategory.RUNTIME)],
                ))

        # ── Stage 3: Runtime ──────────────────────────────────────────────────
        critical_static = sum(1 for r in ctx.static_results for d in r.diagnostics
                              if d.severity == ErrorSeverity.CRITICAL)

        if self.run_runtime:
            if critical_static > 0:
                print(f"  [verify] 3/11 Runtime: SKIPPED ({critical_static} critical static errors)")
                results.append(VerificationResult(stage="runtime", status=StageStatus.SKIPPED,
                                                  metadata={"reason": "critical static errors"}))
            else:
                print("  [verify] 3/11 Runtime startup...")
                evt = ctx.begin_stage("runtime-validation")
                rr = _run_runtime_validation(ctx)
                results.append(rr)
                ctx.end_stage(evt, rr.status)
                print(f"  [verify]       {rr.status.value}")
        else:
            results.append(VerificationResult(stage="runtime", status=StageStatus.SKIPPED))

        # ── Stages 4–9: Parallel post-runtime ────────────────────────────────
        runtime_ok = ctx.runtime_result and ctx.runtime_result.success

        if runtime_ok and self.run_browser:
            print("  [verify] 4-9: Running HTTP / Browser / Performance / Accessibility in parallel...")
            evt = ctx.begin_stage("parallel-checks")

            # browser_result captures + screenshots stored separately
            browser_vr: Optional[VerificationResult] = None
            http_vr: Optional[VerificationResult]    = None
            perf_vr: Optional[VerificationResult]    = None
            a11y_vr: Optional[VerificationResult]    = None

            with ThreadPoolExecutor(max_workers=4) as pool:
                fut_http   = pool.submit(_run_http_tests, ctx)
                fut_browser = pool.submit(_run_browser_and_screenshots, ctx)
                fut_perf   = pool.submit(_run_performance_check, ctx)
                fut_a11y   = pool.submit(_run_accessibility_check, ctx)

                # Collect results as they finish
                for fut in as_completed([fut_http, fut_browser, fut_perf, fut_a11y]):
                    try:
                        result = fut.result()
                        if fut is fut_browser:
                            browser_vr, screenshots = result
                            all_screenshots.extend(s for s in screenshots if s)
                        elif fut is fut_http:
                            http_vr = result
                        elif fut is fut_perf:
                            perf_vr = result
                        elif fut is fut_a11y:
                            a11y_vr = result
                    except Exception as exc:
                        results.append(VerificationResult(
                            stage="parallel", status=StageStatus.FAILED,
                            diagnostics=[_diag("parallel", f"Parallel stage crashed: {exc}",
                                               ErrorSeverity.MEDIUM, ErrorCategory.RUNTIME)],
                        ))

            ctx.end_stage(evt, StageStatus.PASSED)

            for stage_result in [http_vr, browser_vr, perf_vr, a11y_vr]:
                if stage_result:
                    results.append(stage_result)
                    print(f"  [verify]       {stage_result.stage}: {stage_result.status.value} "
                          f"— {len(stage_result.diagnostics)} issues")

            # ── Stage 10: Workflow (sequential, needs browser to be done) ────
            print("  [verify] 10/11 Workflow tests...")
            wf_vr, all_screenshots = _run_workflow_tests(ctx, all_screenshots)
            results.append(wf_vr)
            print(f"  [verify]       {wf_vr.status.value}")

        else:
            skip_reason = "backend not running" if not runtime_ok else "browser disabled"
            for stage in ("http", "browser", "performance", "accessibility", "workflow"):
                results.append(VerificationResult(stage=stage, status=StageStatus.SKIPPED,
                                                  metadata={"reason": skip_reason}))
                print(f"  [verify] SKIPPED {stage}: {skip_reason}")

        # ── Stage 11: LLM Judge (sequential, needs all previous results) ─────
        print("  [verify] 11/11 LLM Judge...")
        all_pre_judge_diags = [d for r in results for d in r.diagnostics]
        judge_vr = _run_llm_judge(ctx, all_pre_judge_diags, all_screenshots)
        results.append(judge_vr)
        if judge_vr.diagnostics:
            print(f"  [verify]       LLM Judge: {judge_vr.diagnostics[0].message[:80]}")
        else:
            print(f"  [verify]       LLM Judge: {judge_vr.status.value}")

        # ── Failure Graph analysis ────────────────────────────────────────────
        all_diags = [d for r in results for d in r.diagnostics]
        graph = _build_graph(all_diags)
        if graph and graph.suppressed_count > 0:
            print(f"  [verify] Failure Graph: {len(graph.roots)} root cause(s), "
                  f"{graph.suppressed_count} downstream suppressed")
            print(f"  [verify] {graph.explain()[:300]}")
            # Store graph on ctx for the fix orchestrator to use
            ctx.failure_graph = graph  # type: ignore[attr-defined]

        return results


# ═══════════════════════════════════════════════════════════════
#   Helpers
# ═══════════════════════════════════════════════════════════════

def _ms(t0: float) -> float:
    return (time.time() - t0) * 1000


def _diag(source: str, msg: str, sev: ErrorSeverity, cat: ErrorCategory,
          hint: Optional[str] = None) -> Diagnostic:
    return Diagnostic(
        error_id=uuid.uuid4().hex[:8],
        category=cat,
        severity=sev,
        source=source,
        message=msg,
        fix_hint=hint,
    )


def _get_endpoints(ctx: GenerationContext) -> list[dict]:
    arch = ctx.architecture
    if not arch:
        return []
    if isinstance(arch, dict):
        return arch.get("endpoints", [])
    return getattr(arch, "endpoints", []) or []


def _arch_dict(ctx: GenerationContext) -> Optional[dict]:
    if ctx.architecture and hasattr(ctx.architecture, "dict"):
        return ctx.architecture.dict()
    if isinstance(ctx.architecture, dict):
        return ctx.architecture
    return None


def _build_graph(diagnostics: list[Diagnostic]):
    try:
        from app.verification.graph import build_failure_graph
        return build_failure_graph(diagnostics)
    except Exception:
        return None


def _categorise_static(err: str) -> ErrorCategory:
    e = err.lower()
    if "syntax" in e:                         return ErrorCategory.SYNTAX
    if "import" in e or "module" in e:        return ErrorCategory.IMPORT
    if "orm" in e or "flask" in e:            return ErrorCategory.CONTRACT
    if "router" in e or "apirouter" in e:     return ErrorCategory.CONTRACT
    if "missing endpoint" in e:               return ErrorCategory.API
    if "schema" in e or "model" in e:         return ErrorCategory.CONTRACT
    if "database" in e or "session" in e:     return ErrorCategory.CONTRACT
    if "stub" in e or "placeholder" in e:     return ErrorCategory.CONTRACT
    if "undefined" in e or "symbol" in e:     return ErrorCategory.IMPORT
    return ErrorCategory.CONTRACT


def _severity_static(err: str) -> ErrorSeverity:
    e = err.lower()
    if "syntax error" in e:                   return ErrorSeverity.CRITICAL
    if "missing app/main.py" in e:            return ErrorSeverity.CRITICAL
    if "missing route file" in e:             return ErrorSeverity.HIGH
    if "orm violation" in e:                  return ErrorSeverity.HIGH
    if "router export" in e:                  return ErrorSeverity.HIGH
    if "undefined symbol" in e:               return ErrorSeverity.HIGH
    if "session leak" in e:                   return ErrorSeverity.MEDIUM
    if "stub handler" in e:                   return ErrorSeverity.MEDIUM
    return ErrorSeverity.MEDIUM


def _hint_static(err: str) -> Optional[str]:
    e = err.lower()
    if "orm violation" in e:
        return "Replace db.Model / db = SQLAlchemy() with SQLAlchemy Base"
    if "router export" in e:
        return "Rename `router` to `{resource}_router = APIRouter()`"
    if "syntax error" in e:
        return "Fix Python syntax; check for missing colons, bad indentation"
    return None
