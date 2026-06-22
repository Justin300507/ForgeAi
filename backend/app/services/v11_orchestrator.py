"""
V11 — Autonomous Deployment Platform

Pipeline:
  Generate (V7 self-improving pipeline)
      ↓
  Static Validation
      ↓
  Runtime Validation
      ↓
  Deploy (Railway → Render → Fly.io)
      ↓
  Health Check (deployment validator)
      ↓
  Return: live URL + deployment logs + health report + forge score

V11 adds a deployment score component to the forge score:
  Generation    30%
  Runtime       20%
  Vision        15%
  Security      10%
  Performance   10%
  Deployment    15%
"""
import time
from typing import Any

from app.services.v7_orchestrator import generate_project_v7
from app.services.deployment_service import deploy_project, get_deployment_history
from app.runtime.deployment_validator import validate_deployment_with_retry
from app.runtime.deployment_error_parser import parse_deployment_error
from app.services.deployment_fix_service import generate_deployment_fix, record_deployment_outcome
from app.services.forge_score_service import calculate_forge_score


def generate_project_v11(
    idea: str,
    provider: str = "auto",
    deploy_provider: str = "railway",
    run_improvement_cycle: bool = False,
    skip_reviews: bool = True,
    skip_deploy: bool = False,
    frontend_target: str = "web",
) -> dict[str, Any]:
    """
    V11 pipeline: generate → validate → deploy → health check → return live URL.

    Args:
        idea:                   plain-English project description
        provider:               LLM provider for generation (auto/cerebras/gemini/openai)
        deploy_provider:        deployment target (railway/render/flyio)
        run_improvement_cycle:  whether to run V7 rule evolution after generation
        skip_reviews:           skip QA/Security/Code/Performance reviews (saves cost)
        skip_deploy:            if True, skip deployment (generation + validation only)
    """
    start = time.time()

    print(f"\n{'#'*70}")
    print(f"# V11 AUTONOMOUS DEPLOYMENT PLATFORM")
    print(f"# Idea: {idea[:60]}")
    print(f"# LLM Provider: {provider} | Deploy: {deploy_provider}")
    print(f"{'#'*70}")

    # ── Step 1: Generate + validate with V7 ──────────────────────────────
    print("\n[V11] Step 1/3 — Generating project...")
    v7_result = generate_project_v7(
        idea=idea,
        provider=provider,
        run_improvement_cycle=run_improvement_cycle,
        skip_reviews=skip_reviews,
        frontend_target=frontend_target,
    )

    project_name = v7_result.get("project_name", "")
    project_path = v7_result.get("project_path", "")
    runtime_passed = bool((v7_result.get("runtime") or {}).get("success"))
    static_passed = bool((v7_result.get("validation") or {}).get("passed"))

    deployment_result: dict[str, Any] = {
        "skipped": True,
        "reason": "not attempted",
    }
    health_report: dict[str, Any] = {
        "skipped": True,
        "reason": "deployment not attempted",
    }

    # ── Step 2: Deploy (only if static validation passed) ────────────────
    if skip_deploy:
        print("\n[V11] Deployment skipped (skip_deploy=True)")
        deployment_result["reason"] = "skip_deploy=True"
    elif not project_path:
        print("\n[V11] Deployment skipped — no project path in result")
        deployment_result["reason"] = "missing project_path"
    elif not static_passed:
        print("\n[V11] Deployment skipped — static validation failed")
        deployment_result["reason"] = "static validation failed"
    else:
        print(f"\n[V11] Step 2/3 — Deploying to {deploy_provider}...")
        deploy_res = deploy_project(
            project_path=project_path,
            project_name=project_name,
            provider_name=deploy_provider,
        )
        deployment_result = {
            "skipped": False,
            "success": deploy_res.success,
            "url": deploy_res.url,
            "logs": deploy_res.logs,
            "error": deploy_res.error,
            "deploy_id": deploy_res.deploy_id,
            "provider": deploy_res.provider,
            "metadata": deploy_res.metadata,
        }

        # ── Step 3: Health check + deploy-error fix loop ─────────────────
        if deploy_res.success and deploy_res.url:
            print(f"\n[V11] Step 3/3 — Verifying deployment at {deploy_res.url}...")
            health = validate_deployment_with_retry(
                url=deploy_res.url,
                retries=4,
                wait=20,
            )
            health_report = health

            # If health check failed, try to parse and fix the error, then redeploy
            if not health["success"] and project_path:
                print(f"\n[V11] Health check failed — attempting deployment fix...")
                parsed_err = parse_deployment_error(
                    logs=deployment_result.get("logs", ""),
                    error=health.get("error"),
                    url=deploy_res.url,
                    health_report=health,
                )
                print(f"  [V11] Deployment error type: {parsed_err['type']} — {parsed_err['message']}")
                fix = generate_deployment_fix(
                    project_path=project_path,
                    parsed_error=parsed_err,
                    error_log=deployment_result.get("logs", ""),
                )
                if fix:
                    print(f"  [V11] Fix applied ({fix.get('path')}) — redeploying...")
                    redeploy_res = deploy_project(
                        project_path=project_path,
                        project_name=project_name + "_fixed",
                        provider_name=deploy_provider,
                    )
                    if redeploy_res.success and redeploy_res.url:
                        health = validate_deployment_with_retry(
                            url=redeploy_res.url,
                            retries=4,
                            wait=20,
                        )
                        health_report = {**health, "redeployed": True, "original_url": deploy_res.url}
                        deployment_result["url"] = redeploy_res.url
                        if health["success"]:
                            print(f"  [V11] Redeployment verified ✓  — {redeploy_res.url}")
                        else:
                            print(f"  [V11] Redeployment health check failed — {health.get('error')}")
                    else:
                        print(f"  [V11] Redeploy failed: {redeploy_res.error}")
                else:
                    print(f"  [V11] No auto-fix available for {parsed_err['type']}")
            elif health["success"]:
                print(f"  [V11] Deployment verified ✓  — {deploy_res.url}")
            else:
                print(f"  [V11] Health check failed — {health.get('error')}")
        else:
            # Deployment itself failed — parse error and try to fix
            parsed_err = parse_deployment_error(
                logs=deploy_res.logs or "",
                error=deploy_res.error,
            )
            print(f"  [V11] Deployment failed: {parsed_err['type']} — {parsed_err['message']}")
            if parsed_err.get("fixable") and project_path:
                fix = generate_deployment_fix(
                    project_path=project_path,
                    parsed_error=parsed_err,
                    error_log=deploy_res.logs or "",
                )
                if fix:
                    print(f"  [V11] Fix applied ({fix.get('path')}) — redeploying...")
                    redeploy_res = deploy_project(
                        project_path=project_path,
                        project_name=project_name,
                        provider_name=deploy_provider,
                    )
                    deployment_result = {
                        "skipped": False,
                        "success": redeploy_res.success,
                        "url": redeploy_res.url,
                        "logs": redeploy_res.logs,
                        "error": redeploy_res.error,
                        "deploy_id": redeploy_res.deploy_id,
                        "provider": redeploy_res.provider,
                        "metadata": redeploy_res.metadata,
                        "was_fixed": True,
                    }
                    if redeploy_res.success and redeploy_res.url:
                        health = validate_deployment_with_retry(
                            url=redeploy_res.url,
                            retries=4,
                            wait=20,
                        )
                        health_report = health
                        if health["success"]:
                            print(f"  [V11] Fixed deployment live at: {redeploy_res.url}")
                    else:
                        health_report = {"skipped": False, "success": False, "reason": "redeploy failed"}
                else:
                    health_report = {"skipped": False, "success": False, "reason": "deployment failed, no fix available"}
            else:
                health_report = {
                    "skipped": False,
                    "success": False,
                    "reason": "deployment failed, health check not attempted",
                }

    elapsed = round(time.time() - start, 2)

    # Record one outcome per run (not per error)
    deploy_succeeded = (
        not deployment_result.get("skipped")
        and deployment_result.get("success")
        and health_report.get("success")
    )
    if not deployment_result.get("skipped"):
        health_latency = (
            (health_report.get("checks") or {}).get("/health", {}).get("latency_ms")
        )
        # Collect all error types seen during this deployment attempt
        seen_errors = [
            e["type"] for e in deployment_result.get("parsed_errors", [])
            if isinstance(e, dict) and e.get("type")
        ]
        record_deployment_outcome(
            success=deploy_succeeded,
            health_latency_ms=health_latency,
            error_types=seen_errors,
        )

    deploy_score = _compute_deployment_score(deployment_result, health_report)

    # Re-compute forge score to include deployment component
    validation = v7_result.get("validation") or {"errors": []}
    runtime_result = v7_result.get("runtime") or {}
    forge_score_with_deploy = calculate_forge_score(
        validation=validation,
        runtime_result=runtime_result,
        deployment_result=deployment_result,
        health_report=health_report,
    )

    return {
        **v7_result,
        "forge_score": forge_score_with_deploy,
        "pipeline": "v11",
        "v11_extras": {
            "deploy_provider": deploy_provider,
            "deployment": deployment_result,
            "health_report": health_report,
            "deployment_score": deploy_score,
            "live_url": deployment_result.get("url"),
        },
        "live_url": deployment_result.get("url"),
        "generation_time_seconds": elapsed,
    }


def _compute_deployment_score(deployment: dict, health: dict) -> dict:
    """
    Deployment score component (0–100):
      Deployed successfully   +60
      Health check passed     +40
    """
    score = 0
    breakdown: dict[str, int] = {}

    if not deployment.get("skipped") and deployment.get("success"):
        score += 60
        breakdown["deployed"] = 60
    else:
        breakdown["deployed"] = 0

    if not health.get("skipped") and health.get("success"):
        score += 40
        breakdown["health_check"] = 40
    else:
        breakdown["health_check"] = 0

    return {"score": score, "breakdown": breakdown}
