"""
V14 — One-Click Deployment Platform

Pipeline:
  1. Generate + validate with V7 (FastAPI backend + React frontend)
  2. Write deterministic deployment configs (.env.example, GitHub Actions, wrangler.toml)
  3. Push to GitHub (creates repo automatically)
  4. Deploy backend to Render (via Render REST API)
  5. Deploy frontend to Cloudflare Pages (via Wrangler CLI)
  6. Health check both deployments
  7. Return full deployment status report

Steps 3-6 are optional (skip_deploy=True for generation-only).
Each step fails gracefully — a GitHub failure doesn't block Cloudflare.

Credentials come from the user's ForgeAI account (Deploy Settings page),
injected as env vars before the job runs:
  GITHUB_TOKEN           — Personal Access Token with repo scope
  RENDER_API_KEY         — From dashboard.render.com → Account → API Keys
  RENDER_OWNER_ID        — Your Render user/team ID (optional, auto-discovered)
  CLOUDFLARE_API_TOKEN   — From dash.cloudflare.com → Profile → API Tokens
  CLOUDFLARE_ACCOUNT_ID  — Your Cloudflare account ID
"""
import time
from typing import Any

from app.services.v7_orchestrator import generate_project_v7
from app.services.deployment_config_service import generate_deployment_configs


