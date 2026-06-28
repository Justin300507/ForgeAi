"""
Worker Dispatcher

Manages a pool of worker *processes* (not threads).

Each worker is a separate OS process running `app.queue.worker`.
They share the same SQLite job queue and independently claim work.

Why processes not threads?
  - Python's GIL limits true CPU parallelism in threads
  - The generation pipeline is CPU+IO heavy (LLM calls, subprocess spawning)
  - Separate processes give true isolation: one worker crash doesn't kill others
  - Scales to multiple machines when combined with Redis queue

Usage:
    from app.queue.dispatcher import dispatcher

    dispatcher.start(n_workers=4)   # spawn 4 worker processes
    dispatcher.status()             # list all workers + their pids
    dispatcher.stop()               # graceful shutdown (wait for current job to finish)
    dispatcher.stop(force=True)     # SIGKILL all workers immediately
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


_WORKER_MODULE = "app.queue.worker"
_BACKEND_DIR   = Path(__file__).parent.parent.parent


@dataclass
class WorkerProcess:
    worker_id:  str
    pid:        Optional[int]
    process:    Optional[subprocess.Popen]
    started_at: str
    stopped:    bool = False

    def is_alive(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None

    def to_dict(self) -> dict:
        return {
            "worker_id":  self.worker_id,
            "pid":        self.pid,
            "started_at": self.started_at,
            "alive":      self.is_alive(),
            "returncode": self.process.returncode if self.process else None,
        }


class WorkerDispatcher:
    """
    Manages a pool of ForgeAI worker sub-processes.

    Thread-safe: start/stop/status can be called from the FastAPI server.
    The monitor thread automatically restarts dead workers (if enabled).
    """

    def __init__(self):
        self._workers: dict[str, WorkerProcess] = {}
        self._lock    = threading.Lock()
        self._monitor: Optional[threading.Thread] = None
        self._auto_restart  = True
        self._target_count  = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, n_workers: int = 4, auto_restart: bool = True) -> list[str]:
        """
        Spawn N worker processes. Returns list of worker IDs.

        If auto_restart=True, a monitor thread watches for dead workers and
        restarts them, keeping the pool at n_workers capacity at all times.
        """
        self._auto_restart = auto_restart
        self._target_count = n_workers

        started = []
        with self._lock:
            alive_count = sum(1 for w in self._workers.values() if w.is_alive())
            to_spawn = max(0, n_workers - alive_count)
            for i in range(to_spawn):
                wid = self._next_worker_id()
                wp  = self._spawn(wid)
                self._workers[wid] = wp
                started.append(wid)

        if auto_restart and (self._monitor is None or not self._monitor.is_alive()):
            self._monitor = threading.Thread(target=self._monitor_loop, daemon=True)
            self._monitor.start()

        print(f"[Dispatcher] Started {len(started)} worker(s). Pool size: {n_workers}")
        return started

    def stop(self, worker_id: Optional[str] = None, force: bool = False) -> int:
        """
        Stop workers gracefully (SIGTERM → wait 30s → SIGKILL).

        If worker_id is given, only stop that worker.
        If worker_id is None, stop all workers and cancel auto-restart.

        Returns count of workers stopped.
        """
        self._target_count = 0  # disable auto-restart

        with self._lock:
            if worker_id:
                targets = {k: v for k, v in self._workers.items() if k == worker_id}
            else:
                targets = dict(self._workers)

        stopped = 0
        for wid, wp in targets.items():
            if wp.process and wp.is_alive():
                if force:
                    wp.process.kill()
                else:
                    wp.process.terminate()
                    try:
                        wp.process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        wp.process.kill()
                wp.stopped = True
                stopped += 1
                print(f"[Dispatcher] Worker {wid} stopped (pid={wp.pid})")

        print(f"[Dispatcher] Stopped {stopped} worker(s)")
        return stopped

    def scale(self, n_workers: int) -> dict:
        """
        Resize the worker pool to exactly n_workers.
        Adds workers if n < target; stops excess workers if n > target.
        """
        self._target_count = n_workers
        with self._lock:
            alive = [(wid, w) for wid, w in self._workers.items() if w.is_alive()]
            current = len(alive)

        if current < n_workers:
            added = self.start(n_workers - current, auto_restart=self._auto_restart)
            return {"action": "scaled_up", "added": len(added), "total": n_workers}
        elif current > n_workers:
            # Stop the most recently started workers (they're less likely to be mid-job)
            excess = current - n_workers
            to_stop = [wid for wid, _ in sorted(alive, key=lambda x: x[1].started_at, reverse=True)][:excess]
            for wid in to_stop:
                self.stop(worker_id=wid)
            return {"action": "scaled_down", "removed": excess, "total": n_workers}
        return {"action": "no_change", "total": current}

    def status(self) -> dict:
        """Return current worker pool status."""
        with self._lock:
            workers = [w.to_dict() for w in self._workers.values()]
        alive = sum(1 for w in workers if w["alive"])
        return {
            "alive":    alive,
            "total":    len(workers),
            "target":   self._target_count,
            "workers":  workers,
            "auto_restart": self._auto_restart,
        }

    # ── Private ───────────────────────────────────────────────────────────────

    def _spawn(self, worker_id: str) -> WorkerProcess:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        process = subprocess.Popen(
            [sys.executable, "-m", _WORKER_MODULE, "--worker-id", worker_id],
            cwd=str(_BACKEND_DIR),
            env=env,
            # Workers write their own logs to stdout; parent process doesn't capture
            # them so they flow to the terminal or the server's log stream.
        )
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print(f"[Dispatcher] Spawned worker {worker_id} (pid={process.pid})")
        return WorkerProcess(
            worker_id  = worker_id,
            pid        = process.pid,
            process    = process,
            started_at = now,
        )

    def _next_worker_id(self) -> str:
        existing = set(self._workers.keys())
        i = 1
        while f"worker-{i}" in existing:
            i += 1
        return f"worker-{i}"

    def _monitor_loop(self) -> None:
        """Background thread that restarts crashed workers."""
        while self._auto_restart and self._target_count > 0:
            time.sleep(5)
            with self._lock:
                alive_count = sum(1 for w in self._workers.values() if w.is_alive())
                deficit     = self._target_count - alive_count
                if deficit > 0:
                    print(f"[Dispatcher] {deficit} worker(s) down — restarting...")
                    for _ in range(deficit):
                        wid = self._next_worker_id()
                        wp  = self._spawn(wid)
                        self._workers[wid] = wp


# Process-level singleton
dispatcher = WorkerDispatcher()
