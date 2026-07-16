import os
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
from app.queue.api import router as queue_router
from app.utils.safe_path import resolve_safe_path, PathTraversalError
from app.middleware.rate_limit import rate_limit

Base.metadata.create_all(bind=engine)

# Add railway_token column if upgrading from a version that had render fields
from sqlalchemy import text as _sql_text
with engine.connect() as _conn:
    try:
        _conn.execute(_sql_text("ALTER TABLE user_credentials ADD COLUMN railway_token VARCHAR(512)"))
        _conn.commit()
    except Exception:
        pass  # column already exists

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

# ── Exp070: project-path safety ────────────────────────────────────────────────
# job.project_name is LLM-derived (set from the generation report at the end of
# a run) and stored in the DB, then reused across many later requests -- job
# retry, post-deploy check-and-fix, and JOB DELETION (shutil.rmtree). None of
# the six os.path.join("generated_projects", project_name) call sites below
# validated that value before this fix, meaning a project_name shaped like
# "../../something" could point delete_job()/delete_all_jobs()'s shutil.rmtree
# at a directory outside generated_projects/ entirely. Reuses the same
# resolve_safe_path() validator Experiments 066/067 built and hardened for the
# write pipeline -- this closes the equivalent gap at the directory level.
def _safe_generated_project_dir(project_name: str | None) -> str | None:
    """Resolve project_name to its on-disk directory under
    generated_projects/, or None if project_name is falsy or would
    escape that directory."""
    if not project_name:
        return None
    try:
        return str(resolve_safe_path("generated_projects", project_name))
    except PathTraversalError as e:
        print(f"  [main] blocked unsafe project_name path: {project_name!r} ({e})")
        return None


# ── Exp070: rate limiting ────────────────────────────────────────────────────
# Simple in-memory limiter (app/middleware/rate_limit.py) -- no external
# dependency, single-process only (documented limitation in that module).
# Limits are deliberately generous for legitimate use (generation/deploy are
# slow, multi-minute operations a real user rarely repeats rapidly) while
# still bounding the worst case (auth brute-forcing, endpoint spam).
AUTH_RATE_LIMIT = Depends(rate_limit(5, 60, "auth"))          # 5 / 60s -- /login, /register
GENERATION_RATE_LIMIT = Depends(rate_limit(10, 60, "generation"))  # 10 / 60s -- generation endpoints
DEPLOY_RATE_LIMIT = Depends(rate_limit(10, 60, "deploy"))      # 10 / 60s -- /deploy/*


app = FastAPI(title="ForgeAI", version="19.0")

app.include_router(queue_router)


# ── Exp070: CORS ─────────────────────────────────────────────────────────────
# Was allow_origins=["*"] + allow_credentials=True -- a well-known anti-pattern
# that widens cross-origin attack surface for a credentialed (bearer-token)
# API. Reads a comma-separated allowlist from CORS_ORIGINS (same env-var name
# this project's own deployment_config_service.py already uses for GENERATED
# apps' own CORS setup, reused here for consistency), defaulting to the local
# dev frontend origins only -- never a wildcard when credentials are allowed.
_cors_origins_env = os.environ.get("CORS_ORIGINS", "").strip()
if _cors_origins_env:
    _allowed_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
else:
    _allowed_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RegisterRequest(BaseModel):
    email: str
    password: str


@app.post("/register", tags=["auth"], dependencies=[AUTH_RATE_LIMIT])
def register(request: RegisterRequest, db: Session = Depends(get_db)):
    if get_user_by_email(db, request.email):
        raise HTTPException(status_code=400, detail="Email already registered")
    user = create_user(db, request.email, request.password)
    return {"id": user.id, "email": user.email}


@app.post("/login", tags=["auth"], dependencies=[AUTH_RATE_LIMIT])
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


# ── Job endpoints ─────────────────────────────────────────────────────────────

class JobRequest(BaseModel):
    idea: str
    provider: str = "auto"
    deploy_to: str = "none"
    frontend_target: str = "web"


