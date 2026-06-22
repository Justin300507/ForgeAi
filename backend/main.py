from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.services.architect_service import generate_architecture
from app.services.backend_service import generate_backend
from app.services.frontend_service import generate_frontend
from app.services.project_service import generate_project
from app.services.planner_service import generate_plan

app = FastAPI(title="ForgeAI", version="12.0")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")


class ProjectIdea(BaseModel):
    idea: str
    provider: str = "auto"


class ArchitectureRequest(BaseModel):
    project_plan: dict


class BackendRequest(BaseModel):
    architecture: dict


class FrontendRequest(BaseModel):
    architecture: dict


@app.post("/generate")
def generate(project: ProjectIdea):
    return generate_plan(
        project.idea,
        project.provider
    )


@app.post("/architect")
def architect(request: ArchitectureRequest):
    return generate_architecture(request.project_plan)


@app.post("/backend")
def backend(request: BackendRequest):
    return generate_backend(request.architecture)


@app.post("/frontend")
def frontend(request: FrontendRequest):
    return generate_frontend(request.architecture)


@app.post("/project")
def project(project: ProjectIdea):
    return generate_project(
        project.idea,
        project.provider
    )


class V6Request(BaseModel):
    idea: str
    provider: str = "auto"
    use_parallel_backend: bool = True
    frontend_target: str = "web"   # "web" | "pwa"


@app.post("/project/v6")
def project_v6(request: V6Request):
    """
    V6 Multi-Agent Engineering Team.
    Stages: PM → Tech Lead → Architect → Backend Team (parallel)
            → Frontend → QA → Security → Validation → Runtime → Export

    frontend_target: "web" (default) or "pwa" (installable Progressive Web App)
    """
    from app.services.v6_orchestrator import generate_project_v6
    return generate_project_v6(
        request.idea,
        provider=request.provider,
        use_parallel_backend=request.use_parallel_backend,
        frontend_target=request.frontend_target,
    )


@app.post("/project/tournament")
def project_tournament(project: ProjectIdea):
    """V5.4 Architecture Tournament — 3 competing architectures, picks the best."""
    return generate_project(project.idea, project.provider, use_tournament=True)


@app.get("/cost/report")
def cost_report():
    """V5.7 Cost report — last 10 generation runs."""
    from app.utils.cost_tracker import load_cost_history
    return {"history": load_cost_history()[-10:]}


class V7Request(BaseModel):
    idea: str
    provider: str = "auto"
    run_improvement_cycle: bool = True
    improvement_cycle_every_n: int = 5
    frontend_target: str = "web"   # "web" | "pwa"


@app.post("/project/v7")
def project_v7(request: V7Request):
    """
    V7 Self-Improving AI Software Engineer.
    = V6 multi-agent pipeline + automatic improvement cycle every N runs.

    frontend_target: "web" (default Tailwind+React) or "pwa" (installable PWA with offline support)
    """
    from app.services.v7_orchestrator import generate_project_v7
    return generate_project_v7(
        request.idea,
        provider=request.provider,
        run_improvement_cycle=request.run_improvement_cycle,
        improvement_cycle_every_n=request.improvement_cycle_every_n,
        frontend_target=request.frontend_target,
    )


class BenchmarkRequest(BaseModel):
    ideas: list[str]
    provider: str = "auto"


@app.post("/benchmark/v7")
def benchmark_v7(request: BenchmarkRequest):
    """V7 Benchmark — measure learning effectiveness, regression rate, cost efficiency."""
    from app.services.v7_orchestrator import run_v7_benchmark
    return run_v7_benchmark(request.ideas, provider=request.provider)


@app.post("/improve")
def run_improvement_cycle(provider: str = "auto"):
    """Trigger the V7 improvement cycle manually (Research → Rule Evolution → Prompt Optimizer)."""
    from app.services.v7_orchestrator import run_improvement_cycle_v7
    return run_improvement_cycle_v7(provider=provider)