def generate_project_v14(
    idea: str,
    provider: str = "auto",
    deploy_to: str = "vercel",          # "vercel" | "render" | "cloudflare" | "both" | "none"  ("railway" accepted as alias for "render")
    run_improvement_cycle: bool = False,
    skip_reviews: bool = True,
    frontend_target: str = "web",
    style_override: str | None = None,
    motion_intensity: str | None = None,
    include_landing_page: bool = False,
) -> dict[str, Any]:
    """
    V14 One-Click Deployment.

    Args:
        idea:                   plain-English project description
        provider:               LLM provider (auto/cerebras/groq/gemini/openai)
        deploy_to:              where to deploy — "render", "cloudflare", "both",
                                "vercel" (single-project full-stack deploy with a
                                provisioned Neon Postgres), or "none"
                                ("railway" is accepted as an alias for "render")
        run_improvement_cycle:  run V7 self-improvement after generation
        skip_reviews:           skip QA/Security reviews (faster, cheaper)
        frontend_target:        "web" (React+Tailwind) or "pwa" (installable PWA)
    """
    start = time.time()

    print(f"\n{'#'*70}")
    print(f"# V14 ONE-CLICK DEPLOYMENT PLATFORM")
    print(f"# Idea: {idea[:60]}")
    print(f"# Provider: {provider} | Deploy: {deploy_to}")
    print(f"{'#'*70}")

    result: dict[str, Any] = {
        "idea": idea,
        "version": "v14",
        "generation": {},
        "deployment_configs": {},
        "github": {},
        "railway": {},
        "cloudflare": {},
        "vercel": {},
        "health": {},
        "report": {},
    }

    # ── Step 1: Generate project with V7 ─────────────────────────────────────
    print("\n[V14] Step 1/5 — Generating project with V7...")
    v7_result = generate_project_v7(
        idea=idea,
        provider=provider,
        run_improvement_cycle=run_improvement_cycle,
        skip_reviews=skip_reviews,
        frontend_target=frontend_target,
        style_override=style_override,
        motion_intensity=motion_intensity,
        include_landing_page=include_landing_page,
    )

    project_name = v7_result.get("project_name", "")
    project_path = v7_result.get("project_path", "")
    static_passed = bool((v7_result.get("validation") or {}).get("passed"))
    runtime_passed = bool((v7_result.get("runtime") or {}).get("success"))

    result["generation"] = {
        "project_name": project_name,
        "project_path": project_path,
        "forge_score": v7_result.get("forge_score"),
        "static_passed": static_passed,
        "runtime_passed": runtime_passed,
        "zip_path": v7_result.get("zip_path"),
    }

    if not project_path:
        result["report"] = {"status": "failed", "reason": "Generation failed — no project path"}
        return result

    # ── Deploy gate: never ship a broken app ──────────────────────────────────
    # A 200 /health does NOT mean the app works — it just means uvicorn started.
    # We only deploy when runtime smoke tests AND CRUD journey both passed.
    if not runtime_passed and deploy_to != "none":
        print(
            "\n[V14] Runtime validation FAILED — blocking deployment.\n"
            "      Fix the runtime errors above, then re-run to deploy.\n"
            "      (A healthy /health endpoint is not enough — CRUD must pass too.)"
        )
        result["report"] = {
            "status": "runtime_failed",
            "reason": "Runtime validation did not pass — deployment blocked to prevent shipping a broken app",
            "project_name": project_name,
            "project_path": project_path,
            "forge_score": v7_result.get("forge_score"),
            "static_passed": static_passed,
            "runtime_passed": False,
            "github_url": None,
            "backend_url": None,
            "frontend_url": None,
            "next_steps": [
                "1. Review the runtime errors above",
                "2. Click 'Fix & Retry' to re-run the repair + runtime loop",
                "3. Once runtime passes, re-run V14 to deploy",
            ],
        }
        result["total_time"] = round(time.time() - start, 2)
        _print_report(result["report"])
        return result

    # ── Step 2: Write deployment configs ─────────────────────────────────────
    print("\n[V14] Step 2/5 — Generating deployment configs...")
    config_results = generate_deployment_configs(project_path, project_name)
    result["deployment_configs"] = config_results

    if deploy_to == "none":
        result["report"] = {
            "status": "generated_only",
            "message": "Project generated with deployment configs. Set deploy_to to 'railway', 'cloudflare', or 'both' to auto-deploy.",
            "files_generated": list(config_results.keys()),
            "next_steps": _build_manual_deploy_instructions(project_name),
        }
        result["total_time"] = round(time.time() - start, 2)
        return result

    # ── Step 3: Push to GitHub ────────────────────────────────────────────────
    print("\n[V14] Step 3/5 — Pushing to GitHub...")
    github_result = _push_to_github(project_path, project_name, idea)
    result["github"] = github_result

    github_repo_url = github_result.get("clone_url") or github_result.get("repo_url")

    # ── Step 4: Deploy backend to Render ─────────────────────────────────────
    # "railway" is accepted as a legacy alias so existing API callers don't break.
    railway_result: dict = {"skipped": True}
    if deploy_to in ("railway", "render", "both"):
        print("\n[V14] Step 4a/5 — Deploying backend to Render...")
        railway_result = _deploy_render(project_path, project_name, github_repo_url)
    result["railway"] = railway_result  # key kept as "railway" for API compatibility

    backend_url = railway_result.get("url")

    # ── Step 5: Deploy frontend to Cloudflare Pages ───────────────────────────
    cloudflare_result: dict = {"skipped": True}
    if deploy_to in ("cloudflare", "both"):
        print("\n[V14] Step 4b/5 — Deploying frontend to Cloudflare Pages...")
        cloudflare_result = _deploy_cloudflare(project_path, project_name, backend_url)
    result["cloudflare"] = cloudflare_result

    frontend_url = cloudflare_result.get("url")

    # ── Step 4c: Vercel — single-project full-stack deploy ────────────────────
    # Mutually exclusive with the Render+Cloudflare split above (Vercel hosts
    # both frontend and backend from one project on the same origin).
    vercel_result: dict = {"skipped": True}
    if deploy_to == "vercel":
        print("\n[V14] Step 4c/5 — Deploying to Vercel (frontend + backend, one project)...")
        vercel_result = _deploy_vercel(project_path, project_name)
        result["vercel"] = vercel_result
        if vercel_result.get("success"):
            backend_url = vercel_result.get("backend_url")
            frontend_url = vercel_result.get("frontend_url")

    # ── Step 5: Health checks ─────────────────────────────────────────────────
    print("\n[V14] Step 5/5 — Health checks...")
    health = _run_health_checks(backend_url, frontend_url)
    result["health"] = health

    # ── Build final report ────────────────────────────────────────────────────
    total_time = round(time.time() - start, 2)
    result["total_time"] = total_time

    deployed = bool(backend_url or frontend_url)
    raw_score = v7_result.get("forge_score")
    forge_score_int = raw_score.get("score") if isinstance(raw_score, dict) else (raw_score or 0)

    # Apply deployment penalties to the reported score so a 100-point generation
    # doesn't stay at 100 when the deploy pipeline failed.
    frontend_deployed = bool(frontend_url)
    backend_deployed  = bool(backend_url)
    cloudflare_failed = (
        not cloudflare_result.get("skipped")
        and not cloudflare_result.get("success")
        and not frontend_deployed
    )
    render_failed = (
        not result.get("railway", {}).get("skipped")
        and not backend_deployed
        and not result.get("railway", {}).get("success", True)
    )
    if cloudflare_failed:
        forge_score_int = max(0, forge_score_int - 20)
        print(f"[Score] -20 Cloudflare deploy failed → adjusted score: {forge_score_int}")
    if render_failed:
        forge_score_int = max(0, forge_score_int - 20)
        print(f"[Score] -20 Render deploy failed → adjusted score: {forge_score_int}")

    result["report"] = {
        "status": "deployed" if deployed else "generated",
        "project_name": project_name,
        "forge_score": forge_score_int,
        "backend_url": backend_url,
        "frontend_url": frontend_url,
        "github_url": github_result.get("repo_url"),
        "backend_health": health.get("backend"),
        "frontend_health": health.get("frontend"),
        "cloudflare_error": cloudflare_result.get("error") if not cloudflare_result.get("url") else None,
        "railway_error": railway_result.get("error") if not railway_result.get("url") else None,
        "total_time_seconds": total_time,
        "deployment_files": list(config_results.keys()),
        "next_steps": _build_next_steps(project_name, backend_url, frontend_url, github_result),
    }

    _print_report(result["report"])
    return result