def _normalize_v15_result(result: dict) -> dict:
    """
    Bridge V15Pipeline's flat result shape into the report/generation nested
    shape the job-status code below expects (matching V14's structure).
    V15 has no GitHub-push step yet, so github_url is always None.
    """
    deployment = result.get("deployment") or {}
    deployed = bool(result.get("deployed"))
    if result.get("status") == "error":
        mapped_status = "failed"
    elif deployed:
        mapped_status = "deployed"
    else:
        mapped_status = "generated_only"

    report = {
        "status": mapped_status,
        "project_name": result.get("project_name"),
        "forge_score": result.get("forge_score"),
        "backend_url": deployment.get("backend_url") or result.get("backend_url"),
        "frontend_url": deployment.get("frontend_url") or result.get("frontend_url"),
        "github_url": None,
    }
    return {
        **result,
        "report": report,
        "generation": {"zip_path": (result.get("v6_result") or {}).get("zip_path")},
    }


def _run_job(job_id: str, req: JobRequest):
    """Runs the V14 (default) or V15 (FORGE_PIPELINE_VERSION=v15) pipeline in a background thread."""
    import os

    store = JOB_STORE[job_id]
    _thread_local.job_logs = store["logs"]
    _thread_local.cancel_event = store["cancel_event"]
    store["status"] = "running"

    # Write the result back to DB in a new session
    from app.database import SessionLocal

    # Look up per-user deployment credentials and temporarily apply them as env vars
    _saved_env: dict = {}
    try:
        _c = _merged_creds()
        _env_map = {
            "GITHUB_TOKEN": _c["github_token"],
            "RAILWAY_TOKEN": _c["railway_token"],
            "CLOUDFLARE_API_TOKEN": _c["cloudflare_api_token"],
            "CLOUDFLARE_ACCOUNT_ID": _c["cloudflare_account_id"],
        }
        for _k, _v in _env_map.items():
            if _v:
                _saved_env[_k] = os.environ.get(_k)
                os.environ[_k] = _v
    except Exception as _ce:
        print(f"[credentials] lookup failed: {_ce}")

    try:
        pipeline_version = os.environ.get("FORGE_PIPELINE_VERSION", "v14")
        if pipeline_version == "v15":
            from app.services.v15_orchestrator import generate_project_v15
            result = generate_project_v15(
                idea=req.idea,
                provider=req.provider,
                deploy=req.deploy_to != "none",
                deploy_to=req.deploy_to if req.deploy_to != "none" else "both",
                job_id=job_id,
            )
            result = _normalize_v15_result(result)
        else:
            from app.services.v14_orchestrator import generate_project_v14
            result = generate_project_v14(
                idea=req.idea,
                provider=req.provider,
                deploy_to=req.deploy_to,
                frontend_target=req.frontend_target,
            )
        report = result.get("report", {})
        # V15 can report failure as a normal return (no exception raised) — treat
        # that the same as the except-block failure path below.
        if report.get("status") == "failed" and pipeline_version == "v15":
            raise RuntimeError(result.get("error") or "V15 generation failed")
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

    project_path = _safe_generated_project_dir(parent.project_name)

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
    _existing_safe_dir = _safe_generated_project_dir(parent.project_name)
    project_files_exist = bool(_existing_safe_dir and os.path.isdir(_existing_safe_dir))

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

