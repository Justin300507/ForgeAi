"""Authenticated, zero-provider smoke tests for the legacy ``/jobs`` V15 path.

Run directly from the repository root:
    backend\\venv\\Scripts\\python.exe backend\\tests\\reliability\\test_v15_jobs_api_smoke.py

Importing this module deliberately has no environment or application-import
side effects.  The direct runner first proves that property, then starts a
fresh process for the isolated SQLite/FastAPI test.  That child still uses the
real Windows-spawn V15 supervisor; only its module-level child pipeline fixture
is fake, so no provider, deployment, or generated project filesystem is used.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


_BACKEND = Path(__file__).resolve().parents[2]
_SELF = Path(__file__).resolve()
_CHILD_MARKER = "FORGE_V15_JOBS_API_SMOKE_CHILD"
_TEST_ENV_KEYS = (
    "DATABASE_URL",
    "SECRET_KEY",
    "FORGE_PIPELINE_VERSION",
    "FORGE_V15_JOB_DEADLINE_S",
)
_REAL_V15_SUPERVISOR = None
_MAIN = None


def child_safe_success(_job_id, _options, messages):
    """Picklable fake child used by the real spawn supervisor."""
    messages.put({"type": "stage", "stage": "verification"})
    messages.put({
        "type": "provider_attempt",
        "stage": "verification",
        "provider": "cerebras",
        "attempt": 1,
        "status": "succeeded",
    })
    return {
        "status": "done",
        "project_name": "smoke_safe_app",
        "forge_score": 91,
        "backend_url": "https://api.example.test/v1?token=must-not-persist",
        "frontend_url": "https://app.example.test/?invite=must-not-persist",
        "v6_result": {"zip_path": "generated_projects/smoke_safe_app.zip"},
        "idea": "must-not-cross-child-ipc",
        "error": "must-not-cross-child-ipc",
    }


def child_slow_for_deadline(_job_id, _options, messages):
    """Emit a stage then wait; the real supervisor must terminate this child."""
    messages.put({"type": "stage", "stage": "verification"})
    time.sleep(5)
    return {"status": "done", "project_name": "late-result"}


def _real_supervisor_with_success(*args, **kwargs):
    assert _REAL_V15_SUPERVISOR is not None
    return _REAL_V15_SUPERVISOR(*args, pipeline_runner=child_safe_success, **kwargs)


def _real_supervisor_with_short_deadline(*args, **kwargs):
    assert _REAL_V15_SUPERVISOR is not None
    return _REAL_V15_SUPERVISOR(*args, pipeline_runner=child_slow_for_deadline, **kwargs)


@contextmanager
def _isolated_app_context():
    """Set up and restore every app-import input in the test child only."""
    global _MAIN, _REAL_V15_SUPERVISOR

    original_env = {name: os.environ.get(name) for name in _TEST_ENV_KEYS}
    with tempfile.TemporaryDirectory(prefix="forgeai-v15-jobs-smoke-") as tmp:
        database_path = Path(tmp) / "jobs-smoke.db"
        os.environ.update({
            "DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
            "SECRET_KEY": secrets.token_hex(32),
            "FORGE_PIPELINE_VERSION": "v15",
        })
        os.environ.pop("FORGE_V15_JOB_DEADLINE_S", None)
        if str(_BACKEND) not in sys.path:
            sys.path.insert(0, str(_BACKEND))
        try:
            from app.jobs.v15_supervisor import run_v15_supervisor
            import main

            _REAL_V15_SUPERVISOR = run_v15_supervisor
            _MAIN = main
            yield main
        finally:
            # SQLite keeps its file handle open on Windows until disposed.
            if _MAIN is not None:
                _MAIN.engine.dispose()
            _MAIN = None
            _REAL_V15_SUPERVISOR = None
            for name, value in original_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def _register_and_login(client, email: str) -> dict[str, str]:
    password = "test-password-for-isolated-jobs-smoke"
    registered = client.post("/register", json={"email": email, "password": password})
    assert registered.status_code == 200, registered.text
    logged_in = client.post("/login", data={"username": email, "password": password})
    assert logged_in.status_code == 200, logged_in.text
    return {"Authorization": f"Bearer {logged_in.json()['access_token']}"}


def _wait_for_terminal_job(client, job_id: str, headers: dict[str, str], timeout_s: float = 12) -> dict:
    deadline = time.monotonic() + timeout_s
    latest = None
    while time.monotonic() < deadline:
        response = client.get(f"/jobs/{job_id}", headers=headers)
        assert response.status_code == 200, response.text
        latest = response.json()
        if latest["status"] in {"done", "error", "cancelled"}:
            return latest
        time.sleep(0.05)
    raise AssertionError(f"job did not reach a terminal state: {latest}")


def _enqueue_v15_job(client, headers: dict[str, str], *, idea: str) -> str:
    response = client.post(
        "/jobs",
        headers=headers,
        json={"idea": idea, "provider": "auto", "deploy_to": "none"},
    )
    assert response.status_code == 200, response.text
    return response.json()["job_id"]


def _with_supervisor_wrapper(wrapper, test) -> None:
    """Inject a fixture while preserving the actual run_v15_supervisor code."""
    from app.jobs import v15_supervisor

    original = v15_supervisor.run_v15_supervisor
    v15_supervisor.run_v15_supervisor = wrapper
    try:
        test()
    finally:
        v15_supervisor.run_v15_supervisor = original


def test_authenticated_jobs_v15_success_persists_safe_stage_and_provider() -> None:
    from fastapi.testclient import TestClient
    from app.middleware.rate_limit import reset_all_rate_limits

    assert _MAIN is not None, "run this standalone smoke test directly"
    reset_all_rate_limits()
    _MAIN.JOB_STORE.clear()
    with TestClient(_MAIN.app) as client:
        headers = _register_and_login(client, "success@example.test")

        def exercise() -> None:
            job_id = _enqueue_v15_job(client, headers, idea="Build a test-only todo application")
            job = _wait_for_terminal_job(client, job_id, headers)
            assert job["status"] == "done", job
            assert job["active_stage"] == "verification"
            assert job["selected_provider"] == "cerebras"
            assert job["deadline_at"].endswith("Z"), job
            assert job["project_name"] == "smoke_safe_app"
            assert job["forge_score"] == 91
            assert job["backend_url"] == "https://api.example.test/v1"
            assert job["frontend_url"] == "https://app.example.test/"
            assert "must-not-cross-child-ipc" not in repr(job)

            foreign_headers = _register_and_login(client, "foreign@example.test")
            foreign = client.get(f"/jobs/{job_id}", headers=foreign_headers)
            assert foreign.status_code == 404, foreign.text
            assert foreign.json() == {"detail": "Job not found"}

        _with_supervisor_wrapper(_real_supervisor_with_success, exercise)


def test_authenticated_jobs_v15_deadline_is_terminal_and_releases_lease() -> None:
    from fastapi.testclient import TestClient
    from app.middleware.rate_limit import reset_all_rate_limits

    assert _MAIN is not None, "run this standalone smoke test directly"
    reset_all_rate_limits()
    _MAIN.JOB_STORE.clear()
    previous_deadline = os.environ.get("FORGE_V15_JOB_DEADLINE_S")
    os.environ["FORGE_V15_JOB_DEADLINE_S"] = "1"
    try:
        with TestClient(_MAIN.app) as client:
            headers = _register_and_login(client, "deadline@example.test")

            def exercise() -> None:
                job_id = _enqueue_v15_job(client, headers, idea="Build a deadline-only smoke app")
                job = _wait_for_terminal_job(client, job_id, headers)
                assert job["status"] == "error", job
                assert job["error"] == "deadline_exceeded at stage verification"
                assert job["active_stage"] == "verification"
                assert job["lease_expires_at"] is None

            _with_supervisor_wrapper(_real_supervisor_with_short_deadline, exercise)
    finally:
        if previous_deadline is None:
            os.environ.pop("FORGE_V15_JOB_DEADLINE_S", None)
        else:
            os.environ["FORGE_V15_JOB_DEADLINE_S"] = previous_deadline


def _run_isolated_suite() -> None:
    with _isolated_app_context():
        tests = [
            test_authenticated_jobs_v15_success_persists_safe_stage_and_provider,
            test_authenticated_jobs_v15_deadline_is_terminal_and_releases_lease,
        ]
        for test in tests:
            test()
            print(f"PASS {test.__name__}")
        print(f"{len(tests)}/{len(tests)} authenticated V15 jobs API smoke tests passed")


def _assert_import_is_side_effect_free() -> None:
    """Probe importing this file without executing its direct runner."""
    probe = (
        "import json, os, runpy; "
        "keys=('DATABASE_URL','SECRET_KEY','FORGE_PIPELINE_VERSION','FORGE_V15_JOB_DEADLINE_S'); "
        "before={k: os.environ.get(k) for k in keys}; "
        f"runpy.run_path({str(_SELF)!r}, run_name='forgeai_jobs_smoke_import_probe'); "
        "after={k: os.environ.get(k) for k in keys}; "
        "print(json.dumps({'before': before, 'after': after}))"
    )
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": "sqlite:///caller.db",
        "SECRET_KEY": "caller-secret-that-must-stay-unchanged",
        "FORGE_PIPELINE_VERSION": "caller-pipeline",
        "FORGE_V15_JOB_DEADLINE_S": "77",
    })
    result = subprocess.run(
        [sys.executable, "-c", probe], env=env, cwd=str(_BACKEND),
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["before"] == observed["after"], observed


def main_test() -> None:
    if os.environ.get(_CHILD_MARKER) == "1":
        _run_isolated_suite()
        return
    _assert_import_is_side_effect_free()
    env = os.environ.copy()
    env[_CHILD_MARKER] = "1"
    result = subprocess.run(
        [sys.executable, str(_SELF)], env=env, cwd=str(_BACKEND),
        check=False,
    )
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main_test()
