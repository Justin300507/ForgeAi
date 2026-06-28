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
import os
import signal
import sys
import threading
import time
import uuid

# Always run from backend/
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(_BACKEND_DIR)
sys.path.insert(0, _BACKEND_DIR)

from app.queue.job_queue import forge_queue, Job

_shutdown = threading.Event()

POLL_INTERVAL_S  = 2    # how long to wait when queue is empty
HEARTBEAT_EVERY  = 10   # seconds between heartbeats while running a job
RECLAIM_EVERY    = 30   # seconds between stale-job sweeps


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


def _process_job(job: Job, worker_id: str) -> None:
    print(f"\n[Worker {worker_id}] ─── Job {job.id[:8]} (attempt {job.attempts}/{job.max_attempts})")
    print(f"[Worker {worker_id}]     Idea: {job.idea[:80]}")
    print(f"[Worker {worker_id}]     Provider: {job.provider}")

    # Start heartbeat thread so the dispatcher knows this worker is alive
    hb_stop = threading.Event()
    hb_thread = threading.Thread(target=_heartbeat_loop, args=(job.id, hb_stop), daemon=True)
    hb_thread.start()

    t0 = time.time()
    try:
        result = _run_pipeline(job)
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
        error   = f"{type(exc).__name__}: {exc}"
        forge_queue.fail(job.id, error, retry=True)
        print(f"[Worker {worker_id}] ✗ Job {job.id[:8]} FAILED in {elapsed}s: {error[:120]}")
        import traceback; traceback.print_exc()

    finally:
        hb_stop.set()
        hb_thread.join(timeout=5)


def _run_pipeline(job: Job) -> dict:
    """Run the full ForgeAI pipeline for this job and return the result dict."""
    from app.services.project_service import generate_project

    result = generate_project(
        idea     = job.idea,
        provider = job.provider,
        **(job.config or {}),
    )
    return result


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