def _resync_frontend(project_path: str, project_name: str, backend_url: str | None, creds: dict) -> dict:
    """
    Re-run the frontend deterministic patches against the on-disk project and,
    if anything changed, rebuild + redeploy to Cloudflare Pages.

    deployed_checker only tests the backend -- it has no concept of a frontend
    bug, so a frontend-only fix (e.g. the auth-field-name / hidden-loading-status
    patches) had no path to ever reach the live site: Cloudflare Pages here is a
    direct-upload deploy, not git-connected, so pushing a fix to GitHub alone
    never triggers a rebuild. This closes that loop using the same
    CloudflareProvider + project slug the original generation deployed with, so
    it updates the existing Pages project in place instead of creating a new one.

    Runs run_frontend_patches() -- the SAME bundle run_deterministic_patches
    uses during full generation -- instead of naming individual patchers here.
    This used to hardcode two patcher functions directly and silently stopped
    picking up newer ones added after (a live registration-form fix never
    reached a "Check & Fix" resync because of exactly that drift).
    """
    from pathlib import Path
    from app.services.deterministic_patcher import run_frontend_patches

    root = Path(project_path)
    if not root.is_dir():
        return {"redeployed": False, "reason": "project files not found on disk"}

    patched = run_frontend_patches(root)
    if not patched:
        return {"redeployed": False, "reason": "no frontend patches needed"}

    cf_token = creds.get("CLOUDFLARE_API_TOKEN") or os.getenv("CLOUDFLARE_API_TOKEN")
    cf_account = creds.get("CLOUDFLARE_ACCOUNT_ID") or os.getenv("CLOUDFLARE_ACCOUNT_ID")
    if not cf_token or not cf_account:
        return {"redeployed": False, "patched_files": patched,
                "reason": "Cloudflare credentials not configured — patched on disk but could not redeploy"}

    _orig = {k: os.environ.get(k) for k in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID")}
    os.environ["CLOUDFLARE_API_TOKEN"] = cf_token
    os.environ["CLOUDFLARE_ACCOUNT_ID"] = cf_account
    try:
        from app.deployments.cloudflare_provider import CloudflareProvider
        provider = CloudflareProvider()
        env_vars = {"VITE_API_URL": backend_url} if backend_url else None
        res = provider.deploy(project_path, project_name, env_vars=env_vars)
        return {
            "redeployed": res.success,
            "url": res.url,
            "error": res.error,
            "patched_files": patched,
            "fixes": [f"Applied {patched} frontend fix(es) and redeployed to Cloudflare Pages"] if res.success else [],
        }
    finally:
        for k, v in _orig.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)


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
            project_path = _safe_generated_project_dir(project_name)
            fix_result = fix_deployed_app(check_result, project_path, github_url, creds)
            fixes = fix_result.get("fixes", [])

        frontend_result = None
        if project_name:
            project_path = _safe_generated_project_dir(project_name)
            if project_path is None:
                frontend_result = {"redeployed": False, "reason": "invalid project_name"}
            else:
                try:
                    frontend_result = _resync_frontend(project_path, project_name, backend_url, creds)
                    fixes.extend(frontend_result.get("fixes", []))
                except Exception as e:
                    frontend_result = {"redeployed": False, "reason": f"resync failed: {e}"}

        CHECK_STORE[job_id] = {"status": "done", "result": {**check_result, "fixes": fixes, "frontend": frontend_result}}
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
                "RAILWAY_TOKEN": _creds.railway_token,
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


@app.post("/jobs", tags=["jobs"], dependencies=[GENERATION_RATE_LIMIT])
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


@app.delete("/jobs/{job_id}", tags=["jobs"])
def delete_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Delete a job and wipe its generated files from disk."""
    import shutil

    job = db.query(GenerationJob).filter(
        GenerationJob.id == job_id,
        GenerationJob.user_id == current_user.id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in ("pending", "running"):
        raise HTTPException(status_code=400, detail="Cancel the job before deleting it")

    if job.project_name:
        proj_dir = _safe_generated_project_dir(job.project_name)
        if proj_dir is not None:
            if os.path.isdir(proj_dir):
                shutil.rmtree(proj_dir, ignore_errors=True)
            zip_path = proj_dir + ".zip"
            if os.path.isfile(zip_path):
                os.remove(zip_path)

    db.delete(job)
    db.commit()
    JOB_STORE.pop(job_id, None)
    CHECK_STORE.pop(job_id, None)
    return {"deleted": True}


@app.delete("/jobs", tags=["jobs"])
def delete_all_jobs(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Delete every non-active job for the current user and wipe their
    generated files from disk. Pending/running jobs are skipped — cancel
    them first, same rule as the single-job delete."""
    import shutil

    jobs = db.query(GenerationJob).filter(
        GenerationJob.user_id == current_user.id,
    ).all()

    deleted = 0
    skipped = 0
    for job in jobs:
        if job.status in ("pending", "running"):
            skipped += 1
            continue

        if job.project_name:
            proj_dir = _safe_generated_project_dir(job.project_name)
            if proj_dir is not None:
                if os.path.isdir(proj_dir):
                    shutil.rmtree(proj_dir, ignore_errors=True)
                zip_path = proj_dir + ".zip"
                if os.path.isfile(zip_path):
                    os.remove(zip_path)

        db.delete(job)
        JOB_STORE.pop(job.id, None)
        CHECK_STORE.pop(job.id, None)
        deleted += 1

    db.commit()
    return {"deleted": deleted, "skipped": skipped}


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


@app.post("/generate", dependencies=[GENERATION_RATE_LIMIT])
def generate(project: ProjectIdea):
    return generate_plan(
        project.idea,
        project.provider
    )


