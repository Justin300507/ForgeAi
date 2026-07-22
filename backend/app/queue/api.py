"""
Queue API Router

Mounts at /queue in the ForgeAI FastAPI app.

Endpoints:
  POST /queue/submit              → enqueue a generation job
  GET  /queue/status/{job_id}    → job status + result when done
  GET  /queue/jobs               → list recent jobs
  GET  /queue/stats              → queue counts by status
  POST /queue/workers/start      → start N worker processes
  POST /queue/workers/stop       → graceful shutdown
  POST /queue/workers/scale      → resize pool
  GET  /queue/workers/status     → current worker pool state

Typical flow:
  1. POST /queue/submit {"idea": "...", "provider": "auto"}
     → {"job_id": "abc-123", "status": "pending"}

  2. Poll GET /queue/status/abc-123 until status == "completed"
     → {"status": "completed", "result": {...full pipeline result...}}

  3. Workers pick up jobs from the queue automatically.
     Start them once at server startup or via POST /queue/workers/start.
"""
from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict
from typing import Optional

from app.queue.job_queue import forge_queue, UnsupportedQueueConfig, validate_v15_queue_config
from app.queue.dispatcher import dispatcher
from app.dependencies.auth import get_current_user

# Queue submission exposes paid generation and worker control.  Keep the
# authentication boundary on the router so future queue routes cannot
# accidentally be added without JWT enforcement.  Internal workers invoke
# queue/dispatcher objects directly and do not traverse this HTTP router.
router = APIRouter(
    prefix="/queue",
    tags=["queue"],
    dependencies=[Depends(get_current_user)],
)


# ── Request/Response models ───────────────────────────────────────────────────

class SubmitRequest(BaseModel):
    # A caller must not be able to smuggle an ownership or privilege field into
    # a paid-generation request; ownership is derived only from the JWT user.
    model_config = ConfigDict(extra="forbid")
    idea:     str
    provider: str = "auto"
    config:   dict = {}

class SubmitResponse(BaseModel):
    job_id:  str
    status:  str
    message: str

class WorkerStartRequest(BaseModel):
    n_workers:    int  = 4
    auto_restart: bool = True

class WorkerScaleRequest(BaseModel):
    n_workers: int