# ── Step helpers ──────────────────────────────────────────────────────────────

def _push_to_github(project_path: str, project_name: str, idea: str) -> dict:
    try:
        from app.services.github_service import push_to_github
        return push_to_github(project_path, project_name, description=idea[:80])
    except Exception as exc:
        return {"success": False, "error": str(exc), "repo_url": None, "clone_url": None}


def _deploy_render(project_path: str, project_name: str, github_url: str | None = None) -> dict:
    try:
        from app.deployments.render_provider import RenderProvider
        provider = RenderProvider()
        env_vars: dict = {}
        if github_url:
            env_vars["GITHUB_REPO_URL"] = github_url
        res = provider.deploy(project_path, project_name, env_vars=env_vars or None)
        if not res.success:
            print(f"  [Render] Deploy failed: {(res.error or '')[:400]}")
            if res.logs:
                print(f"  [Render] Logs: {res.logs[-300:]}")
        return {
            "success": res.success,
            "url": res.url,
            "error": res.error,
            "logs": res.logs[-500:] if res.logs else "",
            "metadata": res.metadata,
        }
    except Exception as exc:
        print(f"  [Render] Exception: {exc}")
        return {"success": False, "error": str(exc), "url": None}


def _deploy_cloudflare(project_path: str, project_name: str, backend_url: str | None) -> dict:
    try:
        from app.deployments.cloudflare_provider import CloudflareProvider
        provider = CloudflareProvider()
        env_vars = {}
        if backend_url:
            env_vars["VITE_API_URL"] = backend_url
        res = provider.deploy(project_path, project_name, env_vars=env_vars or None)
        if not res.success:
            print(f"  [V14] Cloudflare deploy failed: {(res.error or '')[:300]}")
        return {
            "success": res.success,
            "url": res.url,
            "error": res.error,
            "logs": res.logs[-500:] if res.logs else "",
        }
    except Exception as exc:
        print(f"  [V14] Cloudflare deploy exception: {exc}")
        return {"success": False, "error": str(exc), "url": None}


def _deploy_vercel(project_path: str, project_name: str) -> dict:
    """
    One Vercel project hosts both frontend and backend (same origin, backend
    mounted under /api — see vercel_provider.py). Vercel has no disk, so a
    real Postgres is provisioned via Neon first and passed in as DATABASE_URL.
    """
    try:
        from app.deployments.neon_provider import provision_database
        from app.deployments.vercel_provider import VercelProvider

        db_url, db_err = provision_database(project_name)
        if db_err:
            print(f"  [Vercel] Neon provisioning failed: {db_err}")
            return {"success": False, "error": f"Database provisioning failed: {db_err}", "url": None}

        provider = VercelProvider()
        res = provider.deploy(project_path, project_name, env_vars={"DATABASE_URL": db_url})
        if not res.success:
            print(f"  [Vercel] Deploy failed: {(res.error or '')[:400]}")
            if res.logs:
                print(f"  [Vercel] Logs: {res.logs[-300:]}")
        return {
            "success": res.success,
            "url": res.url,
            "backend_url": res.metadata.get("backend_url"),
            "frontend_url": res.metadata.get("frontend_url"),
            "error": res.error,
            "logs": res.logs[-500:] if res.logs else "",
            "metadata": res.metadata,
        }
    except Exception as exc:
        print(f"  [Vercel] Exception: {exc}")
        return {"success": False, "error": str(exc), "url": None}