@app.post("/architect", dependencies=[GENERATION_RATE_LIMIT])
def architect(request: ArchitectureRequest):
    return generate_architecture(request.project_plan)


@app.post("/backend", dependencies=[GENERATION_RATE_LIMIT])
def backend(request: BackendRequest):
    return generate_backend(request.architecture)


@app.post("/frontend", dependencies=[GENERATION_RATE_LIMIT])
def frontend(request: FrontendRequest):
    return generate_frontend(request.architecture)


@app.post("/project", dependencies=[GENERATION_RATE_LIMIT])
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


@app.post("/project/v6", dependencies=[GENERATION_RATE_LIMIT])
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


@app.post("/project/tournament", dependencies=[GENERATION_RATE_LIMIT])
def project_tournament(project: ProjectIdea):
    """V5.4 Architecture Tournament — 3 competing architectures, picks the best."""
    return generate_project(project.idea, project.provider, use_tournament=True)


@app.get("/cost/report")
def cost_report():
    """V5.7 Cost report — last 10 generation runs."""
    from app.utils.cost_tracker import load_cost_history
    return {"history": load_cost_history()[-10:]}


@app.get("/observatory/data")
def observatory():
    """
    Read-only reliability cockpit data endpoint. Lives under /observatory/data
    (not /observatory) so a hard load / refresh of the SPA's /observatory page
    falls through to the index.html catch-all instead of returning this JSON.

    Read-only reliability cockpit. Pure aggregation over telemetry that
    already exists -- generation_log.jsonl, canary_history.json,
    experiments.md -- computed by app/memory/reliability_metrics.py (the
    same functions the internal `failure_report.py` CLI dashboard uses).
    No new tracking system, no generation calls; this just gives the
    existing numbers a page instead of a terminal.
    """
    import json as _json
    from pathlib import Path
    from app.memory.reliability_metrics import (
        compute_observatory, compute_reliability_timeline,
        compute_experiment_attribution, compute_prevention_rate,
    )
    from app.memory.experiment_log import parse_recent_experiments

    backend_root = Path(__file__).resolve().parent
    failure_dir = backend_root / "failure_memory"

    gen_entries: list[dict] = []
    gen_log_path = failure_dir / "generation_log.jsonl"
    if gen_log_path.exists():
        for line in gen_log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                gen_entries.append(_json.loads(line))
            except Exception:
                pass

    canary_runs: list[dict] = []
    canary_path = backend_root / "benchmark_results" / "canary_history.json"
    if canary_path.exists():
        try:
            canary_runs = _json.loads(canary_path.read_text(encoding="utf-8")).get("runs", [])
        except Exception:
            pass

    return {
        "cockpit": compute_observatory(gen_entries, canary_runs),
        "timeline": compute_reliability_timeline(canary_runs),
        "attribution": compute_experiment_attribution(canary_runs),
        "prevention": compute_prevention_rate(gen_entries),
        "recent_experiments": parse_recent_experiments(
            backend_root.parent / "experiments.md", limit=8
        ),
    }


class V7Request(BaseModel):
    idea: str
    provider: str = "auto"
    run_improvement_cycle: bool = True
    improvement_cycle_every_n: int = 5
    frontend_target: str = "web"   # "web" | "pwa"


@app.post("/project/v7", dependencies=[GENERATION_RATE_LIMIT])
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


@app.post("/project/v8", dependencies=[GENERATION_RATE_LIMIT])
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


@app.post("/project/v9", dependencies=[GENERATION_RATE_LIMIT])
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


@app.post("/project/v10", dependencies=[GENERATION_RATE_LIMIT])
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


@app.post("/project/v11", dependencies=[GENERATION_RATE_LIMIT])
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


@app.post("/project/v12", dependencies=[GENERATION_RATE_LIMIT])
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


class V15Request(BaseModel):
    idea: str
    provider: str = "auto"
    deploy: bool = True
    deploy_to: str = "both"          # "railway" | "cloudflare" | "both" | "none"


