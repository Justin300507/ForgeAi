"""Direct, no-network checks for the legacy /jobs V15 process supervisor.

Run: backend\\venv\\Scripts\\python.exe backend/tests/reliability/test_v15_jobs_supervisor.py
"""
from __future__ import annotations

import hashlib
import multiprocessing as mp
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def child_success(_job_id, _options, messages):
    messages.put({"type": "stage", "stage": "generation"})
    messages.put({"type": "selected_provider", "provider": "openai"})
    return {
        "status": "done", "project_name": "safe_project", "forge_score": 91,
        "backend_url": "https://api.example.test", "idea": "PROMPT_SECRET",
        "error": "JWT_SECRET=not-for-ipc",
        "v6_result": {"zip_path": "generated_projects/safe_project.zip"},
    }


def child_actual_provider(_job_id, _options, messages):
    """A child fixture with only the allowed provider-attempt protocol."""
    messages.put({
        "type": "provider_attempt",
        "stage": "planning",
        "provider": "openai",
        "attempt": 1,
        "status": "failed",
        "prompt": "PROMPT_SECRET must never cross IPC",
    })
    messages.put({
        "type": "provider_attempt",
        "stage": "planning",
        "provider": "cerebras",
        "attempt": 2,
        "status": "succeeded",
        "error": "JWT_SECRET=must-not-cross",
    })
    return {"status": "done", "project_name": "provider_truth"}


def child_slow(_job_id, _options, messages):
    messages.put({"type": "stage", "stage": "verification"})
    time.sleep(5)
    return {"status": "done", "project_name": "late_result"}


def child_snapshot_fingerprint(_job_id, _options, _messages):
    """Return an opaque fingerprint so test IPC never exposes an env value."""
    value = os.environ.get("GITHUB_TOKEN", "").encode("utf-8")
    return {
        "status": "done",
        "project_name": hashlib.sha256(value).hexdigest(),
    }


def child_with_sleeping_grandchild(_job_id, _options, messages):
    """V15 child fixture: taskkill /T must also remove this descendant."""
    pid_file = os.environ["FORGE_V15_TEST_GRANDCHILD_PID_FILE"]
    grandchild = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    Path(pid_file).write_text(str(grandchild.pid), encoding="ascii")
    messages.put({"type": "stage", "stage": "verification"})
    time.sleep(10)
    return {"status": "done", "project_name": "should_not_finish"}


def _events():
    values = []
    return values, lambda kind, value: values.append((kind, value))


def test_spawn_success_progress_and_redaction() -> None:
    from app.jobs.v15_supervisor import run_v15_supervisor
    events, on_event = _events()
    outcome = run_v15_supervisor(
        "job-123", options={"style_override": "dark"}, on_event=on_event,
        pipeline_runner=child_success, deadline_s=5,
    )
    assert outcome.last_stage == "generation"
    assert outcome.selected_provider == "openai"
    assert ("stage", "generation") in events and ("selected_provider", "openai") in events
    assert outcome.result["project_name"] == "safe_project"
    assert outcome.result["forge_score"] == 91.0
    assert "PROMPT_SECRET" not in repr(outcome.result)
    assert "JWT_SECRET" not in repr(outcome.result)
    assert outcome.result["v6_result"]["zip_path"] == "generated_projects/safe_project.zip"


def test_actual_provider_attempt_is_sanitized_and_wins_over_router_selection() -> None:
    from app.jobs.v15_supervisor import run_v15_supervisor
    events, on_event = _events()
    outcome = run_v15_supervisor(
        "job-provider-truth", options={}, on_event=on_event,
        pipeline_runner=child_actual_provider, deadline_s=5,
    )
    attempts = [value for kind, value in events if kind == "provider_attempt"]
    assert attempts == [
        {"stage": "planning", "provider": "openai", "attempt": 1, "status": "failed"},
        {"stage": "planning", "provider": "cerebras", "attempt": 2, "status": "succeeded"},
    ]
    assert outcome.effective_provider == "cerebras"
    assert "PROMPT_SECRET" not in repr(events)
    assert "JWT_SECRET" not in repr(events)


def test_spawn_options_cannot_carry_prompt_text() -> None:
    from app.jobs.v15_supervisor import _safe_options
    safe = _safe_options({
        "style_override": "PROMPT_SECRET write a marketplace",
        "motion_intensity": "PROMPT_SECRET",
        "include_landing_page": True,
    })
    assert safe == {"style_override": None, "motion_intensity": None, "include_landing_page": True}