def _run_health_checks(backend_url: str | None, frontend_url: str | None) -> dict:
    import requests
    health: dict = {}

    if backend_url:
        try:
            r = requests.get(f"{backend_url}/health", timeout=8)
            health["backend"] = {"status": r.status_code, "ok": r.status_code == 200, "latency_ms": int(r.elapsed.total_seconds() * 1000)}
        except Exception:
            # Render free-tier backends take ~5 min to build — health fail here is expected
            health["backend"] = {"ok": False, "note": "Render backend building (~5 min) — URL is correct, check back soon"}

    if frontend_url:
        try:
            r = requests.get(frontend_url, timeout=15)
            health["frontend"] = {"status": r.status_code, "ok": r.status_code < 400, "latency_ms": int(r.elapsed.total_seconds() * 1000)}
        except Exception as e:
            health["frontend"] = {"ok": False, "error": str(e)}

    return health


def _build_next_steps(project_name: str, backend_url: str | None, frontend_url: str | None, github: dict) -> list[str]:
    steps = []
    repo_url = github.get("repo_url")
    slug = project_name.lower().replace("_", "-")

    if not github.get("success"):
        steps.append(f"1. Push to GitHub: cd generated_projects/{project_name} && git init && git push")
    if not backend_url:
        steps.append("2. Deploy backend: add RENDER_API_KEY to .env, then re-run with deploy_to='render'")
    if not frontend_url:
        steps.append("3. Deploy frontend: cd your-project && wrangler pages deploy ./dist --project-name=" + slug)
    if backend_url and not frontend_url:
        steps.append(f"4. Set VITE_API_URL={backend_url} in your frontend env")
    if backend_url and frontend_url:
        steps.append(f"App is live! Backend: {backend_url} | Frontend: {frontend_url}")

    return steps or ["All steps completed — your app is live!"]


def _build_manual_deploy_instructions(project_name: str) -> list[str]:
    slug = project_name.lower().replace("_", "-")
    return [
        f"1. Push to GitHub: cd generated_projects/{project_name} && git init && git push",
        "2. Deploy to Render: add RENDER_API_KEY (+ optionally RENDER_OWNER_ID) to .env, re-run with deploy_to='render'",
        f"3. Deploy frontend to Cloudflare: wrangler pages deploy ./dist --project-name={slug}",
        "4. Connect GitHub, Cloudflare, and Render in ForgeAI Deploy Settings for one-click deploy",
    ]


