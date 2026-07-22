"""Zero-network regression tests for queue ownership and operator controls.

Run directly:
  backend\\venv\\Scripts\\python.exe backend/tests/reliability/test_queue_ownership.py
"""
from __future__ import annotations

import gc
import inspect
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.lists = {}

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hvals(self, key):
        return list(self.hashes.get(key, {}).values())

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)


class User:
    def __init__(self, user_id):
        self.id = user_id


class Dispatcher:
    def status(self):
        return {"alive": 0}


def _queue(path: Path):
    from app.queue.job_queue import SQLiteQueue
    return SQLiteQueue(path)


def test_owner_column_migrates_an_existing_database() -> None:
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
            columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        finally:
            conn.close()
        assert "owner_id" in columns
        job_id = q.enqueue("direct workers remain supported")
        assert q.get_job(job_id).owner_id is None
        del q
        gc.collect()


def test_sqlite_owner_filtering_idor_and_stats() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        q = _queue(Path(tmp) / "queue.db")
        alice_pending = q.enqueue("Alice private app", owner_id=101)
        alice_done = q.enqueue("Alice second private app", owner_id=101)
        bob = q.enqueue("Bob private app", owner_id=202)
        q.dequeue("worker")  # oldest (Alice) becomes running
        q.complete(alice_done, {"ok": True})

        assert q.get_job_for_owner(alice_pending, 101).id == alice_pending
        assert q.get_job_for_owner(alice_pending, 202) is None
        assert [job.id for job in q.list_jobs_for_owner(101)] == [alice_done, alice_pending]
        assert [job.id for job in q.list_jobs_for_owner(202)] == [bob]
        assert q.stats_for_owner(101) == {
            "pending": 0, "running": 1, "completed": 1, "failed": 0, "total": 2,
        }
        assert q.stats_for_owner(202) == {
            "pending": 1, "running": 0, "completed": 0, "failed": 0, "total": 1,
        }
        del q
        gc.collect()


def test_redis_owner_parity() -> None:
    from app.queue.job_queue import RedisQueue
    q = RedisQueue.__new__(RedisQueue)
    q._r = FakeRedis()
    q._QUEUE_KEY = "pending"
    q._JOBS_KEY = "jobs"
    alice = q.enqueue("Alice redis job", owner_id=1)
    bob = q.enqueue("Bob redis job", owner_id=2)
    assert q.get_job_for_owner(alice, 1).owner_id == "1"
    assert q.get_job_for_owner(alice, 2) is None
    assert [job.id for job in q.list_jobs_for_owner(2)] == [bob]
    assert q.stats_for_owner(1)["total"] == 1


def test_public_enqueue_binds_authenticated_user_not_forged_body_owner() -> None:
    from pydantic import ValidationError
    from app.queue import api
    with tempfile.TemporaryDirectory() as tmp:
        q = _queue(Path(tmp) / "queue.db")
        old_queue, old_dispatcher = api.forge_queue, api.dispatcher
        api.forge_queue, api.dispatcher = q, Dispatcher()
        try:
            # Owner fields are rejected rather than silently accepted.  A valid
            # request is then bound solely to the authenticated JWT user.
            try:
                api.SubmitRequest.model_validate({
                    "idea": "Build a secure project tracker", "owner_id": 999,
                })
            except ValidationError:
                pass
            else:
                raise AssertionError("request body must not accept owner_id")
            request = api.SubmitRequest(idea="Build a secure project tracker")
            response = api.submit_job(request, current_user=User(7))
            assert q.get_job(response.job_id).owner_id == "7"
            assert q.get_job_for_owner(response.job_id, 999) is None
        finally:
            api.forge_queue, api.dispatcher = old_queue, old_dispatcher
            del q
            gc.collect()


def test_api_foreign_status_is_generic_not_found() -> None:
    from fastapi import HTTPException
    from app.queue import api
    with tempfile.TemporaryDirectory() as tmp:
        q = _queue(Path(tmp) / "queue.db")
        job_id = q.enqueue("An app belonging to another user", owner_id=1)
        old_queue = api.forge_queue
        api.forge_queue = q
        try:
            try:
                api.job_status(job_id, current_user=User(2))
            except HTTPException as exc:
                assert exc.status_code == 404 and exc.detail == "Job not found"
            else:
                raise AssertionError("foreign job must be indistinguishable from missing")
        finally:
            api.forge_queue = old_queue
            del q
            gc.collect()


def test_operator_controls_require_configured_constant_time_gate() -> None:
    from fastapi import HTTPException
    from app.queue import api
    original = os.environ.pop("FORGE_QUEUE_OPERATOR_TOKEN", None)
    try:
        for supplied, expected_status in ((None, 404), ("wrong", 404)):
            try:
                api.require_queue_operator(supplied)
            except HTTPException as exc:
                assert exc.status_code == expected_status
            else:
                raise AssertionError("unset operator secret must disable controls")
        os.environ["FORGE_QUEUE_OPERATOR_TOKEN"] = "operator-secret"
        try:
            api.require_queue_operator("wrong")
        except HTTPException as exc:
            assert exc.status_code == 403
        else:
            raise AssertionError("incorrect operator secret must fail")
        api.require_queue_operator("operator-secret")
        for handler in (api.start_workers, api.stop_workers, api.scale_workers,
                        api.workers_status, api.reclaim_stale):
            assert "_operator" in inspect.signature(handler).parameters
    finally:
        if original is None:
            os.environ.pop("FORGE_QUEUE_OPERATOR_TOKEN", None)
        else:
            os.environ["FORGE_QUEUE_OPERATOR_TOKEN"] = original


def main() -> None:
    tests = [
        test_owner_column_migrates_an_existing_database,
        test_sqlite_owner_filtering_idor_and_stats,
        test_redis_owner_parity,
        test_public_enqueue_binds_authenticated_user_not_forged_body_owner,
        test_api_foreign_status_is_generic_not_found,
        test_operator_controls_require_configured_constant_time_gate,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)}/{len(tests)} queue ownership tests passed")


if __name__ == "__main__":
    main()
