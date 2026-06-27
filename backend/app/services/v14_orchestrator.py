"""
V14 — One-Click Deployment Platform

Pipeline:
  1. Generate + validate with V7 (FastAPI backend + React frontend)
  2. Write deterministic deployment configs (render.yaml, .env.example, GitHub Actions, wrangler.toml)
  3. Push to GitHub (creates repo automatically)
  4. Deploy backend to Render (via Render REST API)
  5. Deploy frontend to Cloudflare Pages (via Wrangler CLI)
  6. Health check both deployments
  7. Return full deployment status report

Steps 3-6 are optional (skip_deploy=True for generation-only).
Each step fails gracefully — a GitHub failure doesn't block Cloudflare.

Required env vars in backend/.env:
  GITHUB_TOKEN       — Personal Access Token with repo scope
  RENDER_API_KEY     — From dashboard.render.com → Account → API Keys
  RENDER_OWNER_ID    — Your Render user/team ID
  CLOUDFLARE_API_TOKEN — From dash.cloudflare.com → Profile → API Tokens
  CLOUDFLARE_ACCOUNT_ID — Your Cloudflare account ID
"""
import time
from typing import Any

from app.services.v7_orchestrator import generate_project_v7
from app.services.deployment_config_service import generate_deployment_configs


def generate_project_v14(
    idea: str,
    provider: str = "auto",
    deploy_to: str = "both",          # "render" | "cloudflare" | "both" | "none"
    run_improvement_cycle: bool = False,
    skip_reviews: bool = True,
    frontend_target: str = "web",
) -> dict[str, Any]:
    """
    V14 One-Click Deployment.

    Args:
        idea:                   plain-English project description
        provider:               LLM provider (auto/cerebras/groq/gemini/openai)
        deploy_to:              where to deploy — "render", "cloudflare", "both", or "none"
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
        "render": {},
        "cloudflare": {},
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

    # ── Step 2: Write deployment configs ─────────────────────────────────────
    print("\n[V14] Step 2/5 — Generating deployment configs...")
    config_results = generate_deployment_configs(project_path, project_name)
    result["deployment_configs"] = config_results

    if deploy_to == "none":
        result["report"] = {
            "status": "generated_only",
            "message": "Project generated with deployment configs. Set deploy_to to 'render', 'cloudflare', or 'both' to auto-deploy.",
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
    render_result: dict = {"skipped": True}
    if deploy_to in ("render", "both"):
        print("\n[V14] Step 4a/5 — Deploying backend to Render...")
        render_result = _deploy_render(project_path, project_name, github_repo_url)
    result["render"] = render_result

    backend_url = render_result.get("backend_url") or render_result.get("url")

    # ── Step 5: Deploy frontend to Cloudflare Pages ───────────────────────────
    cloudflare_result: dict = {"skipped": True}
    if deploy_to in ("cloudflare", "both"):
        print("\n[V14] Step 4b/5 — Deploying frontend to Cloudflare Pages...")
        cloudflare_result = _deploy_cloudflare(project_path, project_name, backend_url)
    result["cloudflare"] = cloudflare_result

    frontend_url = cloudflare_result.get("url")

    # ── Step 5: Health checks ─────────────────────────────────────────────────
    print("\n[V14] Step 5/5 — Health checks...")
    health = _run_health_checks(backend_url, frontend_url)
    result["health"] = health

    # ── Build final report ────────────────────────────────────────────────────
    total_time = round(time.time() - start, 2)
    result["total_time"] = total_time

    deployed = bool(backend_url or frontend_url)
    result["report"] = {
        "status": "deployed" if deployed else "generated",
        "project_name": project_name,
        "forge_score": v7_result.get("forge_score"),
        "backend_url": backend_url,
        "frontend_url": frontend_url,
        "github_url": github_result.get("repo_url"),
        "backend_health": health.get("backend"),
        "frontend_health": health.get("frontend"),
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
        return push_to_github(project_path, project_name, description=f"Generated by ForgeAI: {idea[:80]}")
    except Exception as exc:
        return {"success": False, "error": str(exc), "repo_url": None, "clone_url": None}


def _deploy_render(project_path: str, project_name: str, repo_url: str | None) -> dict:
    try:
        from app.deployments.render_provider import RenderProvider
        provider = RenderProvider()
        env_vars = {}
        if repo_url:
            env_vars["GITHUB_REPO_URL"] = repo_url
        res = provider.deploy(project_path, project_name, env_vars=env_vars or None)
        return {
            "success": res.success,
            "url": res.url,
            "backend_url": (res.metadata or {}).get("backend_url"),
            "frontend_url": (res.metadata or {}).get("frontend_url"),
            "error": res.error,
            "logs": res.logs[-500:] if res.logs else "",
            "metadata": res.metadata,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "url": None}


def _deploy_cloudflare(project_path: str, project_name: str, backend_url: str | None) -> dict:
    try:
        from app.deployments.cloudflare_provider import CloudflareProvider
        provider = CloudflareProvider()
        env_vars = {}
        if backend_url:
            env_vars["VITE_API_URL"] = backend_url
        res = provider.deploy(project_path, project_name, env_vars=env_vars or None)
        return {
            "success": res.success,
            "url": res.url,
            "error": res.error,
            "logs": res.logs[-500:] if res.logs else "",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "url": None}


def _run_health_checks(backend_url: str | None, frontend_url: str | None) -> dict:
    import requests
    health: dict = {}

    if backend_url:
        try:
            for _ in range(3):
                try:
                    r = requests.get(f"{backend_url}/health", timeout=15)
                    health["backend"] = {"status": r.status_code, "ok": r.status_code == 200, "latency_ms": int(r.elapsed.total_seconds() * 1000)}
                    break
                except Exception:
                    time.sleep(5)
            else:
                health["backend"] = {"status": 0, "ok": False, "latency_ms": None, "error": "timeout"}
        except Exception as e:
            health["backend"] = {"ok": False, "error": str(e)}

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
        steps.append("2. Deploy backend: go to render.com → New → Blueprint → connect your GitHub repo")
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
        "2. Deploy to Render: go to render.com → New → Blueprint → connect repo (uses render.yaml)",
        f"3. Deploy frontend to Cloudflare: wrangler pages deploy ./dist --project-name={slug}",
        "4. Add RENDER_API_KEY, RENDER_OWNER_ID, GITHUB_TOKEN, CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID to backend/.env for one-click deploy",
    ]


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
    if report.get("github_url"):
        print(f"  GitHub:       {report['github_url']}")
    print(f"  Total time:   {report.get('total_time_seconds', '?')}s")
    print(f"{'='*70}")
