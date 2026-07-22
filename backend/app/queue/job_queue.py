"""
ForgeAI Job Queue

SQLite-backed persistent task queue with atomic dequeue so N workers
on the same machine (or same shared volume) can pull jobs concurrently
without ever processing the same job twice.

Upgrade path:
  - Local / single machine → SQLiteQueue (default, zero deps)
  - Multi-machine / Redis deployed → RedisQueue (set REDIS_URL env var)

Queue contract:
  - `enqueue(idea, provider, config)` → job_id  (add a job)
  - `dequeue(worker_id)` → Job | None           (claim one job atomically)
  - `heartbeat(job_id)` → None                  (keep job alive)
  - `complete(job_id, result)` → None            (mark success)
  - `fail(job_id, error, retry)` → None          (mark failure, optionally re-enqueue)
  - `get_job(job_id)` → Job | None
  - `list_jobs(status, limit)` → list[Job]
  - `reclaim_stale(timeout_s)` → int             (return count reclaimed)
  - `stats()` → dict

Stale job reclaim: if a worker crashes without completing its job,
the job stays `running` forever. Call `reclaim_stale()` periodically
(the dispatcher does this) to re-queue jobs whose heartbeat is old.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


_DB_PATH = Path(__file__).parent.parent.parent / "failure_memory" / "job_queue.db"

HEARTBEAT_TIMEOUT_S = 60   # reclaim jobs whose worker hasn't pulsed in 60s
MAX_ATTEMPTS        = 3    # retry up to 3x before marking permanently failed
V15_QUEUE_CONFIG_KEYS = frozenset({
    "deploy", "deploy_to", "style_override", "motion_intensity", "include_landing_page",
})


class UnsupportedQueueConfig(ValueError):
    """A persisted queue job contains options the V15 queue runner cannot honor."""


def validate_v15_queue_config(config: dict | None) -> dict:
    """Return a shallow safe config copy or reject keys rather than dropping them."""
    value = config or {}
    if not isinstance(value, dict) or set(value) - V15_QUEUE_CONFIG_KEYS:
        raise UnsupportedQueueConfig("unsupported queue configuration")
    return dict(value)


@dataclass
class Job:
    id:            str
    idea:          str
    # Jobs created before queue ownership was introduced intentionally retain
    # a NULL owner.  They remain runnable by local workers but are never
    # exposed through owner-scoped HTTP reads.
    owner_id:      Optional[str] = None
    provider:      str          = "auto"
    config:        dict         = field(default_factory=dict)
    status:        str          = "pending"   # pending | running | completed | failed
    worker_id:     Optional[str] = None
    attempts:      int          = 0
    max_attempts:  int          = MAX_ATTEMPTS
    created_at:    str          = ""
    started_at:    Optional[str] = None
    completed_at:  Optional[str] = None
    heartbeat_at:  Optional[str] = None
    result_json:   Optional[str] = None
    error:         Optional[str] = None
    # Watchdog observability is deliberately metadata-only.  Never persist a
    # stage message here: provider exceptions and prompts can contain secrets.
    last_stage:     Optional[str] = None
    last_stage_at:  Optional[str] = None
    deadline_at:    Optional[str] = None
    deadline_exceeded: bool      = False

    # Result deserialized on demand
    @property
    def result(self) -> Optional[dict]:
        if self.result_json:
            try:
                return json.loads(self.result_json)
            except Exception:
                return None
        return None

    def to_api(self) -> dict:
        d = asdict(self)
        d["result"] = self.result
        del d["result_json"]
        return d


class SQLiteQueue:
    """
    Persistent queue backed by SQLite.

    Thread-safe: uses `BEGIN IMMEDIATE` for dequeue so concurrent workers
    on the same SQLite file don't double-claim jobs.

    Process-safe: SQLite WAL mode allows multiple readers + one writer.
    Multiple worker *processes* on the same machine share the same file.
    """

    def __init__(self, db_path: Path = _DB_PATH):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id           TEXT PRIMARY KEY,
                    idea         TEXT NOT NULL,
                    owner_id     TEXT,
                    provider     TEXT DEFAULT 'auto',
                    config_json  TEXT DEFAULT '{}',
                    status       TEXT DEFAULT 'pending',
                    worker_id    TEXT,
                    attempts     INTEGER DEFAULT 0,
                    max_attempts INTEGER DEFAULT 3,
                    created_at   TEXT,
                    started_at   TEXT,
                    completed_at TEXT,
                    heartbeat_at TEXT,
                    result_json  TEXT,
                    error        TEXT,
                    last_stage   TEXT,
                    last_stage_at TEXT,
                    deadline_at  TEXT,
                    deadline_exceeded INTEGER DEFAULT 0
                )
            """)
            # Existing installs predate watchdog state.  SQLite does not add
            # columns through CREATE TABLE IF NOT EXISTS, so keep this small,
            # additive migration idempotent and transactionally local.
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
            for name, ddl in (
                ("owner_id", "TEXT"),
                ("last_stage", "TEXT"),
                ("last_stage_at", "TEXT"),
                ("deadline_at", "TEXT"),
                ("deadline_exceeded", "INTEGER DEFAULT 0"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {ddl}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON jobs(status, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_owner_created ON jobs(owner_id, created_at)")

    def enqueue(self, idea: str, provider: str = "auto", config: dict | None = None,
                owner_id: str | int | None = None) -> str:
        job_id = str(uuid.uuid4())
        now    = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO jobs (id, idea, owner_id, provider, config_json, status, created_at, max_attempts)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """, (job_id, idea, str(owner_id) if owner_id is not None else None,
                  provider, json.dumps(config or {}), now, MAX_ATTEMPTS))
        return job_id

    def dequeue(self, worker_id: str) -> Optional[Job]:
        """
        Atomically claim the oldest pending job for this worker.

        Uses BEGIN IMMEDIATE so no two workers can claim the same job even
        when running as separate OS processes on the same SQLite file.
        """
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id FROM jobs WHERE status='pending' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                return None
            job_id = row["id"]
            conn.execute("""
                UPDATE jobs
                SET status='running', worker_id=?, started_at=?, heartbeat_at=?, attempts=attempts+1
                WHERE id=?
            """, (worker_id, now, now, job_id))
            conn.execute("COMMIT")
            return self.get_job(job_id, conn=conn)
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            return None
        finally:
            conn.close()

    def heartbeat(self, job_id: str) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._connect() as conn:
            conn.execute("UPDATE jobs SET heartbeat_at=? WHERE id=?", (now, job_id))

    def record_progress(self, job_id: str, stage: str) -> None:
        """Persist an allowlisted V15 stage for status polling and watchdog diagnostics."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET last_stage=?, last_stage_at=?, heartbeat_at=? WHERE id=?",
                (stage, now, now, job_id),
            )

    def mark_deadline_exceeded(self, job_id: str, stage: str, deadline_at: str) -> None:
        """Fail a timed-out job terminally; watchdog failures must not auto-retry."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        error = f"deadline_exceeded at stage {stage}"
        with self._connect() as conn:
            conn.execute("""
                UPDATE jobs
                SET status='failed', completed_at=?, error=?, last_stage=?,
                    last_stage_at=?, deadline_at=?, deadline_exceeded=1
                WHERE id=?
            """, (now, error, stage, now, deadline_at, job_id))

    def complete(self, job_id: str, result: dict) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._connect() as conn:
            conn.execute("""
                UPDATE jobs SET status='completed', completed_at=?, result_json=?, error=NULL
                WHERE id=?
            """, (now, json.dumps(result, default=str), job_id))

    def fail(self, job_id: str, error: str, retry: bool = True) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._connect() as conn:
            row = conn.execute("SELECT attempts, max_attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return
            attempts     = row["attempts"]
            max_attempts = row["max_attempts"]
            if retry and attempts < max_attempts:
                # Re-queue: reset to pending so another worker picks it up
                conn.execute("""
                    UPDATE jobs SET status='pending', worker_id=NULL, started_at=NULL,
                                    error=?, heartbeat_at=NULL
                    WHERE id=?
                """, (f"attempt {attempts} failed: {error[:200]}", job_id))
            else:
                conn.execute("""
                    UPDATE jobs SET status='failed', completed_at=?, error=?
                    WHERE id=?
                """, (now, error[:500], job_id))

    def reclaim_stale(self, timeout_s: int = HEARTBEAT_TIMEOUT_S) -> int:
        """
        Find jobs stuck in `running` with a stale heartbeat (worker crashed).
        Re-queue them as `pending` so a live worker picks them up.
        Returns count of jobs reclaimed.
        """
        cutoff_ts  = time.time() - timeout_s
        cutoff_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff_ts))
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT id, attempts, max_attempts FROM jobs
                WHERE status='running' AND heartbeat_at < ?
            """, (cutoff_str,)).fetchall()
            reclaimed = 0
            for row in rows:
                if row["attempts"] >= row["max_attempts"]:
                    conn.execute(
                        "UPDATE jobs SET status='failed', error='Worker crashed — max attempts reached' WHERE id=?",
                        (row["id"],)
                    )
                else:
                    conn.execute(
                        "UPDATE jobs SET status='pending', worker_id=NULL, started_at=NULL, heartbeat_at=NULL WHERE id=?",
                        (row["id"],)
                    )
                reclaimed += 1
        return reclaimed

    def get_job(self, job_id: str, conn: sqlite3.Connection | None = None) -> Optional[Job]:
        _conn = conn or self._connect()
        try:
            row = _conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return None
            return _row_to_job(row)
        finally:
            if conn is None:
                _conn.close()

    def get_job_for_owner(self, job_id: str, owner_id: str | int) -> Optional[Job]:
        """Return a job only when it belongs to the authenticated owner."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id=? AND owner_id=?", (job_id, str(owner_id))
            ).fetchone()
            return _row_to_job(row) if row else None

    def list_jobs(self, status: str | None = None, limit: int = 50) -> list[Job]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [_row_to_job(r) for r in rows]

    def list_jobs_for_owner(self, owner_id: str | int, status: str | None = None,
                            limit: int = 50) -> list[Job]:
        """List only jobs owned by one authenticated user."""
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE owner_id=? AND status=? ORDER BY created_at DESC LIMIT ?",
                    (str(owner_id), status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE owner_id=? ORDER BY created_at DESC LIMIT ?",
                    (str(owner_id), limit),
                ).fetchall()
            return [_row_to_job(r) for r in rows]

    def stats(self) -> dict:
        with self._connect() as conn:
            counts = {
                row["status"]: row["cnt"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
                ).fetchall()
            }
        return {
            "pending":   counts.get("pending", 0),
            "running":   counts.get("running", 0),
            "completed": counts.get("completed", 0),
            "failed":    counts.get("failed", 0),
            "total":     sum(counts.values()),
        }

    def stats_for_owner(self, owner_id: str | int) -> dict:
        """Return status counts for one owner without leaking global queue load."""
        with self._connect() as conn:
            counts = {
                row["status"]: row["cnt"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) as cnt FROM jobs WHERE owner_id=? GROUP BY status",
                    (str(owner_id),),
                ).fetchall()
            }
        return {
            "pending": counts.get("pending", 0), "running": counts.get("running", 0),
            "completed": counts.get("completed", 0), "failed": counts.get("failed", 0),
            "total": sum(counts.values()),
        }


def _row_to_job(row: sqlite3.Row) -> Job:
    config = {}
    try:
        config = json.loads(row["config_json"] or "{}")
    except Exception:
        pass
    return Job(
        id            = row["id"],
        idea          = row["idea"],
        owner_id      = row["owner_id"] if "owner_id" in row.keys() else None,
        provider      = row["provider"] or "auto",
        config        = config,
        status        = row["status"],
        worker_id     = row["worker_id"],
        attempts      = row["attempts"],
        max_attempts  = row["max_attempts"],
        created_at    = row["created_at"] or "",
        started_at    = row["started_at"],
        completed_at  = row["completed_at"],
        heartbeat_at  = row["heartbeat_at"],
        result_json   = row["result_json"],
        error         = row["error"],
        last_stage    = row["last_stage"] if "last_stage" in row.keys() else None,
        last_stage_at = row["last_stage_at"] if "last_stage_at" in row.keys() else None,
        deadline_at   = row["deadline_at"] if "deadline_at" in row.keys() else None,
        deadline_exceeded = bool(row["deadline_exceeded"]) if "deadline_exceeded" in row.keys() else False,
    )


# ── Optional Redis backend (same interface) ────────────────────────────────────

class RedisQueue:
    """
    Redis-backed queue. Uses BLPOP for instant delivery (no polling).
    Set REDIS_URL env var to enable: redis://host:6379/0

    Drop-in replacement for SQLiteQueue — same public API.
    Install: pip install redis
    """

    def __init__(self, url: str):
        import redis as _redis
        self._r = _redis.from_url(url, decode_responses=True)
        self._QUEUE_KEY  = "forgeai:queue:pending"
        self._JOBS_KEY   = "forgeai:jobs"       # hash: job_id → json

    def enqueue(self, idea: str, provider: str = "auto", config: dict | None = None,
                owner_id: str | int | None = None) -> str:
        job_id = str(uuid.uuid4())
        job = Job(
            id=job_id, idea=idea, owner_id=str(owner_id) if owner_id is not None else None,
            provider=provider, config=config or {},
            status="pending", created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        )
        self._r.hset(self._JOBS_KEY, job_id, json.dumps(asdict(job), default=str))
        self._r.rpush(self._QUEUE_KEY, job_id)
        return job_id

    def dequeue(self, worker_id: str, block_s: int = 5) -> Optional[Job]:
        result = self._r.blpop(self._QUEUE_KEY, timeout=block_s)
        if not result:
            return None
        job_id = result[1]
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        raw = self._r.hget(self._JOBS_KEY, job_id)
        if not raw:
            return None
        data = json.loads(raw)
        data.update(status="running", worker_id=worker_id, started_at=now,
                    heartbeat_at=now, attempts=data.get("attempts", 0) + 1)
        self._r.hset(self._JOBS_KEY, job_id, json.dumps(data, default=str))
        return Job(**{k: data.get(k) for k in Job.__dataclass_fields__})

    def heartbeat(self, job_id: str) -> None:
        raw = self._r.hget(self._JOBS_KEY, job_id)
        if raw:
            data = json.loads(raw)
            data["heartbeat_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._r.hset(self._JOBS_KEY, job_id, json.dumps(data, default=str))

    def record_progress(self, job_id: str, stage: str) -> None:
        raw = self._r.hget(self._JOBS_KEY, job_id)
        if raw:
            data = json.loads(raw)
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            data.update(last_stage=stage, last_stage_at=now, heartbeat_at=now)
            self._r.hset(self._JOBS_KEY, job_id, json.dumps(data, default=str))

    def mark_deadline_exceeded(self, job_id: str, stage: str, deadline_at: str) -> None:
        raw = self._r.hget(self._JOBS_KEY, job_id)
        if raw:
            data = json.loads(raw)
            data.update(
                status="failed", completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                error=f"deadline_exceeded at stage {stage}", last_stage=stage,
                last_stage_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                deadline_at=deadline_at, deadline_exceeded=True,
            )
            self._r.hset(self._JOBS_KEY, job_id, json.dumps(data, default=str))

    def complete(self, job_id: str, result: dict) -> None:
        raw = self._r.hget(self._JOBS_KEY, job_id)
        if raw:
            data = json.loads(raw)
            data.update(status="completed", completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        result_json=json.dumps(result, default=str))
            self._r.hset(self._JOBS_KEY, job_id, json.dumps(data, default=str))

    def fail(self, job_id: str, error: str, retry: bool = True) -> None:
        raw = self._r.hget(self._JOBS_KEY, job_id)
        if not raw:
            return
        data = json.loads(raw)
        attempts = data.get("attempts", 1)
        max_att  = data.get("max_attempts", MAX_ATTEMPTS)
        if retry and attempts < max_att:
            data.update(status="pending", worker_id=None, error=f"attempt {attempts} failed: {error[:200]}")
            self._r.hset(self._JOBS_KEY, job_id, json.dumps(data, default=str))
            self._r.rpush(self._QUEUE_KEY, job_id)
        else:
            data.update(status="failed", error=error[:500])
            self._r.hset(self._JOBS_KEY, job_id, json.dumps(data, default=str))

    def reclaim_stale(self, timeout_s: int = HEARTBEAT_TIMEOUT_S) -> int:
        """Requeue or fail stale running jobs, matching SQLiteQueue semantics.

        Redis hashes do not gain reliable expiry behavior merely by storing a
        heartbeat timestamp, so recover explicitly from the persisted job
        records.  Worker loops may call this concurrently; duplicate recovery
        is harmless because only jobs still marked ``running`` are considered.
        """
        cutoff_ts = time.time() - timeout_s
        cutoff_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff_ts))
        reclaimed = 0
        for raw in self._r.hvals(self._JOBS_KEY):
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                continue
            job_id = data.get("id")
            if job_id and self._reclaim_one_stale(job_id, cutoff_str):
                reclaimed += 1
        return reclaimed

    def _reclaim_one_stale(self, job_id: str, cutoff_str: str) -> bool:
        """Atomically reclaim one stale Redis job using optimistic CAS.

        Redis stores queue jobs in one hash, so WATCH protects the hash while
        this method re-reads the specific job field and queues its replacement.
        If another worker changes the hash first, EXEC aborts and we re-check
        state; this prevents duplicate rpush operations.
        """
        for _ in range(3):
            pipe = self._r.pipeline()
            try:
                pipe.watch(self._JOBS_KEY)
                raw = pipe.hget(self._JOBS_KEY, job_id)
                if not raw:
                    return False
                data = json.loads(raw)
                heartbeat_at = data.get("heartbeat_at")
                if data.get("status") != "running" or not heartbeat_at or heartbeat_at >= cutoff_str:
                    return False
                if data.get("attempts", 0) >= data.get("max_attempts", MAX_ATTEMPTS):
                    data.update(status="failed", error="Worker crashed — max attempts reached")
                    requeue = False
                else:
                    data.update(status="pending", worker_id=None, started_at=None, heartbeat_at=None)
                    requeue = True
                pipe.multi()
                pipe.hset(self._JOBS_KEY, job_id, json.dumps(data, default=str))
                if requeue:
                    pipe.rpush(self._QUEUE_KEY, job_id)
                pipe.execute()
                return True
            except Exception as exc:
                # redis-py raises WatchError on a competing write.  Avoid a
                # hard dependency in the optional Redis backend while never
                # swallowing unrelated client/serialization failures.
                if type(exc).__name__ == "WatchError":
                    continue
                raise
            finally:
                pipe.reset()
        return False

    def get_job(self, job_id: str, **_) -> Optional[Job]:
        raw = self._r.hget(self._JOBS_KEY, job_id)
        if not raw:
            return None
        data = json.loads(raw)
        return Job(**{k: data.get(k) for k in Job.__dataclass_fields__})

    def get_job_for_owner(self, job_id: str, owner_id: str | int) -> Optional[Job]:
        job = self.get_job(job_id)
        return job if job and job.owner_id == str(owner_id) else None

    def list_jobs(self, status: str | None = None, limit: int = 50) -> list[Job]:
        all_raw = self._r.hvals(self._JOBS_KEY)
        jobs = []
        for raw in all_raw:
            try:
                data = json.loads(raw)
                if status is None or data.get("status") == status:
                    jobs.append(Job(**{k: data.get(k) for k in Job.__dataclass_fields__}))
            except Exception:
                pass
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def list_jobs_for_owner(self, owner_id: str | int, status: str | None = None,
                            limit: int = 50) -> list[Job]:
        owner = str(owner_id)
        jobs = [job for job in self.list_jobs(status=status, limit=10000) if job.owner_id == owner]
        return jobs[:limit]

    def stats(self) -> dict:
        jobs = self.list_jobs(limit=10000)
        counts: dict[str, int] = {}
        for j in jobs:
            counts[j.status] = counts.get(j.status, 0) + 1
        return {**{"pending": 0, "running": 0, "completed": 0, "failed": 0}, **counts,
                "total": len(jobs)}

    def stats_for_owner(self, owner_id: str | int) -> dict:
        jobs = self.list_jobs_for_owner(owner_id, limit=10000)
        counts: dict[str, int] = {}
        for job in jobs:
            counts[job.status] = counts.get(job.status, 0) + 1
        return {**{"pending": 0, "running": 0, "completed": 0, "failed": 0}, **counts,
                "total": len(jobs)}


# ── Singleton factory ──────────────────────────────────────────────────────────

def get_queue() -> SQLiteQueue | RedisQueue:
    """
    Returns the active queue backend.
    Use REDIS_URL env var to switch to Redis (e.g. in production / multi-machine).
    """
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        return RedisQueue(redis_url)
    return SQLiteQueue()


# Process-level singleton — workers and API share the same object
forge_queue = get_queue()
