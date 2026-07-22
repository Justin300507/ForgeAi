"""
Exp140 (part 2): the /queue worker system (app/queue/worker.py) had the
exact same spawn-everywhere pattern as app/jobs/v15_supervisor.py -- same
"Windows-spawn-safe" framing, same unconditional
multiprocessing.get_context("spawn"). Same live incident (2026-07-22
Render OOM), same fix: fork() on the actual production OS (Linux), with
the same post-fork SQLAlchemy engine-disposal safety net.

See test_exp140_supervisor_fork_on_linux.py for the full incident
writeup; this file only covers the second, independent occurrence of the
bug so it doesn't silently regress back once the first is forgotten.

This dev machine is Windows and cannot execute the fork() path directly
(mp.get_context("fork") raises ValueError there); these tests verify the
dispatch logic and dispose-on-fork behavior by mocking os.name.

Run directly: python tests/reliability/test_exp140_queue_worker_fork_on_linux.py
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import app.database  # noqa: F401 -- warm imports before mock.patch("module.X", ...) or
import app.services.v15_orchestrator  # noqa: F401 -- os.name="posix" mid-mock breaks a fresh
import app.core.context  # noqa: F401 -- import's pathlib.Path() OS dispatch (see test comments)
import app.queue.worker as worker


class _StopEarly(Exception):
    """Raised from the mocked ctx.Process(...) call itself, before
    process.start() or the polling loop -- these tests only care what
    start-method string reached mp.get_context(...)."""


def _fake_context_that_stops_before_polling():
    fake_ctx = mock.MagicMock()
    fake_ctx.Queue.return_value = mock.MagicMock()
    fake_ctx.Process.side_effect = _StopEarly()
    return fake_ctx


class _FakeJob:
    id = "job-1"
    idea = "test"
    provider = "auto"
    config = {}


def test_run_pipeline_in_child_uses_fork_context_on_non_windows():
    fake_ctx = _fake_context_that_stops_before_polling()
    with mock.patch.object(worker.os, "name", "posix"), \
         mock.patch.object(worker.mp, "get_context", return_value=fake_ctx) as get_ctx:
        try:
            worker._run_pipeline_in_child(_FakeJob(), pipeline_runner=lambda job: {"status": "done"})
        except _StopEarly:
            pass

    get_ctx.assert_called_once_with("fork")


def test_run_pipeline_in_child_uses_spawn_context_on_windows():
    fake_ctx = _fake_context_that_stops_before_polling()
    with mock.patch.object(worker.os, "name", "nt"), \
         mock.patch.object(worker.mp, "get_context", return_value=fake_ctx) as get_ctx:
        try:
            worker._run_pipeline_in_child(_FakeJob(), pipeline_runner=lambda job: {"status": "done"})
        except _StopEarly:
            pass

    get_ctx.assert_called_once_with("spawn")


def test_pipeline_child_disposes_inherited_engine_on_non_windows_before_import():
    """pipeline_runner=None exercises the REAL pipeline branch, where the
    dispose fix actually lives -- generate_project_v15 itself is mocked
    out so this test only proves dispose() fires, not that generation
    works."""
    fake_engine = mock.MagicMock()
    messages = mock.MagicMock()

    with mock.patch.object(worker.os, "name", "posix"), \
         mock.patch("app.database.engine", fake_engine), \
         mock.patch(
             "app.core.context.GenerationContext.__init__", return_value=None,
         ), \
         mock.patch(
             "app.services.v15_orchestrator.generate_project_v15",
             return_value={"status": "done"},
         ), \
         mock.patch.object(worker, "validate_v15_queue_config", return_value={}):
        worker._pipeline_child(_FakeJob(), messages, pipeline_runner=None)

    fake_engine.dispose.assert_called_once_with(close=False)


def test_pipeline_child_does_not_dispose_engine_on_windows():
    fake_engine = mock.MagicMock()
    messages = mock.MagicMock()

    with mock.patch.object(worker.os, "name", "nt"), \
         mock.patch("app.database.engine", fake_engine), \
         mock.patch(
             "app.services.v15_orchestrator.generate_project_v15",
             return_value={"status": "done"},
         ), \
         mock.patch.object(worker, "validate_v15_queue_config", return_value={}):
        worker._pipeline_child(_FakeJob(), messages, pipeline_runner=None)

    fake_engine.dispose.assert_not_called()


def test_pipeline_runner_shortcut_skips_the_dispose_branch_entirely():
    """The fixture pipeline_runner path (used by every other worker test in
    this suite) must keep working exactly as before, on both platforms --
    it returns before app.database's engine is ever touched."""
    messages = mock.MagicMock()
    calls = []

    def fixture_runner(job):
        calls.append(job.id)
        return {"status": "done"}

    with mock.patch.object(worker.os, "name", "posix"):
        worker._pipeline_child(_FakeJob(), messages, pipeline_runner=fixture_runner)

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
