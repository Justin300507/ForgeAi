"""
ForgeAI Queue Worker

Runs as a standalone process. Polls the job queue and executes the full
generation pipeline for each job it claims.

Start one worker:
    python -m app.queue.worker

Start with explicit worker ID (for logging):
    python -m app.queue.worker --worker-id worker-2

Start with Redis backend:
    REDIS_URL=redis://localhost:6379/0 python -m app.queue.worker

The worker is stateless — all state lives in the queue database.
Multiple workers can run on the same or different machines simultaneously.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import multiprocessing as mp
import os
import queue
import re
import signal
import sys
import threading
import time
import uuid

# Always run from backend/
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(_BACKEND_DIR)
sys.path.insert(0, _BACKEND_DIR)

from app.queue.job_queue import forge_queue, Job, UnsupportedQueueConfig, validate_v15_queue_config

_shutdown = threading.Event()

POLL_INTERVAL_S  = 2    # how long to wait when queue is empty
HEARTBEAT_EVERY  = 10   # seconds between heartbeats while running a job
RECLAIM_EVERY    = 30   # seconds between stale-job sweeps
DEFAULT_JOB_DEADLINE_S = 20 * 60
_SAFE_STAGE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _handle_sigterm(signum, frame):
    print(f"\n[Worker] SIGTERM received — finishing current job then exiting...")
    _shutdown.set()


signal.signal(signal.SIGTERM, _handle_sigterm)
if hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, _handle_sigterm)


def run_worker(worker_id: str) -> None:
    """
    Main worker loop.

    Claim a job → start heartbeat thread → run pipeline → mark done.
    Loops until _shutdown is set (SIGTERM / KeyboardInterrupt).
    """
    print(f"[Worker {worker_id}] Started. Queue: {forge_queue.__class__.__name__}")
    print(f"[Worker {worker_id}] Polling every {POLL_INTERVAL_S}s. Ctrl+C to stop.")

    last_reclaim = time.time()

    while not _shutdown.is_set():
        # Periodic stale-job reclaim (only one worker needs to do this,
        # but it's idempotent so all workers can safely call it)
        if time.time() - last_reclaim > RECLAIM_EVERY:
            n = forge_queue.reclaim_stale()
            if n:
                print(f"[Worker {worker_id}] Reclaimed {n} stale job(s)")
            last_reclaim = time.time()

        job = forge_queue.dequeue(worker_id)
        if job is None:
            _shutdown.wait(timeout=POLL_INTERVAL_S)
            continue

        _process_job(job, worker_id)

    print(f"[Worker {worker_id}] Shutdown complete.")


def _process_job(job: Job, worker_id: str, *, pipeline_runner=None,
                 deadline_s: int | None = None) -> None:
    print(f"\n[Worker {worker_id}] ─── Job {job.id[:8]} (attempt {job.attempts}/{job.max_attempts})")
    print(f"[Worker {worker_id}]     Provider: {job.provider}")

    # Proactive disk-space guard (Exp161): see app/services/storage_cleanup.py
    # and main.py::_run_job's identical hook for the full incident history.
    from app.services.storage_cleanup import clean_stale_node_modules_if_needed
    clean_stale_node_modules_if_needed()

    # Start heartbeat thread so the dispatcher knows this worker is alive
    hb_stop = threading.Event()
    hb_thread = threading.Thread(target=_heartbeat_loop, args=(job.id, hb_stop), daemon=True)
    hb_thread.start()

    t0 = time.time()
    try:
        result = _run_pipeline_in_child(job, pipeline_runner=pipeline_runner,
                                        deadline_s=deadline_s)
        elapsed = round(time.time() - t0, 1)

        score = (result.get("forge_score") or {})
        if isinstance(score, dict):
            score = score.get("score", 0)

        forge_queue.complete(job.id, result)
        print(
            f"[Worker {worker_id}] ✓ Job {job.id[:8]} done in {elapsed}s  "
            f"score={score:.0f}  "
            f"runtime={result.get('runtime', {}).get('success', False)}"
        )

    except Exception as exc:
        elapsed = round(time.time() - t0, 1)
        if isinstance(exc, JobDeadlineExceeded):
            forge_queue.mark_deadline_exceeded(job.id, exc.stage, exc.deadline_at)
            error = f"deadline_exceeded at stage {exc.stage}"
        elif isinstance(exc, UnsupportedQueueConfig):
            error = "unsupported queue configuration"
            forge_queue.fail(job.id, error, retry=False)
        else:
            # Never persist raw child exceptions: providers may echo prompts
            # or credentials in error bodies.
            error = f"pipeline_child_error:{type(exc).__name__}"
            forge_queue.fail(job.id, error, retry=True)
        print(f"[Worker {worker_id}] ✗ Job {job.id[:8]} FAILED in {elapsed}s: {error[:120]}")
        if not isinstance(exc, JobDeadlineExceeded):
            import traceback; traceback.print_exc()

    finally:
        hb_stop.set()
        hb_thread.join(timeout=5)


class JobDeadlineExceeded(RuntimeError):
    def __init__(self, stage: str, deadline_at: str):
        self.stage = stage
        self.deadline_at = deadline_at
        super().__init__("queue job deadline exceeded")


def _job_deadline_s(value: int | None = None) -> int:
    """Read a bounded whole-job deadline without accepting request input."""
    if value is not None:
        return max(1, int(value))
    try:
        return max(1, int(os.getenv("FORGE_QUEUE_JOB_DEADLINE_S", str(DEFAULT_JOB_DEADLINE_S))))
    except ValueError:
        return DEFAULT_JOB_DEADLINE_S


def _safe_stage(value: object) -> str:
    """Only stage identifiers cross the child boundary; no LLM text/errors."""
    stage = str(value or "unknown").lower()
    return stage if _SAFE_STAGE.fullmatch(stage) else "unknown"


def _child_event(messages, payload: dict) -> None:
    if payload.get("event") not in {"stage:start", "stage:end"}:
        return
    try:
        messages.put_nowait({"type": "stage", "stage": _safe_stage(payload.get("stage"))})
    except Exception:
        pass  # observability must not stall a generation child


def _pipeline_child(job: Job, messages, pipeline_runner=None) -> None:
    """Process target. The parent exclusively owns SQLite."""
    try:
        if pipeline_runner is not None:
            result = pipeline_runner(job)
        else:
            # See app/jobs/v15_supervisor.py's identical fix (Exp140, live
            # Render OOM incident 2026-07-22) for the full rationale: on the
            # fork() path (non-Windows -- see _run_pipeline_in_child below)
            # this child's copy of app.database's module-level `engine`,
            # and any pooled connections it already opened in the PARENT
            # before fork, was duplicated by the OS fork, not freshly
            # created. dispose(close=False) drops those inherited
            # references without touching what the parent may still be
            # using, and is a no-op under spawn (Windows), where the child
            # is a fresh interpreter that hasn't opened any connections yet.
            if os.name != "nt":
                from app.database import engine as _inherited_engine
                _inherited_engine.dispose(close=False)
            from app.core.events import EventBus
            from app.services.v15_orchestrator import generate_project_v15
            config = validate_v15_queue_config(job.config)
            bus = EventBus().on("*", lambda payload: _child_event(messages, payload))
            result = generate_project_v15(
                idea=job.idea, provider=job.provider,
                # Queue jobs historically did not deploy. Keep deployment
                # opt-in through existing job configuration only.
                deploy=bool(config.get("deploy", False)),
                deploy_to=config.get("deploy_to", "both"), job_id=job.id,
                style_override=config.get("style_override"),
                motion_intensity=config.get("motion_intensity"),
                include_landing_page=bool(config.get("include_landing_page", False)),
                event_bus=bus,
            )
        messages.put({"type": "result", "result": result})
    except BaseException as exc:
        # Exception messages are untrusted provider output; pass a class only.
        messages.put({"type": "error", "error_type": type(exc).__name__})


def _run_pipeline_in_child(job: Job, *, pipeline_runner=None,
                           deadline_s: int | None = None) -> dict:
    """Terminate only the owned child when the whole-job deadline is reached."""
    # Guard direct database inserts too; the API performs the same validation
    # before enqueueing.  Do this before allocating child IPC resources.
    if pipeline_runner is None:
        validate_v15_queue_config(job.config)
    # See app/jobs/v15_supervisor.py's run_v15_supervisor for the full
    # rationale (Exp140, live Render OOM incident 2026-07-22): "spawn" is
    # required on Windows (this repo's dev environment) but re-imports this
    # app's entire dependency chain from scratch per job on top of the
    # already-running parent server's own copy -- expensive enough to OOM
    # a 512MB production instance. fork() is copy-on-write against the
    # parent's already-loaded memory on the actual production OS (Linux).
    ctx = mp.get_context("spawn" if os.name == "nt" else "fork")
    messages = ctx.Queue(maxsize=64)
    process = ctx.Process(target=_pipeline_child, args=(job, messages, pipeline_runner), daemon=False)
    seconds = _job_deadline_s(deadline_s)
    started = time.monotonic()
    deadline_at = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    last_stage = "queued"
    process.start()
    try:
        while True:
            remaining = seconds - (time.monotonic() - started)
            if remaining <= 0:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
                    if process.is_alive() and hasattr(process, "kill"):
                        process.kill()
                        process.join(timeout=5)
                raise JobDeadlineExceeded(last_stage, deadline_at)
            try:
                message = messages.get(timeout=min(0.5, remaining))
            except queue.Empty:
                if not process.is_alive():
                    raise RuntimeError("pipeline child exited without a result")
                continue
            if message.get("type") == "stage":
                last_stage = _safe_stage(message.get("stage"))
                forge_queue.record_progress(job.id, last_stage)
            elif message.get("type") == "result":
                result = message.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError("pipeline child returned an invalid result")
                return result
            elif message.get("type") == "error":
                raise RuntimeError(f"pipeline child failed: {message.get('error_type', 'unknown')}")
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=5)
        messages.close()


def _run_pipeline(job: Job) -> dict:
    """Compatibility wrapper for callers that historically imported this helper."""
    return _run_pipeline_in_child(job)


def _heartbeat_loop(job_id: str, stop: threading.Event) -> None:
    """Send heartbeats every HEARTBEAT_EVERY seconds while the job runs."""
    while not stop.wait(timeout=HEARTBEAT_EVERY):
        try:
            forge_queue.heartbeat(job_id)
        except Exception:
            pass


def main():
    p = argparse.ArgumentParser(description="ForgeAI queue worker")
    p.add_argument("--worker-id", default=None,
                   help="Unique worker ID (default: auto-generated)")
    args = p.parse_args()

    worker_id = args.worker_id or f"w-{uuid.uuid4().hex[:6]}"

    try:
        run_worker(worker_id)
    except KeyboardInterrupt:
        print(f"\n[Worker {worker_id}] KeyboardInterrupt — stopping.")


if __name__ == "__main__":
    main()