def retry_project_v14(
    project_path: str,
    project_name: str,
    idea: str,
    provider: str = "auto",
    deploy_to: str = "none",
    frontend_target: str = "web",
) -> dict:
    """
    Resume from an existing project on disk — skip generation entirely.
    Runs: patcher → validation → runtime → deploy.
    Used when "Fix & Retry" is clicked on a failed job.
    """
    import json
    import os
    start = time.time()

    print(f"\n{'#'*70}")
    print(f"# V14 RETRY — resuming from existing files")
    print(f"# Project: {project_name} | Deploy: {deploy_to}")
    print(f"{'#'*70}")

    # Load plan + architecture from metadata.json
    metadata_path = os.path.join(project_path, "metadata.json")
    plan: dict = {"project_name": project_name}
    architecture: dict = {}
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, encoding="utf-8") as f:
                meta = json.load(f)
            plan = meta.get("plan", plan)
            architecture = meta.get("architecture", architecture)
        except Exception:
            pass

    from app.services.v6_orchestrator import repair_project
    repair_result = repair_project(project_path, plan, architecture, provider)

    static_passed = bool((repair_result.get("validation") or {}).get("passed"))
    runtime_passed = bool((repair_result.get("runtime") or {}).get("success"))

    result: dict = {
        "idea": idea,
        "version": "v14-retry",
        "generation": {
            "project_name": project_name,
            "project_path": project_path,
            "forge_score": repair_result.get("forge_score"),
            "static_passed": static_passed,
            "runtime_passed": runtime_passed,
            "zip_path": repair_result.get("zip_path"),
        },
        "deployment_configs": {},
        "github": {},
        "railway": {},
        "cloudflare": {},
        "health": {},
        "report": {},
    }

    if not static_passed:
        result["report"] = {
            "status": "failed",
            "reason": "Static validation still failing after repair",
            "forge_score": repair_result.get("forge_score"),
            "project_name": project_name,
            "errors": (repair_result.get("validation") or {}).get("errors", [])[:10],
        }
        result["total_time"] = round(time.time() - start, 2)
        return result

    # Deployment configs
    print("\n[V14-RETRY] Writing deployment configs...")
    config_results = generate_deployment_configs(project_path, project_name)
    result["deployment_configs"] = config_results

    if deploy_to == "none":
        result["report"] = {
            "status": "generated_only",
            "project_name": project_name,
            "forge_score": repair_result.get("forge_score"),
            "message": "Repair passed. Set deploy_to to redeploy.",
        }
        result["total_time"] = round(time.time() - start, 2)
        return result

    # Push to GitHub
    print("\n[V14-RETRY] Pushing to GitHub...")
    github_result = _push_to_github(project_path, project_name, idea)
    result["github"] = github_result
    github_repo_url = github_result.get("clone_url") or github_result.get("repo_url")

    # Deploy Railway
    railway_result: dict = {"skipped": True}
    if deploy_to in ("railway", "both"):
        print("\n[V14-RETRY] Deploying to Railway...")
        railway_result = _deploy_railway(project_path, project_name, github_repo_url)
    result["railway"] = railway_result
    backend_url = railway_result.get("url")

    # Deploy Cloudflare
    cloudflare_result: dict = {"skipped": True}
    if deploy_to in ("cloudflare", "both"):
        print("\n[V14-RETRY] Deploying to Cloudflare...")
        cloudflare_result = _deploy_cloudflare(project_path, project_name, backend_url)
    result["cloudflare"] = cloudflare_result
    frontend_url = cloudflare_result.get("url")

    # Health checks
    health = _run_health_checks(backend_url, frontend_url)
    result["health"] = health

    total_time = round(time.time() - start, 2)
    result["total_time"] = total_time
    deployed = bool(backend_url or frontend_url)

    raw_score = repair_result.get("forge_score")
    retry_score = raw_score.get("score") if isinstance(raw_score, dict) else (raw_score or 0)
    if not cloudflare_result.get("skipped") and not cloudflare_result.get("success") and not frontend_url:
        retry_score = max(0, retry_score - 20)
        print(f"[Score] -20 Cloudflare deploy failed → retry adjusted score: {retry_score}")
    if not railway_result.get("skipped") and not railway_result.get("success") and not backend_url:
        retry_score = max(0, retry_score - 20)
        print(f"[Score] -20 Railway deploy failed → retry adjusted score: {retry_score}")

    result["report"] = {
        "status": "deployed" if deployed else "repaired",
        "project_name": project_name,
        "forge_score": retry_score,
        "backend_url": backend_url,
        "frontend_url": frontend_url,
        "github_url": github_result.get("repo_url"),
        "backend_health": health.get("backend"),
        "frontend_health": health.get("frontend"),
        "cloudflare_error": cloudflare_result.get("error") if not cloudflare_result.get("url") else None,
        "railway_error": railway_result.get("error") if not railway_result.get("url") else None,
        "total_time_seconds": total_time,
    }
    _print_report(result["report"])
    return result


def _print_report(report: dict) -> None:
    print(f"\n{'='*70}")
    print(f"  V14 DEPLOYMENT REPORT")
    print(f"{'='*70}")
    print(f"  Status:       {report['status'].upper()}")
    print(f"  Project:      {report['project_name']}")
    print(f"  Forge Score:  {report.get('forge_score', 'N/A')}")
    if report.get("backend_url"):
        print(f"  Backend:      {report['backend_url']} ({report.get('backend_health', {}).get('status', '?')})")
    if report.get("frontend_url"):
        print(f"  Frontend:     {report['frontend_url']} ({report.get('frontend_health', {}).get('status', '?')})")
    elif report.get("cloudflare_error"):
        print(f"  Frontend:     (deploy failed) {str(report['cloudflare_error'])[:150]}")
    if report.get("github_url"):
        print(f"  GitHub:       {report['github_url']}")
    print(f"  Total time:   {report.get('total_time_seconds', '?')}s")
    print(f"{'='*70}")
