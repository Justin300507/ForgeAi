import sys
import uuid
import asyncio
import threading
from datetime import datetime, timedelta

from fastapi import FastAPI, Depends, HTTPException, status, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import Base, engine, get_db
from app.services.user_service import create_user, get_user_by_email
from app.dependencies.auth import authenticate_user, create_access_token, get_current_user, ACCESS_TOKEN_EXPIRE_MINUTES
from app.services.architect_service import generate_architecture
from app.services.backend_service import generate_backend
from app.services.frontend_service import generate_frontend
from app.services.project_service import generate_project
from app.services.planner_service import generate_plan
from app.models.generation_job import GenerationJob
from app.models.user_credentials import UserCredentials

Base.metadata.create_all(bind=engine)

# ── Per-job stdout tee ────────────────────────────────────────────────────────

_thread_local = threading.local()
_original_stdout = sys.stdout


class _JobCancelled(BaseException):
    """Raised inside a generation thread when the user cancels the job.
    Inherits BaseException so it passes through broad `except Exception` blocks."""


class _TeeStdout:
    def write(self, text):
        _original_stdout.write(text)
        if text and text.strip():
            job_logs: list | None = getattr(_thread_local, "job_logs", None)
            if job_logs is not None:
                job_logs.append(text.rstrip("\n"))
        # Cancellation check — every print() is a checkpoint
        cancel: threading.Event | None = getattr(_thread_local, "cancel_event", None)
        if cancel and cancel.is_set():
            raise _JobCancelled("Job cancelled by user")

    def flush(self):
        _original_stdout.flush()


sys.stdout = _TeeStdout()

# ── In-memory job store (real-time streaming) ─────────────────────────────────
# {job_id: {"status": str, "logs": list[str], "result": dict|None, "error": str|None,
#            "cancel_event": threading.Event}}
JOB_STORE: dict[str, dict] = {}
CHECK_STORE: dict[str, dict] = {}  # {job_id: {"status": str, "result": dict|None}}