def test_child_urls_strip_queries_and_fragments() -> None:
    from app.jobs.v15_supervisor import _safe_child_result
    result = _safe_child_result({
        "status": "done", "project_name": "safe_project",
        "backend_url": "https://api.example.test/v1?token=JWT_SECRET&PROMPT_SECRET=yes#fragment",
        "frontend_url": "https://app.example.test/dashboard?invite=private#top",
    })
    assert result["backend_url"] == "https://api.example.test/v1"
    assert result["frontend_url"] == "https://app.example.test/dashboard"
    assert "JWT_SECRET" not in repr(result) and "PROMPT_SECRET" not in repr(result)


def test_child_zip_requires_conventional_safe_path() -> None:
    from app.jobs.v15_supervisor import _safe_child_result
    safe = _safe_child_result({
        "status": "done", "project_name": "project_a",
        "v6_result": {"zip_path": r"C:\\workspace\\generated_projects\\project_a.zip"},
    })
    unsafe = _safe_child_result({
        "status": "done", "project_name": "project_a",
        "v6_result": {"zip_path": "generated_projects/project_a.zip?token=JWT_SECRET"},
    })
    assert safe["v6_result"]["zip_path"] == "generated_projects/project_a.zip"
    assert unsafe["v6_result"]["zip_path"] is None


def test_timeout_is_bounded_and_terminal_signal_is_safe() -> None:
    from app.jobs.v15_supervisor import V15JobDeadlineExceeded, run_v15_supervisor
    events, on_event = _events()
    try:
        run_v15_supervisor("job-timeout", options={}, on_event=on_event,
                           pipeline_runner=child_slow, deadline_s=1)
    except V15JobDeadlineExceeded as exc:
        assert exc.stage == "verification"
        assert exc.deadline_at is not None
    else:
        raise AssertionError("deadline must terminate the owned child")


def test_cancellation_wins_over_late_child_result() -> None:
    from app.jobs.v15_supervisor import V15JobCancelled, run_v15_supervisor
    cancelled = mp.Event()

    def on_event(kind, value):
        if kind == "stage" and value == "verification":
            cancelled.set()

    try:
        run_v15_supervisor("job-cancel", options={}, on_event=on_event,
                           is_cancelled=cancelled.is_set,
                           pipeline_runner=child_slow, deadline_s=5)
    except V15JobCancelled:
        pass
    else:
        raise AssertionError("cancel must win before a late result is accepted")


def test_spawn_environment_is_restored_and_snapshots_do_not_cross() -> None:
    from app.jobs.v15_supervisor import run_v15_supervisor

    name = "GITHUB_TOKEN"
    previous = os.environ.get(name)
    os.environ[name] = "parent-baseline"
    snapshots = ("first-child-only", "second-child-only")

    def run(snapshot: str) -> str:
        outcome = run_v15_supervisor(
            f"job-{snapshot[:5]}", options={}, on_event=lambda *_: None,
            credential_overrides={name: snapshot},
            pipeline_runner=child_snapshot_fingerprint, deadline_s=5,
        )
        return outcome.result["project_name"]

    try:
        # Concurrent supervisors prove their serialized spawn snapshots do not
        # inherit each other's credential overlay.
        with ThreadPoolExecutor(max_workers=2) as executor:
            observed = list(executor.map(run, snapshots))
        expected = [hashlib.sha256(value.encode("utf-8")).hexdigest() for value in snapshots]
        assert observed == expected
        assert os.environ[name] == "parent-baseline"
        assert "first-child-only" not in repr(observed)
        assert "second-child-only" not in repr(observed)
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def test_v15_credential_snapshot_allows_only_deployment_keys() -> None:
    from app.jobs.v15_supervisor import _credential_overrides

    allowed = _credential_overrides({
        "GITHUB_TOKEN": "opaque-github",
        "RAILWAY_TOKEN": "opaque-railway",
        "CLOUDFLARE_API_TOKEN": "opaque-cloudflare",
        "CLOUDFLARE_ACCOUNT_ID": "opaque-account",
        "OPENAI_API_KEY": "must-not-cross",
        "FORGE_V15_TEST_SNAPSHOT": "must-not-cross",
    })
    assert set(allowed) == {
        "GITHUB_TOKEN", "RAILWAY_TOKEN",
        "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
    }
    assert "must-not-cross" not in repr(allowed)


def test_windows_deadline_kills_spawned_grandchild_tree() -> None:
    """A real Windows child-tree check; skipped only outside the target OS."""
    if os.name != "nt":
        return
    from app.jobs.v15_supervisor import V15JobDeadlineExceeded, run_v15_supervisor

    name = "FORGE_V15_TEST_GRANDCHILD_PID_FILE"
    previous = os.environ.get(name)
    with tempfile.TemporaryDirectory() as tmp:
        pid_file = Path(tmp) / "grandchild.pid"
        os.environ[name] = str(pid_file)
        try:
            try:
                run_v15_supervisor(
                    "job-grandchild", options={}, on_event=lambda *_: None,
                    pipeline_runner=child_with_sleeping_grandchild, deadline_s=1,
                )
            except V15JobDeadlineExceeded:
                pass
            else:
                raise AssertionError("deadline must stop the child tree")
            deadline = time.monotonic() + 5
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert pid_file.exists(), "fixture child never started its grandchild"
            pid = int(pid_file.read_text(encoding="ascii"))
            # tasklist is Windows-native and observes the real process tree.
            while time.monotonic() < deadline:
                listing = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                    check=False, capture_output=True, text=True,
                ).stdout
                if str(pid) not in listing:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("V15 deadline left a spawned grandchild alive")
        finally:
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous


def child_with_log_lines(_job_id, _options, messages):
    messages.put({"type": "log", "line": "=== PRODUCT MANAGER (V6) ==="})
    messages.put({"type": "log", "line": "x" * 5000})  # oversized, must be bounded
    messages.put({"type": "log", "line": ""})  # blank, must be dropped
    return {"status": "done", "project_name": "safe_project", "forge_score": 91}


def test_log_lines_relay_through_supervisor_and_are_length_bounded() -> None:
    """V15 jobs previously left the frontend's log panel at 0 lines forever
    (the child's prints never crossed the IPC boundary). This is the
    parent-side half of the fix: a 'log' message becomes an on_event('log', ...)
    call, bounded so one giant line can't bloat the in-memory job store."""
    from app.jobs.v15_supervisor import run_v15_supervisor
    events, on_event = _events()
    run_v15_supervisor(
        "job-log-relay", options={}, on_event=on_event,
        pipeline_runner=child_with_log_lines, deadline_s=5,
    )
    log_events = [value for kind, value in events if kind == "log"]
    assert log_events == ["=== PRODUCT MANAGER (V6) ===", "x" * 2000]


def test_child_log_tee_redacts_known_secrets_and_relays_others() -> None:
    """Unit test for the child-side half: _ChildLogTee must relay ordinary
    progress text but never a line containing a live secret value, even
    though that same text is still written through to the real stdout
    (container logs) unchanged."""
    import io
    import queue as _queue
    from app.jobs.v15_supervisor import _ChildLogTee

    real_stdout = io.StringIO()
    messages: _queue.Queue = _queue.Queue()
    tee = _ChildLogTee(real_stdout, messages, frozenset({"sk-live-secret-123"}))

    tee.write("=== ARCHITECT (V6) ===\n")
    tee.write("token=sk-live-secret-123 leaked into a debug line\n")
    tee.write("   \n")  # whitespace-only, must be dropped

    assert real_stdout.getvalue() == (
        "=== ARCHITECT (V6) ===\n"
        "token=sk-live-secret-123 leaked into a debug line\n"
        "   \n"
    ), "the real stdout stream must be unaffected by relay/redaction"

    relayed = []
    while True:
        try:
            relayed.append(messages.get_nowait())
        except _queue.Empty:
            break
    assert relayed == [{"type": "log", "line": "=== ARCHITECT (V6) ==="}]


def test_additive_model_migration() -> None:
    from sqlalchemy import create_engine
    from app.jobs.v15_supervisor import ensure_generation_job_columns
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "old.db"
        conn = sqlite3.connect(path)
        try:
            conn.execute("CREATE TABLE generation_jobs (id VARCHAR(36) PRIMARY KEY, status VARCHAR(16))")
            conn.commit()
        finally:
            conn.close()
        engine = create_engine(f"sqlite:///{path}")
        try:
            ensure_generation_job_columns(engine)
            ensure_generation_job_columns(engine)  # idempotent upgrade retry
        finally:
            engine.dispose()  # Windows keeps SQLite handles open otherwise
        conn = sqlite3.connect(path)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(generation_jobs)")}
        finally:
            conn.close()
        assert {"progress_stage", "progress_updated_at", "effective_provider", "execution_token",
                "lease_expires_at", "deadline_at", "total_tokens", "estimated_cost_usd",
                "cache_hits"} <= columns


def main() -> None:
    tests = [
        test_spawn_success_progress_and_redaction,
        test_actual_provider_attempt_is_sanitized_and_wins_over_router_selection,
        test_spawn_options_cannot_carry_prompt_text,
        test_child_urls_strip_queries_and_fragments,
        test_child_zip_requires_conventional_safe_path,
        test_timeout_is_bounded_and_terminal_signal_is_safe,
        test_cancellation_wins_over_late_child_result,
        test_spawn_environment_is_restored_and_snapshots_do_not_cross,
        test_v15_credential_snapshot_allows_only_deployment_keys,
        test_windows_deadline_kills_spawned_grandchild_tree,
        test_log_lines_relay_through_supervisor_and_are_length_bounded,
        test_child_log_tee_redacts_known_secrets_and_relays_others,
        test_additive_model_migration,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)}/{len(tests)} V15 job supervisor tests passed")


if __name__ == "__main__":
    main()