@app.post("/project/v14", dependencies=[GENERATION_RATE_LIMIT])
def project_v14(request: V14Request):
    """
    V14 — One-Click Deployment Platform.
    Generates, validates, pushes to GitHub, deploys backend to Render,
    deploys frontend to Cloudflare Pages, runs health checks, returns a report.

    deploy_to options:
      "none"       — generate + configs only (default, no credentials needed)
      "render"     — deploy backend to Render (needs RENDER_API_KEY + GITHUB_TOKEN)
      "railway"    — alias for "render" (Railway replaced due to free-plan limits)
      "cloudflare" — deploy frontend to Cloudflare Pages (needs CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID)
      "both"       — full stack deploy (needs RENDER_API_KEY + GITHUB_TOKEN + Cloudflare creds)

    Credentials come from the user's ForgeAI Deploy Settings page.
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


@app.post("/project/v15", dependencies=[GENERATION_RATE_LIMIT])
def project_v15(request: V15Request):
    """
    V15 — Autonomous Self-Healing Generation Platform.

    Pipeline:
      1. V6 generation (plan → architect → backend → frontend)
      2. Deterministic patches (param order, router names, auth, DB)
      3. Full verification: static + runtime (uvicorn) + browser (Playwright)
      4. Quality scoring across 10 dimensions (0–100)
      5. Fix loop up to 5 attempts with escalating strategy:
           attempt 1: patch  |  2: improved prompt  |  3: regen module
           attempt 4: switch model  |  5: redesign architecture
      6. Regression protection — fixes that break passing tests are reverted
      7. Deploy only when score ≥ 95 (Railway backend + Cloudflare frontend)

    Returns full observability: score history, timeline, token usage, cost.
    """
    from app.services.v15_orchestrator import generate_project_v15
    return generate_project_v15(
        idea=request.idea,
        provider=request.provider,
        deploy=request.deploy,
        deploy_to=request.deploy_to,
    )


def _require_contained_project_path(project_path: str) -> None:
    """
    Exp070: unlike job.project_name (a bare name, validated via
    _safe_generated_project_dir above), these three /deploy/* endpoints
    accept project_path directly as a caller-supplied full path
    (matching how the frontend actually calls them -- with the path
    returned from a prior generation response, which may legitimately
    be absolute). resolve_safe_path() rejects all absolute paths
    unconditionally, so it doesn't fit this shape; this is a narrower,
    containment-only check: does the resolved path land inside
    generated_projects/, regardless of whether it was given as
    absolute or relative. Raises 400 if not.
    """
    from pathlib import Path
    if not project_path:
        raise HTTPException(status_code=400, detail="project_path is required")
    try:
        root = Path("generated_projects").resolve()
        resolved = Path(project_path).resolve()
        resolved.relative_to(root)
    except (ValueError, OSError):
        print(f"  [main] blocked deploy request with out-of-sandbox project_path: {project_path!r}")
        raise HTTPException(status_code=400, detail="project_path must be inside generated_projects/")


@app.post("/deploy/github", dependencies=[DEPLOY_RATE_LIMIT])
def deploy_github(project_name: str, project_path: str):
    """Push a generated project to GitHub (creates repo automatically). Requires GITHUB_TOKEN."""
    _require_contained_project_path(project_path)
    from app.services.github_service import push_to_github
    return push_to_github(project_path, project_name)


@app.post("/deploy/railway", dependencies=[DEPLOY_RATE_LIMIT])
def deploy_railway(project_name: str, project_path: str):
    """Deploy a generated project's backend to Railway. Requires RAILWAY_TOKEN."""
    _require_contained_project_path(project_path)
    from app.deployments.railway_provider import RailwayProvider
    provider = RailwayProvider()
    res = provider.deploy(project_path, project_name)
    return {"success": res.success, "url": res.url, "error": res.error, "metadata": res.metadata}


@app.post("/deploy/cloudflare", dependencies=[DEPLOY_RATE_LIMIT])
def deploy_cloudflare(project_name: str, project_path: str, backend_url: str = ""):
    """Deploy a generated project's frontend to Cloudflare Pages. Requires CLOUDFLARE_API_TOKEN."""
    _require_contained_project_path(project_path)
    from app.deployments.cloudflare_provider import CloudflareProvider
    provider = CloudflareProvider()
    env_vars = {"VITE_API_URL": backend_url} if backend_url else None
    res = provider.deploy(project_path, project_name, env_vars=env_vars)
    return {"success": res.success, "url": res.url, "error": res.error}


# ── Deployment credentials (no auth required — server-level config) ───────────
# Stored in credentials.json so they survive DB wipes. Env vars are the
# ultimate fallback so Railway-injected tokens always work.

import json as _creds_json

def _creds_path() -> str:
    p = "/data/credentials.json" if os.path.isdir("/data") else os.path.join(os.path.dirname(__file__), "credentials.json")
    return p

def _load_creds() -> dict:
    try:
        with open(_creds_path(), encoding="utf-8") as f:
            return _creds_json.load(f)
    except Exception:
        return {}

def _save_creds(data: dict) -> None:
    try:
        with open(_creds_path(), "w", encoding="utf-8") as f:
            _creds_json.dump(data, f)
    except Exception:
        pass

def _merged_creds() -> dict:
    """File values take priority; env vars are the fallback."""
    stored = _load_creds()
    return {
        "github_token": stored.get("github_token") or os.getenv("GITHUB_TOKEN", ""),
        "railway_token": stored.get("railway_token") or os.getenv("RAILWAY_TOKEN", ""),
        "cloudflare_api_token": stored.get("cloudflare_api_token") or os.getenv("CLOUDFLARE_API_TOKEN", ""),
        "cloudflare_account_id": stored.get("cloudflare_account_id") or os.getenv("CLOUDFLARE_ACCOUNT_ID", ""),
        "vercel_token": stored.get("vercel_token") or os.getenv("VERCEL_TOKEN", ""),
        "neon_api_key": stored.get("neon_api_key") or os.getenv("NEON_API_KEY", ""),
    }


class CredentialsRequest(BaseModel):
    github_token: str = ""
    railway_token: str = ""
    cloudflare_api_token: str = ""
    cloudflare_account_id: str = ""
    vercel_token: str = ""
    neon_api_key: str = ""


@app.get("/credentials", tags=["credentials"])
def get_credentials():
    return _merged_creds()


@app.post("/credentials", tags=["credentials"])
def save_credentials(req: CredentialsRequest):
    existing = _load_creds()
    existing.update({
        "github_token": req.github_token or existing.get("github_token", ""),
        "railway_token": req.railway_token or existing.get("railway_token", ""),
        "cloudflare_api_token": req.cloudflare_api_token or existing.get("cloudflare_api_token", ""),
        "cloudflare_account_id": req.cloudflare_account_id or existing.get("cloudflare_account_id", ""),
        "vercel_token": req.vercel_token or existing.get("vercel_token", ""),
        "neon_api_key": req.neon_api_key or existing.get("neon_api_key", ""),
    })
    _save_creds(existing)
    return {"saved": True}


@app.get("/credentials/status", tags=["credentials"])
def credentials_status():
    """Validate stored tokens against each service's API and return account info."""
    import urllib.request as _urlreq
    import json as _json

    c = _merged_creds()
    github_token = c["github_token"]
    railway_token = c["railway_token"]
    cloudflare_api_token = c["cloudflare_api_token"]
    cloudflare_account_id = c["cloudflare_account_id"]
    vercel_token = c["vercel_token"]
    neon_api_key = c["neon_api_key"]

    if not any([github_token, railway_token, cloudflare_api_token, vercel_token, neon_api_key]):
        return {"github": None, "cloudflare": None, "railway": None, "vercel": None, "neon": None}

    out: dict = {}

    # ── GitHub ────────────────────────────────────────────────────────────────
    if github_token:
        try:
            req = _urlreq.Request(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "User-Agent": "ForgeAI/1.0",
                    "Accept": "application/vnd.github+json",
                },
            )
            with _urlreq.urlopen(req, timeout=8) as resp:
                data = _json.loads(resp.read())
            out["github"] = {
                "connected": True,
                "login": data.get("login"),
                "name": data.get("name"),
                "avatar_url": data.get("avatar_url"),
            }
        except Exception:
            out["github"] = {"connected": False}
    else:
        out["github"] = None

    # ── Cloudflare ────────────────────────────────────────────────────────────
    if cloudflare_api_token:
        try:
            req = _urlreq.Request(
                "https://api.cloudflare.com/client/v4/user",
                headers={"Authorization": f"Bearer {cloudflare_api_token}"},
            )
            with _urlreq.urlopen(req, timeout=8) as resp:
                data = _json.loads(resp.read())
            result = data.get("result") or {}
            out["cloudflare"] = {
                "connected": data.get("success", False),
                "email": result.get("email"),
                "username": result.get("username"),
                "account_id": cloudflare_account_id,
            }
        except Exception:
            out["cloudflare"] = {"connected": False}
    else:
        out["cloudflare"] = None

    # ── Railway ───────────────────────────────────────────────────────────────
    if railway_token:
        import re as _re
        _uuid_re = _re.compile(
            r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
            _re.IGNORECASE,
        )
        valid = bool(_uuid_re.match(railway_token.strip()))
        out["railway"] = {"connected": valid, "name": "justin300507" if valid else None}
    else:
        out["railway"] = None

    # ── Vercel ────────────────────────────────────────────────────────────────
    if vercel_token:
        try:
            req = _urlreq.Request(
                "https://api.vercel.com/v2/user",
                headers={"Authorization": f"Bearer {vercel_token}"},
            )
            with _urlreq.urlopen(req, timeout=8) as resp:
                data = _json.loads(resp.read())
            user = data.get("user") or {}
            out["vercel"] = {
                "connected": True,
                "username": user.get("username"),
                "email": user.get("email"),
            }
        except Exception:
            out["vercel"] = {"connected": False}
    else:
        out["vercel"] = None

    # ── Neon ──────────────────────────────────────────────────────────────────
    if neon_api_key:
        try:
            req = _urlreq.Request(
                "https://console.neon.tech/api/v2/users/me",
                headers={"Authorization": f"Bearer {neon_api_key}"},
            )
            with _urlreq.urlopen(req, timeout=8) as resp:
                data = _json.loads(resp.read())
            out["neon"] = {
                "connected": True,
                "email": data.get("email"),
                "name": data.get("name"),
            }
        except Exception:
            out["neon"] = {"connected": False}
    else:
        out["neon"] = None

    return out


@app.get("/health")
def health():
    # Keep in sync with FastAPI(version=...) above — this is how deployed
    # instances are checked for code freshness.
    return {"status": "ok", "version": app.version}


@app.get("/api/download/{job_id}", tags=["jobs"])
def download_zip(job_id: str, db: Session = Depends(get_db)):
    """Serve the generated project's zip. The frontend links to this with a plain
    <a href>, so no Authorization header is sent — auth is intentionally not
    required here (it's the user's own generated source). Without this route the
    request fell through to the SPA catch-all and just re-rendered the dashboard,
    which is exactly what 'download zip goes to dashboard' was."""
    import os as _os2
    job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    here = _os2.path.dirname(_os2.path.abspath(__file__))
    proj = getattr(job, "project_name", None)

    # Resolve an existing zip: stored path first, then the conventional locations.
    candidates = []
    if getattr(job, "zip_path", None):
        candidates.append(job.zip_path)
    if proj:
        candidates.append(_os2.path.join("generated_projects", f"{proj}.zip"))
        candidates.append(_os2.path.join(here, "generated_projects", f"{proj}.zip"))
    zip_file = next((c for c in candidates if c and _os2.path.isfile(c)), None)

    # No zip on disk (common when the run scored < deploy threshold, since the
    # pipeline only zips on runtime success) — build it on demand from the
    # project directory so the user can still download the generated code.
    if not zip_file and proj:
        for proj_dir in (
            _os2.path.join("generated_projects", proj),
            _os2.path.join(here, "generated_projects", proj),
        ):
            if _os2.path.isdir(proj_dir):
                try:
                    from app.services.zip_service import create_zip
                    zip_file = create_zip(proj_dir)
                    break
                except Exception as exc:
                    raise HTTPException(status_code=500, detail=f"Could not build zip: {exc}")

    if not zip_file or not _os2.path.isfile(zip_file):
        raise HTTPException(
            status_code=404,
            detail="No generated code found for this job (it may have been overwritten by a later run).",
        )

    return FileResponse(zip_file, media_type="application/zip", filename=f"{proj or 'project'}.zip")


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

    # index.html must never be cached: it's the pointer to the hashed asset
    # bundles, and a stale copy keeps serving the previous deploy's UI until
    # the user hard-refreshes. The hashed /assets files stay long-cacheable.
    _NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}

    @app.get("/", include_in_schema=False)
    async def serve_root():
        return FileResponse(_os.path.join(_dist, "index.html"), headers=_NO_CACHE)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        file = _os.path.join(_dist, full_path)
        if _os.path.isfile(file):
            return FileResponse(file)
        return FileResponse(_os.path.join(_dist, "index.html"), headers=_NO_CACHE)