@app.get("/leaderboard")
def improvement_leaderboard():
    """V7 Improvement Leaderboard — every improvement ranked by benchmark impact."""
    from app.services.improvement_leaderboard_service import get_leaderboard
    return {"leaderboard": get_leaderboard(top_n=20)}


@app.get("/research/latest")
def latest_research():
    """Latest findings from the Autonomous Research Agent."""
    from app.services.research_agent_service import get_latest_findings
    return get_latest_findings() or {"message": "No research findings yet — run /improve first"}


@app.get("/dataset/stats")
def dataset_stats():
    """V7 Dataset stats — runs per version, avg score, pass rate."""
    from app.services.failure_dataset_service import get_dataset_stats
    return get_dataset_stats()


@app.get("/benchmark/comparison")
def benchmark_comparison():
    """V7 Benchmark comparison — V5 vs V6 vs V7 side-by-side."""
    from app.services.benchmark_comparison_service import compare_versions
    comparison = compare_versions()
    return {
        "best_version": comparison.best_version,
        "regression_detected": comparison.regression_detected,
        "regression_details": comparison.regression_details,
        "improvement_from_v5": comparison.improvement_from_v5,
        "improvement_velocity": comparison.improvement_velocity,
        "versions": {
            v: {
                "run_count": m.run_count,
                "avg_score": m.avg_score,
                "pass_rate": m.pass_rate,
                "security_score": m.security_score,
                "performance_score": m.performance_score,
                "maintainability_score": m.maintainability_score,
                "top_failure_types": m.top_failure_types,
            }
            for v, m in comparison.versions.items()
        },
    }


class V8Request(BaseModel):
    idea: str
    run_improvement_cycle: bool = False
    skip_reviews: bool = True


@app.post("/project/v8")
def project_v8(request: V8Request):
    """
    V8 — Google Gemini Pipeline.
    Full V7 pipeline using Google Gemini-2.5-Flash for all generation stages.
    Useful when Cerebras credits are exhausted or for Gemini quality benchmarking.
    Requires GEMINI_API_KEY in .env.
    """
    from app.services.v8_orchestrator import generate_project_v8
    return generate_project_v8(
        idea=request.idea,
        run_improvement_cycle=request.run_improvement_cycle,
        skip_reviews=request.skip_reviews,
    )


class V9Request(BaseModel):
    idea: str
    run_improvement_cycle: bool = False
    skip_reviews: bool = True


@app.post("/project/v9")
def project_v9(request: V9Request):
    """
    V9 — OpenAI ChatGPT Pipeline.
    Full V7 pipeline using OpenAI GPT-4o-mini (GPT-4o fallback) for all generation stages.
    Requires OPENAI_API_KEY in .env.
    """
    from app.services.v9_orchestrator import generate_project_v9
    return generate_project_v9(
        idea=request.idea,
        run_improvement_cycle=request.run_improvement_cycle,
        skip_reviews=request.skip_reviews,
    )


class V10Request(BaseModel):
    idea: str
    run_improvement_cycle: bool = False
    skip_reviews: bool = False


@app.post("/project/v10")
def project_v10(request: V10Request):
    """
    V10 — Smart Multi-Provider Pipeline.
    Uses the best available provider for each stage with automatic fallback.
    Provider chain: Cerebras → Groq → OpenRouter → OpenAI → Gemini → Ollama.
    Full reviews enabled by default for maximum quality.
    """
    from app.services.v10_orchestrator import generate_project_v10
    return generate_project_v10(
        idea=request.idea,
        run_improvement_cycle=request.run_improvement_cycle,
        skip_reviews=request.skip_reviews,
    )


class V11Request(BaseModel):
    idea: str
    provider: str = "auto"
    deploy_provider: str = "railway"
    run_improvement_cycle: bool = False
    skip_reviews: bool = True
    skip_deploy: bool = False
    frontend_target: str = "web"   # "web" | "pwa"


