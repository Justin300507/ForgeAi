"""
Exp140: V15 supervisor process creation must use fork() on the actual
production target (Linux/Render), not spawn() everywhere.

Live incident (2026-07-22, same day Exp136 shipped): Render's "forgeai"
web service (free tier, 512MB, no disk) exceeded its memory limit while a
user's generation job was running, leaving the frontend's Generation log
stuck at "Waiting for pipeline output... 0 lines" forever (the job's
parent-child IPC connection died with the OOM-killed process, so no
terminal event was ever sent).

Root cause: app/jobs/v15_supervisor.py's own code comments say "the
production target is Windows" and unconditionally used
multiprocessing.get_context("spawn") for every V15 job. spawn is the ONLY
option on Windows (correct for this repo's dev environment) but starts a
brand-new interpreter that re-imports this app's entire dependency chain
from scratch -- on top of the already-running parent server's own copy of
the same -- which is expensive enough to OOM a 512MB instance. Actual
production (Render) is Linux, where fork() is available and is
copy-on-write against the parent's already-loaded memory instead.

This file cannot execute the fork() path itself (this dev machine is
Windows; multiprocessing has no "fork" context registered there at all --
mp.get_context("fork") raises ValueError). It instead verifies the
DISPATCH LOGIC is correct by mocking os.name, and verifies the specific
post-fork SQLAlchemy safety mitigation (engine.dispose(close=False) as
the child's first action, before any DB use -- SQLite does not support
sharing a connection across processes, and a forked child inherits the
parent's already-opened pooled connections otherwise) fires only on the
fork path, never on spawn.

Run directly: python tests/reliability/test_exp140_supervisor_fork_on_linux.py
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import app.database  # noqa: F401 -- warm the import before mock.patch("app.database.X", ...)
import app.jobs.v15_supervisor as sup


class _StopEarly(Exception):
    """Raised from the mocked ctx.Process(...) call itself, before
    process.start() or the polling loop -- these two tests only care what
    start-method string reached mp.get_context(...)."""


def _fake_context_that_stops_before_polling():
    fake_ctx = mock.MagicMock()
    fake_ctx.Queue.return_value = mock.MagicMock()
    fake_ctx.Process.side_effect = _StopEarly()
    return fake_ctx


def test_run_v15_supervisor_uses_fork_context_on_non_windows():
    """The actual production OS (Linux) must get the cheap, copy-on-write
    start method, not the interpreter-duplicating one this module's
    docstring shows was reasoned about for Windows only."""
    fake_ctx = _fake_context_that_stops_before_polling()

    with mock.patch.object(sup.os, "name", "posix"), \
         mock.patch.object(sup.mp, "get_context", return_value=fake_ctx) as get_ctx:
        try:
            sup.run_v15_supervisor(
                "job-1", options={}, on_event=lambda *a: None,
                pipeline_runner=lambda *a: {"status": "done"},
            )
        except _StopEarly:
            pass  # expected -- stopped intentionally before the poll loop

    get_ctx.assert_called_once_with("fork")


def test_run_v15_supervisor_uses_spawn_context_on_windows():
    """Windows has no fork() at all -- the dev-environment path must be
    completely unchanged by this fix."""
    fake_ctx = _fake_context_that_stops_before_polling()

    with mock.patch.object(sup.os, "name", "nt"), \
         mock.patch.object(sup.mp, "get_context", return_value=fake_ctx) as get_ctx:
        try:
            sup.run_v15_supervisor(
                "job-1", options={}, on_event=lambda *a: None,
                pipeline_runner=lambda *a: {"status": "done"},
            )
        except _StopEarly:
            pass

    get_ctx.assert_called_once_with("spawn")


def test_child_disposes_inherited_engine_on_non_windows_before_any_db_use():
    """The forked child's very first action must drop any inherited
    pooled connections (close=False: don't touch what the parent may still
    be using) before touching SessionLocal at all."""
    call_order = []

    fake_engine = mock.MagicMock()
    fake_engine.dispose.side_effect = lambda **kw: call_order.append(("dispose", kw))

    class _FakeSessionLocal:
        def __call__(self):
            call_order.append(("SessionLocal", {}))
            db = mock.MagicMock()
            db.query.return_value.filter.return_value.first.return_value = None
            return db

    messages = mock.MagicMock()
    with mock.patch.object(sup.os, "name", "posix"), \
         mock.patch("app.database.engine", fake_engine), \
         mock.patch("app.database.SessionLocal", _FakeSessionLocal()):
        sup._run_v15_child("job-1", {}, messages, pipeline_runner=None)

    assert call_order, "neither dispose nor SessionLocal was ever called"
    assert call_order[0] == ("dispose", {"close": False}), (
        f"dispose(close=False) must be the FIRST action in the child, got: {call_order}"
    )


def test_child_does_not_dispose_engine_on_windows():
    """spawn's child is a fresh interpreter with no connections to have
    inherited yet -- disposing there would be dead code exercising a risk
    that doesn't exist on that path."""
    fake_engine = mock.MagicMock()

    class _FakeSessionLocal:
        def __call__(self):
            db = mock.MagicMock()
            db.query.return_value.filter.return_value.first.return_value = None
            return db

    messages = mock.MagicMock()
    with mock.patch.object(sup.os, "name", "nt"), \
         mock.patch("app.database.engine", fake_engine), \
         mock.patch("app.database.SessionLocal", _FakeSessionLocal()):
        sup._run_v15_child("job-1", {}, messages, pipeline_runner=None)

    fake_engine.dispose.assert_not_called()


def test_pipeline_runner_shortcut_still_skips_the_dispose_branch_entirely():
    """The test/fixture pipeline_runner path (used by every other
    supervisor test in this suite) returns before app.database is even
    imported -- must keep working exactly as before, on both platforms."""
    messages = mock.MagicMock()
    calls = []

    def fixture_runner(job_id, options, msgs):
        calls.append(job_id)
        return {"status": "done"}

    with mock.patch.object(sup.os, "name", "posix"):
        sup._run_v15_child("job-1", {}, messages, pipeline_runner=fixture_runner)

    assert calls == ["job-1"]
    sent = [c.args[0] for c in messages.put.call_args_list]
    assert any(m.get("type") == "result" for m in sent)


if __name__ == "__main__":
    import traceback
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception:
            failed += 1
            print(f"ERROR: {t.__name__}:")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
