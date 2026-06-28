def calculate_forge_score(
    validation,
    runtime_result,
    frontend_build_result=None,
    playwright_result=None,
    vision_result=None,
    docker_result=None,
    deployment_result=None,
    health_report=None,
):
    category_weights = {
        "Missing app/main.py": 25,
        "Missing route file": 20,
        "Architecture violation": 15,
        "Schema mismatch": 8,
        "Session leak risk": 8,
        "Missing APIRouter": 8,
        "No endpoints found": 8,
        "Database symbol": 8,
        "ORM violation": 10,
        "Syntax error": 10,
        "Stub handler risk": 8,
        "Self-shadowing recursion risk": 10,
        "Missing endpoint": 5,
        "Missing symbol": 5,
        "Undefined symbol": 5,
        "Router export mismatch": 5,
        "Missing backend import target": 6,
        "Missing frontend import target": 6,
        "Import style mismatch": 6,
        "Orphan file": 3,
    }

    category_caps = {
        "Schema mismatch": 16,
        "ORM violation": 20,
        "Undefined symbol": 15,
        "Missing symbol": 15,
        "Database symbol": 16,
        "Import style mismatch": 12,
    }

    category_totals = {}
    other_total = 0

    for error in validation["errors"]:
        matched = False
        for category, weight in category_weights.items():
            if category in error:
                category_totals[category] = category_totals.get(category, 0) + weight
                matched = True
                break
        if not matched:
            other_total += 2

    score = 100

    for category, total in category_totals.items():
        cap = category_caps.get(category)
        score -= min(total, cap) if cap else total

    score -= other_total

    # ── Runtime scoring ──────────────────────────────────────────────────────
    # Health check failed entirely → -30
    # Health OK but endpoints failing → proportional penalty (up to -25)
    # CRUD journey critically failed → -20
    if not runtime_result or not runtime_result.get("success", False):
        score -= 30
    else:
        # Penalize endpoint timeouts/errors even when health passed
        behavioral_issues = runtime_result.get("behavioral_issues", [])
        if behavioral_issues:
            timeout_count = sum(
                1 for i in behavioral_issues if "timed out" in i.get("issue", "")
            )
            error_count = sum(
                1 for i in behavioral_issues if "crashed" in i.get("issue", "")
            )
            # 5 pts per timeout, 3 pts per 5xx crash, capped at 25
            endpoint_penalty = min(timeout_count * 5 + error_count * 3, 25)
            score -= endpoint_penalty

    # CRUD journey result (serialized into runtime_result.journey by BackendRunner)
    journey = runtime_result.get("journey") if runtime_result else None
    if journey and not journey.get("skipped", True):
        if not journey.get("success", True):
            score -= 20  # critical CRUD steps failed
        else:
            steps_failed = journey.get("steps_failed", 0)
            if steps_failed:
                score -= min(steps_failed * 2, 8)

    # ── Frontend build ───────────────────────────────────────────────────────
    # -15 if Node is present but build failed; skip if Node missing.
    # Default node_missing=False: apply penalty unless explicitly marked as skipped.
    if frontend_build_result and not frontend_build_result.get("node_missing", False):
        if not frontend_build_result.get("success", False):
            score -= 15

    # ── Playwright ───────────────────────────────────────────────────────────
    # -5 per blank page, -3 per JS console error (capped at -10)
    if playwright_result:
        _get = (
            playwright_result.get
            if isinstance(playwright_result, dict)
            else lambda k, d=None: getattr(playwright_result, k, d)
        )
        if not _get("skipped"):
            blank_penalty = len(_get("blank_pages") or []) * 5
            error_penalty = len(_get("console_errors") or []) * 3
            score -= min(blank_penalty + error_penalty, 10)

    # ── Vision UI quality ────────────────────────────────────────────────────
    # 70+  = functional UI          → 0
    # 50-69 = partial UI            → -5
    # 30-49 = skeleton/placeholder  → -12
    # 0-29  = blank or broken       → -20
    if vision_result:
        _vget = (
            vision_result.get
            if isinstance(vision_result, dict)
            else lambda k, d=None: getattr(vision_result, k, d)
        )
        if not _vget("skipped"):
            ui_score = _vget("ui_score") or 100
            if ui_score < 30:
                score -= 20
            elif ui_score < 50:
                score -= 12
            elif ui_score < 70:
                score -= 5

    # ── Docker ───────────────────────────────────────────────────────────────
    if docker_result and not getattr(
        docker_result,
        "skipped",
        docker_result.get("skipped", True) if isinstance(docker_result, dict) else True,
    ):
        _dget = (
            docker_result.get
            if isinstance(docker_result, dict)
            else lambda k, d=None: getattr(docker_result, k, d)
        )
        if not _dget("build_passed"):
            score -= 15
        elif not _dget("health_passed"):
            score -= 10

    # ── Deployment ───────────────────────────────────────────────────────────
    # -20 build/startup failed
    # -15 deployed but /health non-200
    # -5  healthy but latency > 2000ms
    if deployment_result and not deployment_result.get("skipped"):
        if not deployment_result.get("success"):
            score -= 20
        elif health_report and not health_report.get("skipped"):
            if not health_report.get("success"):
                score -= 15
            else:
                health_check = (health_report.get("checks") or {}).get("/health", {})
                latency_ms = health_check.get("latency_ms")
                if latency_ms and latency_ms > 2000:
                    score -= 5

    score = max(0, score)

    if score >= 90:
        grade = "A"
    elif score >= 80:
        grade = "B"
    elif score >= 70:
        grade = "C"
    elif score >= 60:
        grade = "D"
    else:
        grade = "F"

    return {"score": score, "grade": grade}


def build_pipeline_metrics(
    validation: dict,
    runtime_result: dict | None,
    frontend_build_result: dict | None,
    playwright_result=None,
) -> dict:
    """
    Produce a structured breakdown of per-stage pass/fail used for tracking
    progress toward the 7-metric targets.

    Targets:
        compile_success    ≥98%
        backend_starts     ≥98%
        health_endpoint    ≥99%
        crud_tests         ≥95%
        frontend_builds    ≥95%
        browser_tests      ≥95%
        deployment_success ≥90%
    """
    compile_success = validation.get("passed", False)

    health_passed = bool(runtime_result and runtime_result.get("success", False))
    backend_starts = health_passed  # if health passed, server definitely started

    endpoint_pass_rate = (
        runtime_result.get("endpoint_pass_rate", 1.0) if runtime_result else None
    )

    journey = runtime_result.get("journey") if runtime_result else None
    crud_passed: bool | None = None
    if journey and not journey.get("skipped", True):
        crud_passed = journey.get("success", False)

    node_missing = frontend_build_result.get("node_missing", True) if frontend_build_result else True
    frontend_builds: bool | None = None
    if not node_missing and frontend_build_result:
        frontend_builds = frontend_build_result.get("success", False)

    browser_tests: bool | None = None
    if playwright_result:
        _get = (
            playwright_result.get
            if isinstance(playwright_result, dict)
            else lambda k, d=None: getattr(playwright_result, k, d)
        )
        if not _get("skipped", True):
            browser_tests = _get("success", False)

    return {
        "compile_success": compile_success,
        "backend_starts": backend_starts,
        "health_endpoint": health_passed,
        "endpoint_pass_rate": endpoint_pass_rate,
        "crud_tests": crud_passed,
        "frontend_builds": frontend_builds,
        "browser_tests": browser_tests,
        "node_available": not node_missing,
    }
