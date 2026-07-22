"""Zero-network checks for queue watchdog durability and child isolation.

Run directly: backend\\venv\\Scripts\\python.exe backend/tests/reliability/test_queue_watchdog.py
"""
from __future__ import annotations

import gc
import io
import json
import sqlite3
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def successful_runner(job):
    return {"status": "done", "forge_score": 91, "runtime": {"success": True}}


def leaking_failure_runner(job):
    raise RuntimeError("PROMPT=do not persist this secret")


def slow_runner(job):
    time.sleep(5)
    return {"status": "done"}


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.lists = {}
        self.versions = {}
        self.on_multi = None

    def _changed(self, key):
        self.versions[key] = self.versions.get(key, 0) + 1

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value
        self._changed(key)

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        self._changed(key)

    def blpop(self, key, timeout=0):
        values = self.lists.get(key, [])
        return (key, values.pop(0)) if values else None

    def hvals(self, key):
        return list(self.hashes.get(key, {}).values())

    def pipeline(self):
        return FakePipeline(self)


class WatchError(RuntimeError):
    pass


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.watched = {}
        self.commands = []

    def watch(self, key):
        self.watched[key] = self.redis.versions.get(key, 0)

    def hget(self, key, field):
        return self.redis.hget(key, field)

    def multi(self):
        hook, self.redis.on_multi = self.redis.on_multi, None
        if hook:
            hook()

    def hset(self, key, field, value):
        self.commands.append(("hset", key, field, value))

    def rpush(self, key, value):
        self.commands.append(("rpush", key, value))

    def execute(self):
        if any(self.redis.versions.get(key, 0) != version for key, version in self.watched.items()):
            raise WatchError("watched key changed")
        for command in self.commands:
            getattr(self.redis, command[0])(*command[1:])

    def reset(self):
        self.commands.clear()


def _queue(path: Path):
    from app.queue.job_queue import SQLiteQueue
    return SQLiteQueue(path)


def _claim(q, idea="A reliable CRM app"):
    job_id = q.enqueue(idea)
    job = q.dequeue("test-worker")
    assert job and job.id == job_id
    return job_id, job


def test_additive_migration_and_progress() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "old.db"
        conn = sqlite3.connect(db)
        try:
            conn.execute("""CREATE TABLE jobs (
                id TEXT PRIMARY KEY, idea TEXT NOT NULL, provider TEXT, config_json TEXT,
                status TEXT, worker_id TEXT, attempts INTEGER, max_attempts INTEGER,
                created_at TEXT, started_at TEXT, completed_at TEXT, heartbeat_at TEXT,
                result_json TEXT, error TEXT)""")
        finally:
            conn.close()
        q = _queue(db)
        conn = sqlite3.connect(db)
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        finally:
            conn.close()
        assert {"last_stage", "last_stage_at", "deadline_at", "deadline_exceeded"} <= cols
        job_id, _ = _claim(q)
        q.record_progress(job_id, "generation")
        view = q.get_job(job_id).to_api()
        assert view["last_stage"] == "generation"
        assert view["last_stage_at"] and view["deadline_exceeded"] is False
        del q
        gc.collect()


def test_successful_spawned_child_result() -> None:
    from app.queue import worker
    with tempfile.TemporaryDirectory() as tmp:
        q = _queue(Path(tmp) / "queue.db")
        original = worker.forge_queue
        worker.forge_queue = q
        try:
            job_id, job = _claim(q)
            worker._process_job(job, "test-worker", pipeline_runner=successful_runner, deadline_s=5)
            stored = q.get_job(job_id)
            assert stored.status == "completed"
            assert stored.result["forge_score"] == 91
        finally:
            worker.forge_queue = original
            del q
            gc.collect()


def test_deadline_is_terminal_and_worker_recovers() -> None:
    from app.queue import worker
    with tempfile.TemporaryDirectory() as tmp:
        q = _queue(Path(tmp) / "queue.db")
        original = worker.forge_queue
        worker.forge_queue = q
        try:
            timed_out_id, timed_out = _claim(q)
            worker._process_job(timed_out, "test-worker", pipeline_runner=slow_runner, deadline_s=1)
            failed = q.get_job(timed_out_id)
            assert failed.status == "failed"
            assert failed.deadline_exceeded is True
            assert failed.error == "deadline_exceeded at stage queued"
            assert failed.attempts == 1  # terminal watchdog failure never re-queues

            next_id, next_job = _claim(q, "A simple todo app")
            worker._process_job(next_job, "test-worker", pipeline_runner=successful_runner, deadline_s=5)
            assert q.get_job(next_id).status == "completed"
        finally:
            worker.forge_queue = original
            del q
            gc.collect()


def test_child_exception_never_leaks_prompt_text() -> None:
    from app.queue import worker
    with tempfile.TemporaryDirectory() as tmp:
        q = _queue(Path(tmp) / "queue.db")
        original = worker.forge_queue
        worker.forge_queue = q
        try:
            job_id, job = _claim(q, "sensitive idea should not appear in an exception")
            worker._process_job(job, "test-worker", pipeline_runner=leaking_failure_runner, deadline_s=5)
            api = q.get_job(job_id).to_api()
            assert api["status"] == "pending"  # ordinary child error retains retry semantics
            assert api["error"] == "attempt 1 failed: pipeline_child_error:RuntimeError"
            assert "PROMPT=" not in str(api) and "sensitive idea" not in api["error"]
        finally:
            worker.forge_queue = original
            del q
            gc.collect()


