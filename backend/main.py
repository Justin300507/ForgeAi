from fastapi import FastAPI
from pydantic import BaseModel

from app.services.architect_service import generate_architecture
from app.services.backend_service import generate_backend
from app.services.frontend_service import generate_frontend
from app.services.project_service import generate_project
from app.services.planner_service import generate_plan

app = FastAPI(title="ForgeAI", version="7.0")


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


@app.post("/project/v6")
def project_v6(request: V6Request):
    """
    V6 Multi-Agent Engineering Team.
    Stages: PM → Tech Lead → Architect → Backend Team (parallel)
            → Frontend → QA → Security → Validation → Runtime → Export
    """
    from app.services.v6_orchestrator import generate_project_v6
    return generate_project_v6(
        request.idea,
        provider=request.provider,
        use_parallel_backend=request.use_parallel_backend,
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


@app.post("/project/v7")
def project_v7(request: V7Request):
    """
    V7 Self-Improving AI Software Engineer.
    = V6 multi-agent pipeline + automatic improvement cycle every N runs.
    The improvement cycle runs: Research Agent → Rule Evolution → Prompt Optimizer → Leaderboard.
    """
    from app.services.v7_orchestrator import generate_project_v7
    return generate_project_v7(
        request.idea,
        provider=request.provider,
        run_improvement_cycle=request.run_improvement_cycle,
        improvement_cycle_every_n=request.improvement_cycle_every_n,
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


@app.get("/health")
def health():
    return {"status": "ok", "version": "7.0"}