def require_queue_operator(
    operator_token: Optional[str] = Header(default=None, alias="X-Forge-Queue-Operator"),
) -> None:
    """Gate global worker/PID controls behind an explicitly configured secret.

    JWT authentication remains required by the router.  This second boundary
    prevents any ordinary application user from operating the shared worker
    pool.  An unset token disables these routes rather than accidentally
    making them public.
    """
    expected = os.getenv("FORGE_QUEUE_OPERATOR_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not operator_token or not secrets.compare_digest(operator_token, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


# ── Job endpoints ─────────────────────────────────────────────────────────────

@router.post("/submit", response_model=SubmitResponse, summary="Enqueue a generation job")
def submit_job(req: SubmitRequest, current_user=Depends(get_current_user)):
    """
    Add a ForgeAI generation job to the queue.

    Returns immediately with a job_id. Poll `/queue/status/{job_id}` to get
    the result when a worker finishes processing it.
    """
    if not req.idea or len(req.idea.strip()) < 5:
        raise HTTPException(400, "idea must be at least 5 characters")

    try:
        config = validate_v15_queue_config(req.config)
    except UnsupportedQueueConfig:
        raise HTTPException(400, "unsupported queue configuration")
    # Ownership is always derived from the authenticated user; request input
    # deliberately has no owner field.
    job_id = forge_queue.enqueue(
        idea=req.idea.strip(), provider=req.provider, config=config, owner_id=current_user.id,
    )
    stats  = forge_queue.stats_for_owner(current_user.id)
    queue_depth = stats.get("pending", 0)

    return SubmitResponse(
        job_id  = job_id,
        status  = "pending",
        message = f"Job queued. {queue_depth} job(s) ahead of it. "
                  f"Workers: {dispatcher.status()['alive']} alive.",
    )


@router.get("/status/{job_id}", summary="Get job status and result")
def job_status(job_id: str, current_user=Depends(get_current_user)):
    """
    Poll this endpoint after submitting a job.

    Returns:
      - status: pending | running | completed | failed
      - result: full pipeline result dict (only when status=completed)
      - error:  error message (only when status=failed)
      - worker_id, attempts, timing fields
    """
    job = forge_queue.get_job_for_owner(job_id, current_user.id)
    if not job:
        # Do not distinguish a missing job from a foreign job (IDOR defense).
        raise HTTPException(404, "Job not found")
    return job.to_api()


@router.get("/jobs", summary="List recent jobs")
def list_jobs(status: Optional[str] = None, limit: int = 20,
              current_user=Depends(get_current_user)):
    """
    List recent jobs, optionally filtered by status.

    status options: pending | running | completed | failed
    """
    jobs = forge_queue.list_jobs_for_owner(current_user.id, status=status, limit=limit)
    return {
        "jobs": [j.to_api() for j in jobs],
        "count": len(jobs),
    }


@router.get("/stats", summary="Queue stats by status")
def queue_stats(current_user=Depends(get_current_user)):
    """Return counts of jobs in each status bucket."""
    return forge_queue.stats_for_owner(current_user.id)


# ── Worker management ─────────────────────────────────────────────────────────

@router.post("/workers/start", summary="Start N worker processes")
def start_workers(req: WorkerStartRequest, _operator=Depends(require_queue_operator)):
    """
    Spawn N ForgeAI worker processes. Each worker is an independent OS process
    that polls the queue and runs the full generation pipeline.

    Workers persist until you call /workers/stop. With auto_restart=true,
    crashed workers are automatically restarted.
    """
    if req.n_workers < 1 or req.n_workers > 32:
        raise HTTPException(400, "n_workers must be between 1 and 32")

    worker_ids = dispatcher.start(n_workers=req.n_workers, auto_restart=req.auto_restart)
    return {
        "started": worker_ids,
        "message": f"Started {len(worker_ids)} worker(s). "
                   f"Auto-restart: {req.auto_restart}",
        "status": dispatcher.status(),
    }


@router.post("/workers/stop", summary="Stop all workers gracefully")
def stop_workers(worker_id: Optional[str] = None, force: bool = False,
                 _operator=Depends(require_queue_operator)):
    """
    Stop workers gracefully (SIGTERM, waits 30s, then SIGKILL).

    - worker_id: stop only this worker
    - force: SIGKILL immediately (job in progress will be re-queued by reclaim)
    """
    stopped = dispatcher.stop(worker_id=worker_id, force=force)
    return {
        "stopped": stopped,
        "message": f"Stopped {stopped} worker(s)",
        "status": dispatcher.status(),
    }


@router.post("/workers/scale", summary="Resize the worker pool")
def scale_workers(req: WorkerScaleRequest, _operator=Depends(require_queue_operator)):
    """
    Resize the worker pool to exactly n_workers. Starts or stops workers as needed.
    """
    if req.n_workers < 0 or req.n_workers > 32:
        raise HTTPException(400, "n_workers must be between 0 and 32")
    result = dispatcher.scale(req.n_workers)
    return {**result, "status": dispatcher.status()}


@router.get("/workers/status", summary="Current worker pool state")
def workers_status(_operator=Depends(require_queue_operator)):
    """
    Returns the current worker pool: how many are alive, their PIDs,
    started_at timestamps, and whether they're running.
    """
    return dispatcher.status()


@router.post("/workers/reclaim", summary="Re-queue stale jobs (manual trigger)")
def reclaim_stale(_operator=Depends(require_queue_operator)):
    """
    Manually trigger stale-job reclaim. Workers do this automatically every 30s,
    but you can call it immediately if you know a worker just crashed.
    """
    n = forge_queue.reclaim_stale()
    return {"reclaimed": n, "message": f"Re-queued {n} stale job(s)"}