def test_redis_watchdog_interface_parity() -> None:
    from app.queue.job_queue import RedisQueue
    q = RedisQueue.__new__(RedisQueue)
    q._r = FakeRedis()
    q._QUEUE_KEY = "pending"
    q._JOBS_KEY = "jobs"
    job_id = q.enqueue("A durable queue app")
    q.record_progress(job_id, "generation")
    q.mark_deadline_exceeded(job_id, "generation", "2030-01-01T00:00:00Z")
    view = q.get_job(job_id).to_api()
    assert view["status"] == "failed"
    assert view["deadline_exceeded"] is True
    assert view["last_stage"] == "generation"
    assert view["error"] == "deadline_exceeded at stage generation"


def test_redis_reclaims_stale_workers_without_ttl_assumptions() -> None:
    from app.queue.job_queue import RedisQueue
    q = RedisQueue.__new__(RedisQueue)
    q._r = FakeRedis()
    q._QUEUE_KEY = "pending"
    q._JOBS_KEY = "jobs"
    retry_id = q.enqueue("A retryable stale job")
    terminal_id = q.enqueue("A terminal stale job")
    retry = q.dequeue("worker-a")
    terminal = q.dequeue("worker-b")
    assert retry and terminal
    for job_id, attempts in ((retry_id, 1), (terminal_id, 3)):
        data = json.loads(q._r.hget(q._JOBS_KEY, job_id))
        data.update(attempts=attempts, max_attempts=3, heartbeat_at="2000-01-01T00:00:00Z")
        q._r.hset(q._JOBS_KEY, job_id, json.dumps(data))
    assert q.reclaim_stale(timeout_s=1) == 2
    assert q.get_job(retry_id).status == "pending"
    assert q.get_job(retry_id).worker_id is None
    assert q.get_job(terminal_id).status == "failed"
    assert q.get_job(terminal_id).error == "Worker crashed — max attempts reached"
    assert q._r.lists[q._QUEUE_KEY].count(retry_id) == 1
    assert terminal_id not in q._r.lists[q._QUEUE_KEY]


def test_redis_competing_reclaimers_commit_only_once() -> None:
    from app.queue.job_queue import RedisQueue
    q = RedisQueue.__new__(RedisQueue)
    q._r = FakeRedis()
    q._QUEUE_KEY = "pending"
    q._JOBS_KEY = "jobs"
    job_id = q.enqueue("A stale job that two workers notice")
    assert q.dequeue("worker-a")
    data = json.loads(q._r.hget(q._JOBS_KEY, job_id))
    data["heartbeat_at"] = "2000-01-01T00:00:00Z"
    q._r.hset(q._JOBS_KEY, job_id, json.dumps(data))
    # The competing reclaimer runs between the outer read and EXEC.  Its
    # update invalidates the outer WATCH; outer retry sees pending and does
    # nothing, so only one rpush can be committed.
    q._r.on_multi = lambda: q.reclaim_stale(timeout_s=1)
    assert q.reclaim_stale(timeout_s=1) == 0
    assert q.get_job(job_id).status == "pending"
    assert q._r.lists[q._QUEUE_KEY].count(job_id) == 1


def test_worker_stdout_never_includes_job_idea() -> None:
    from app.queue import worker
    with tempfile.TemporaryDirectory() as tmp:
        q = _queue(Path(tmp) / "queue.db")
        original = worker.forge_queue
        worker.forge_queue = q
        secret_idea = "PROMPT_SECRET: do not write this to stdout"
        try:
            job_id, job = _claim(q, secret_idea)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                worker._process_job(job, "test-worker", pipeline_runner=successful_runner, deadline_s=5)
            assert q.get_job(job_id).status == "completed"
            assert secret_idea not in stdout.getvalue()
            assert "PROMPT_SECRET" not in stdout.getvalue()
        finally:
            worker.forge_queue = original
            del q
            gc.collect()


def test_v15_queue_config_is_allowlisted() -> None:
    from app.queue.job_queue import UnsupportedQueueConfig, validate_v15_queue_config
    assert validate_v15_queue_config({"deploy": False, "deploy_to": "both"}) == {
        "deploy": False, "deploy_to": "both"}
    try:
        validate_v15_queue_config({"use_tournament": True})
    except UnsupportedQueueConfig as exc:
        assert str(exc) == "unsupported queue configuration"
    else:
        raise AssertionError("unknown legacy config must not be silently dropped")


def main() -> None:
    tests = [
        test_additive_migration_and_progress,
        test_successful_spawned_child_result,
        test_deadline_is_terminal_and_worker_recovers,
        test_child_exception_never_leaks_prompt_text,
        test_redis_watchdog_interface_parity,
        test_redis_reclaims_stale_workers_without_ttl_assumptions,
        test_redis_competing_reclaimers_commit_only_once,
        test_worker_stdout_never_includes_job_idea,
        test_v15_queue_config_is_allowlisted,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)}/{len(tests)} queue watchdog tests passed")


if __name__ == "__main__":
    main()