@app.post("/project/v11")
def project_v11(request: V11Request):
    """
    V11 — Autonomous Deployment Platform.
    Generates, validates, deploys to Railway, and returns a live URL.

    frontend_target: "web" (Tailwind+React) or "pwa" (installable PWA, works offline)

    Requires:
      - RAILWAY_TOKEN in backend/.env
      - Railway CLI: npm install -g @railway/cli
    """
    from app.services.v11_orchestrator import generate_project_v11
    return generate_project_v11(
        idea=request.idea,
        provider=request.provider,
        deploy_provider=request.deploy_provider,
        run_improvement_cycle=request.run_improvement_cycle,
        skip_reviews=request.skip_reviews,
        skip_deploy=request.skip_deploy,
        frontend_target=request.frontend_target,
    )


@app.get("/deployments")
def deployment_history():
    """V11 — List recent deployments with URLs and health status."""
    from app.services.deployment_service import get_deployment_history
    return {"deployments": get_deployment_history(limit=20)}


@app.get("/deployment/leaderboard")
def deployment_leaderboard():
    """
    V11 Deployment Improvement Leaderboard.
    7-day vs 30-day success rates, improvement velocity, best/worst fix patterns.
    This is how you know ForgeAI is genuinely learning — not just running more generations.
    """
    from app.services.deployment_fix_service import get_deployment_leaderboard
    return get_deployment_leaderboard()


@app.get("/deployment/stats")
def deployment_stats():
    """
    V11 Deployment Benchmark.
    Returns total/successful/failed, success_rate, top errors, and per-error fix rates.
    A generated app scores 100 only when it builds, runs, deploys, and /health is fast.
    """
    from app.services.deployment_fix_service import get_deployment_stats
    return get_deployment_stats()


@app.get("/deployment/memory")
def deployment_memory():
    """V11 — Raw deployment error memory (per-error seen/fixed counts)."""
    from app.services.deployment_fix_service import get_deployment_memory_summary
    summary = get_deployment_memory_summary()
    errors = summary.get("errors", {})
    # Format as readable table: { "PortError": "12 → fixed 11 (91.7%)", ... }
    formatted = {}
    for err_type, v in sorted(errors.items(), key=lambda x: x[1].get("seen", 0), reverse=True):
        seen = v.get("seen", 0)
        fixed = v.get("fixed", 0)
        fix_rate = round(fixed / seen * 100, 1) if seen else 0.0
        formatted[err_type] = f"{seen} seen → {fixed} fixed ({fix_rate}%)"
    return {
        "total_deployments": summary.get("total_deployments", 0),
        "successful_deployments": summary.get("successful_deployments", 0),
        "avg_health_latency_ms": round(summary.get("avg_health_latency_ms", 0)),
        "error_fix_summary": formatted,
    }


class V12Request(BaseModel):
    idea: str
    provider: str = "auto"
    deploy_provider: str = "railway"
    run_improvement_cycle: bool = False
    skip_reviews: bool = True
    metrics_requests: int = 5
    skip_evolution: bool = False


@app.post("/project/v12")
def project_v12(request: V12Request):
    """
    V12 — Continuous Product Evolution.
    Generates, deploys, measures live metrics, evolves the code, and redeploys.

    Pipeline: Generate → Deploy → Metrics → LLM Analysis → Regenerate → Redeploy
    Returns: v1 URL, v2 URL (if evolved), metrics delta, evolution plan.

    Requires RAILWAY_TOKEN in backend/.env and Railway CLI installed.
    """
    from app.services.v12_orchestrator import generate_project_v12
    return generate_project_v12(
        idea=request.idea,
        provider=request.provider,
        deploy_provider=request.deploy_provider,
        run_improvement_cycle=request.run_improvement_cycle,
        skip_reviews=request.skip_reviews,
        metrics_requests=request.metrics_requests,
        skip_evolution=request.skip_evolution,
    )


@app.get("/health")
def health():
    return {"status": "ok", "version": "12.0"}