app = FastAPI(title="ForgeAI", version="14.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RegisterRequest(BaseModel):
    email: str
    password: str


@app.post("/register", tags=["auth"])
def register(request: RegisterRequest, db: Session = Depends(get_db)):
    if get_user_by_email(db, request.email):
        raise HTTPException(status_code=400, detail="Email already registered")
    user = create_user(db, request.email, request.password)
    return {"id": user.id, "email": user.email}


@app.post("/login", tags=["auth"])
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {"access_token": token, "token_type": "bearer"}


@app.get("/me", tags=["auth"])
def me(current_user=Depends(get_current_user)):
    return {"id": current_user.id, "email": current_user.email, "is_active": current_user.is_active}


@app.get("/docs-redirect", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")


# ── Job endpoints ─────────────────────────────────────────────────────────────

class JobRequest(BaseModel):
    idea: str
    provider: str = "auto"
    deploy_to: str = "none"
    frontend_target: str = "web"


def _run_job(job_id: str, req: JobRequest):
    """Runs the V14 pipeline in a background thread."""
    import os
    from app.services.v14_orchestrator import generate_project_v14

    store = JOB_STORE[job_id]
    _thread_local.job_logs = store["logs"]
    _thread_local.cancel_event = store["cancel_event"]
    store["status"] = "running"

    # Write the result back to DB in a new session
    from app.database import SessionLocal

    # Look up per-user deployment credentials and temporarily apply them as env vars
    _saved_env: dict = {}
    try:
        _cred_db = SessionLocal()
        try:
            _job = _cred_db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
            _creds = (
                _cred_db.query(UserCredentials)
                .filter(UserCredentials.user_id == _job.user_id)
                .first()
            ) if _job else None
        finally:
            _cred_db.close()

        if _creds:
            _env_map = {
                "GITHUB_TOKEN": _creds.github_token,
                "RENDER_API_KEY": _creds.render_api_key,
                "RENDER_OWNER_ID": _creds.render_owner_id,
                "CLOUDFLARE_API_TOKEN": _creds.cloudflare_api_token,
                "CLOUDFLARE_ACCOUNT_ID": _creds.cloudflare_account_id,
            }
            for _k, _v in _env_map.items():
                if _v:
                    _saved_env[_k] = os.environ.get(_k)
                    os.environ[_k] = _v
    except Exception as _ce:
        print(f"[credentials] lookup failed: {_ce}")

    try:
        result = generate_project_v14(
            idea=req.idea,
            provider=req.provider,
            deploy_to=req.deploy_to,
            frontend_target=req.frontend_target,
        )
        report = result.get("report", {})
        # Only mark done if not already cancelled by user
        if not store["cancel_event"].is_set():
            store["status"] = "done"
            store["result"] = result

            db = SessionLocal()
            try:
                job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
                if job and job.status != "cancelled":
                    raw_score = report.get("forge_score")
                    forge_score = raw_score.get("score") if isinstance(raw_score, dict) else raw_score
                    # frontend_url: prefer report field, fall back to raw cloudflare result
                    frontend_url = (
                        report.get("frontend_url")
                        or result.get("cloudflare", {}).get("url")
                    )
                    job.status = "done"
                    job.project_name = report.get("project_name")
                    job.forge_score = forge_score
                    job.backend_url = report.get("backend_url")
                    job.frontend_url = frontend_url
                    job.github_url = report.get("github_url")
                    job.zip_path = result.get("generation", {}).get("zip_path")
                    job.completed_at = datetime.utcnow()
                    db.commit()
            finally:
                db.close()

    except _JobCancelled:
        # cancel_job() already wrote status=cancelled to DB; just sync in-memory store
        store["status"] = "cancelled"
        store["error"] = "Cancelled by user"

    except Exception as exc:
        if store["cancel_event"].is_set():
            store["status"] = "cancelled"
        else:
            store["status"] = "error"
            store["error"] = str(exc)
            db = SessionLocal()
            try:
                job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
                if job and job.status != "cancelled":
                    job.status = "error"
                    job.error = str(exc)
                    job.completed_at = datetime.utcnow()
                    db.commit()
            finally:
                db.close()

    finally:
        # Restore any env vars we temporarily overrode
        for _k, _orig in _saved_env.items():
            if _orig is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _orig


def _run_job_retry(job_id: str, parent: "GenerationJob"):
    """Resumes from existing project files — skips generation."""
    from app.services.v14_orchestrator import retry_project_v14
    from app.database import SessionLocal
    import os

    store = JOB_STORE[job_id]
    _thread_local.job_logs = store["logs"]
    _thread_local.cancel_event = store["cancel_event"]
    store["status"] = "running"

    project_path = os.path.join("generated_projects", parent.project_name) if parent.project_name else None

    try:
        result = retry_project_v14(
            project_path=project_path,
            project_name=parent.project_name or "",
            idea=parent.idea,
            provider=parent.provider or "auto",
            deploy_to=parent.deploy_to or "none",
            frontend_target=parent.frontend_target or "web",
        )
        report = result.get("report", {})
        if not store["cancel_event"].is_set():
            store["status"] = "done"
            store["result"] = result

            db = SessionLocal()
            try:
                job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
                if job and job.status != "cancelled":
                    raw_score = report.get("forge_score")
                    forge_score = raw_score.get("score") if isinstance(raw_score, dict) else raw_score
                    frontend_url = report.get("frontend_url") or result.get("cloudflare", {}).get("url")
                    job.status = "done"
                    job.project_name = report.get("project_name") or parent.project_name
                    job.forge_score = forge_score
                    job.backend_url = report.get("backend_url")
                    job.frontend_url = frontend_url
                    job.github_url = report.get("github_url")
                    job.zip_path = result.get("generation", {}).get("zip_path")
                    job.completed_at = datetime.utcnow()
                    db.commit()
            finally:
                db.close()

    except _JobCancelled:
        # cancel_job() already wrote status=cancelled to DB; just sync in-memory store
        store["status"] = "cancelled"
        store["error"] = "Cancelled by user"

    except Exception as exc:
        if store["cancel_event"].is_set():
            # Job was cancelled mid-run; ignore the exception
            store["status"] = "cancelled"
        else:
            store["status"] = "error"
            store["error"] = str(exc)
            db = SessionLocal()
            try:
                job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
                if job and job.status != "cancelled":
                    job.status = "error"
                    job.error = str(exc)
                    job.completed_at = datetime.utcnow()
                    db.commit()
            finally:
                db.close()


@app.post("/jobs/{job_id}/cancel", tags=["jobs"])
def cancel_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Immediately marks the job cancelled and signals the thread to stop."""
    job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
    if not job or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("running", "pending"):
        raise HTTPException(status_code=400, detail=f"Job is already {job.status}")

    # Update DB immediately — don't wait for the thread
    job.status = "cancelled"
    job.error = "Cancelled by user"
    job.completed_at = datetime.utcnow()
    db.commit()

    # Update in-memory store so WebSocket broadcasts "cancelled" on next tick
    store = JOB_STORE.get(job_id)
    if store:
        store["status"] = "cancelled"
        store["error"] = "Cancelled by user"
        store["cancel_event"].set()

    return {"cancelled": True}


@app.post("/jobs/{job_id}/retry", tags=["jobs"])
def retry_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Resume a failed job from existing files — skips generation entirely.
    Runs the fix loop + deployment again on the already-generated code.
    Falls back to full rebuild if project files no longer exist on disk.
    """
    import os
    parent = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
    if not parent or parent.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found")

    new_job_id = str(uuid.uuid4())
    JOB_STORE[new_job_id] = {"status": "pending", "logs": [], "result": None, "error": None, "cancel_event": threading.Event()}

    new_job = GenerationJob(
        id=new_job_id,
        user_id=current_user.id,
        idea=parent.idea,
        provider=parent.provider,
        deploy_to=parent.deploy_to,
        frontend_target=parent.frontend_target,
        status="pending",
        project_name=parent.project_name,  # preserve so retry can find the files
    )
    db.add(new_job)
    db.commit()

    # Check if project files exist on disk
    project_files_exist = (
        parent.project_name
        and os.path.isdir(os.path.join("generated_projects", parent.project_name))
    )

    if project_files_exist:
        thread = threading.Thread(target=_run_job_retry, args=(new_job_id, parent), daemon=True)
    else:
        # Files gone — fall back to full rebuild
        req = JobRequest(
            idea=parent.idea,
            provider=parent.provider or "auto",
            deploy_to=parent.deploy_to or "none",
            frontend_target=parent.frontend_target or "web",
        )
        thread = threading.Thread(target=_run_job, args=(new_job_id, req), daemon=True)

    thread.start()
    return {"job_id": new_job_id, "resumed_from_files": project_files_exist}


# ── Post-deploy error checker ─────────────────────────────────────────────────

def _run_check_and_fix(job_id: str, backend_url: str, project_name: str | None,
                       github_url: str | None, creds: dict):
    from app.services.deployed_checker import check_deployed_app
    from app.services.deployed_fixer import fix_deployed_app
    import os

    CHECK_STORE[job_id] = {"status": "checking", "result": None}
    try:
        check_result = check_deployed_app(backend_url)
        fixes: list[str] = []
        if check_result.get("errors"):
            project_path = os.path.join("generated_projects", project_name) if project_name else None
            fix_result = fix_deployed_app(check_result, project_path, github_url, creds)
            fixes = fix_result.get("fixes", [])
        CHECK_STORE[job_id] = {"status": "done", "result": {**check_result, "fixes": fixes}}
    except Exception as e:
        CHECK_STORE[job_id] = {"status": "error", "result": {"error": str(e)}}


@app.post("/jobs/{job_id}/check-deployed", tags=["jobs"])
def check_deployed_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Check a deployed app for errors and automatically fix + redeploy."""
    job = db.query(GenerationJob).filter(
        GenerationJob.id == job_id,
        GenerationJob.user_id == current_user.id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.backend_url:
        raise HTTPException(status_code=400, detail="No backend URL — deploy first")

    # Collect user credentials for GitHub push
    from app.database import SessionLocal
    creds: dict = {}
    try:
        _db = SessionLocal()
        _creds = _db.query(UserCredentials).filter(UserCredentials.user_id == current_user.id).first()
        if _creds:
            creds = {
                "GITHUB_TOKEN": _creds.github_token,
                "RENDER_API_KEY": _creds.render_api_key,
                "CLOUDFLARE_API_TOKEN": _creds.cloudflare_api_token,
                "CLOUDFLARE_ACCOUNT_ID": _creds.cloudflare_account_id,
            }
        _db.close()
    except Exception:
        pass

    CHECK_STORE[job_id] = {"status": "checking", "result": None}
    background_tasks.add_task(
        _run_check_and_fix, job_id, job.backend_url,
        job.project_name, job.github_url, creds,
    )
    return {"status": "checking", "message": f"Checking {job.backend_url}..."}


@app.get("/jobs/{job_id}/check-status", tags=["jobs"])
def get_check_status(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Poll for check-deployed result."""
    job = db.query(GenerationJob).filter(
        GenerationJob.id == job_id,
        GenerationJob.user_id == current_user.id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return CHECK_STORE.get(job_id, {"status": "not_started", "result": None})


@app.post("/jobs", tags=["jobs"])
def create_job(
    req: JobRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    job_id = str(uuid.uuid4())
    JOB_STORE[job_id] = {"status": "pending", "logs": [], "result": None, "error": None, "cancel_event": threading.Event()}

    job = GenerationJob(
        id=job_id,
        user_id=current_user.id,
        idea=req.idea,
        provider=req.provider,
        deploy_to=req.deploy_to,
        frontend_target=req.frontend_target,
        status="pending",
    )
    db.add(job)
    db.commit()

    thread = threading.Thread(target=_run_job, args=(job_id, req), daemon=True)
    thread.start()

    return {"job_id": job_id}


@app.get("/jobs", tags=["jobs"])
def list_jobs(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    jobs = (
        db.query(GenerationJob)
        .filter(GenerationJob.user_id == current_user.id)
        .order_by(GenerationJob.created_at.desc())
        .limit(50)
        .all()
    )
    return {"jobs": [_job_to_dict(j) for j in jobs]}


@app.get("/jobs/{job_id}", tags=["jobs"])
def get_job(job_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
    if not job or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    result = _job_to_dict(job)
    # Merge in-memory logs if still running
    mem = JOB_STORE.get(job_id)
    if mem:
        result["logs"] = mem.get("logs", [])
        result["live_result"] = mem.get("result")
    return result


def _job_to_dict(job: GenerationJob) -> dict:
    check = CHECK_STORE.get(job.id, {})
    return {
        "id": job.id,
        "idea": job.idea,
        "provider": job.provider,
        "deploy_to": job.deploy_to,
        "status": job.status,
        "project_name": job.project_name,
        "forge_score": job.forge_score,
        "backend_url": job.backend_url,
        "frontend_url": job.frontend_url,
        "github_url": job.github_url,
        "zip_path": job.zip_path,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "check_status": check.get("status"),
        "check_result": check.get("result"),
    }


# ── WebSocket — live generation log stream ────────────────────────────────────

@app.websocket("/ws/{job_id}")
async def ws_job(websocket: WebSocket, job_id: str):
    await websocket.accept()
    sent = 0
    try:
        while True:
            store = JOB_STORE.get(job_id)
            if not store:
                await websocket.send_json({"type": "error", "message": "Job not found"})
                break

            logs: list[str] = store["logs"]
            new_lines = logs[sent:]
            for line in new_lines:
                await websocket.send_json({"type": "log", "message": line})
            sent += len(new_lines)

            status = store["status"]
            if status == "done":
                await websocket.send_json({"type": "done", "result": store["result"]})
                break
            elif status == "cancelled":
                await websocket.send_json({"type": "cancelled", "message": "Job cancelled by user"})
                break
            elif status == "error":
                await websocket.send_json({"type": "error", "message": store["error"]})
                break

            await asyncio.sleep(0.4)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


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


class V14Request(BaseModel):
    idea: str
    provider: str = "auto"
    deploy_to: str = "none"          # "render" | "cloudflare" | "both" | "none"
    run_improvement_cycle: bool = False
    skip_reviews: bool = True
    frontend_target: str = "web"


@app.post("/project/v14")
def project_v14(request: V14Request):
    """
    V14 — One-Click Deployment Platform.
    Generates, validates, pushes to GitHub, deploys backend to Render,
    deploys frontend to Cloudflare Pages, runs health checks, returns a report.

    deploy_to options:
      "none"       — generate + configs only (default, no credentials needed)
      "render"     — deploy backend to Render (needs RENDER_API_KEY + RENDER_OWNER_ID + GITHUB_TOKEN)
      "cloudflare" — deploy frontend to Cloudflare Pages (needs CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID)
      "both"       — full stack deploy (needs all 5 credentials above)

    Required in backend/.env for deployment:
      GITHUB_TOKEN, RENDER_API_KEY, RENDER_OWNER_ID,
      CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID
    """
    from app.services.v14_orchestrator import generate_project_v14
    return generate_project_v14(
        idea=request.idea,
        provider=request.provider,
        deploy_to=request.deploy_to,
        run_improvement_cycle=request.run_improvement_cycle,
        skip_reviews=request.skip_reviews,
        frontend_target=request.frontend_target,
    )


@app.post("/deploy/github")
def deploy_github(project_name: str, project_path: str):
    """Push a generated project to GitHub (creates repo automatically). Requires GITHUB_TOKEN."""
    from app.services.github_service import push_to_github
    return push_to_github(project_path, project_name)


@app.post("/deploy/render")
def deploy_render(project_name: str, project_path: str, github_repo_url: str = ""):
    """Deploy a generated project's backend to Render. Requires RENDER_API_KEY + RENDER_OWNER_ID."""
    from app.deployments.render_provider import RenderProvider
    provider = RenderProvider()
    env_vars = {"GITHUB_REPO_URL": github_repo_url} if github_repo_url else None
    res = provider.deploy(project_path, project_name, env_vars=env_vars)
    return {"success": res.success, "url": res.url, "error": res.error, "metadata": res.metadata}


@app.post("/deploy/cloudflare")
def deploy_cloudflare(project_name: str, project_path: str, backend_url: str = ""):
    """Deploy a generated project's frontend to Cloudflare Pages. Requires CLOUDFLARE_API_TOKEN."""
    from app.deployments.cloudflare_provider import CloudflareProvider
    provider = CloudflareProvider()
    env_vars = {"VITE_API_URL": backend_url} if backend_url else None
    res = provider.deploy(project_path, project_name, env_vars=env_vars)
    return {"success": res.success, "url": res.url, "error": res.error}


# ── Per-user deployment credentials ──────────────────────────────────────────

class CredentialsRequest(BaseModel):
    github_token: str = ""
    render_api_key: str = ""
    render_owner_id: str = ""
    cloudflare_api_token: str = ""
    cloudflare_account_id: str = ""


@app.get("/credentials", tags=["credentials"])
def get_credentials(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    creds = db.query(UserCredentials).filter(UserCredentials.user_id == current_user.id).first()
    if not creds:
        return {}
    return {
        "github_token": creds.github_token or "",
        "render_api_key": creds.render_api_key or "",
        "render_owner_id": creds.render_owner_id or "",
        "cloudflare_api_token": creds.cloudflare_api_token or "",
        "cloudflare_account_id": creds.cloudflare_account_id or "",
    }


@app.post("/credentials", tags=["credentials"])
def save_credentials(
    req: CredentialsRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    creds = db.query(UserCredentials).filter(UserCredentials.user_id == current_user.id).first()
    if not creds:
        creds = UserCredentials(user_id=current_user.id)
        db.add(creds)
    creds.github_token = req.github_token or None
    creds.render_api_key = req.render_api_key or None
    creds.render_owner_id = req.render_owner_id or None
    creds.cloudflare_api_token = req.cloudflare_api_token or None
    creds.cloudflare_account_id = req.cloudflare_account_id or None
    db.commit()
    return {"saved": True}


@app.get("/health")
def health():
    return {"status": "ok", "version": "14.0"}


# ── Serve React frontend (production) ─────────────────────────────────────────
import os as _os

_here = _os.path.dirname(_os.path.abspath(__file__))
_dist = _os.path.join(_here, "..", "frontend", "dist")
_dist = _os.path.abspath(_dist)

print(f"[startup] frontend dist path: {_dist} exists={_os.path.isdir(_dist)}")

if _os.path.isdir(_dist):
    _assets = _os.path.join(_dist, "assets")
    if _os.path.isdir(_assets):
        app.mount("/assets", StaticFiles(directory=_assets), name="assets")

    @app.get("/", include_in_schema=False)
    async def serve_root():
        return FileResponse(_os.path.join(_dist, "index.html"))

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        file = _os.path.join(_dist, full_path)
        if _os.path.isfile(file):
            return FileResponse(file)
        return FileResponse(_os.path.join(_dist, "index.html"))