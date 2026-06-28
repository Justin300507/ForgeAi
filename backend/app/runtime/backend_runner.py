import subprocess
import time
import sys
from pathlib import Path

import requests

from app.runtime.runtime_models import RuntimeResult
from app.services.endpoint_smoke_test_service import run_endpoint_smoke_tests


class BackendRunner:
    def run(self, backend_path: str, architecture: dict | None = None) -> RuntimeResult:

        backend_dir = Path(backend_path)

        if not backend_dir.exists():

            raise Exception(
                f"Project path does not exist: {backend_path}"
            )

        start_time = time.time()

        print(
            f"Running runtime validation in: {backend_dir}"
        )

        print(
            f"Using Python: {sys.executable}"
        )

        requirements_file = (
            backend_dir / "app" / "requirements.txt"
        )

        # Skip pip install — generated projects use the same stack already
        # installed in the container (FastAPI, SQLAlchemy, pydantic, etc.)
        if requirements_file.exists():
            print(f"Skipping pip install (packages pre-installed): {requirements_file}")

        # Delete stale SQLite DBs — schema may have changed since last run
        # (Base.metadata.create_all won't ALTER existing tables, just skips them)
        for stale_db in backend_dir.rglob("*.db"):
            try:
                stale_db.unlink()
                print(f"  [runner] Deleted stale DB: {stale_db.name}")
            except Exception:
                pass
        for stale_db in backend_dir.rglob("*.sqlite3"):
            try:
                stale_db.unlink()
            except Exception:
                pass

        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8001"
            ],
            cwd=backend_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        max_wait = 5
        healthy = False

        for _ in range(max_wait):

            if process.poll() is not None:
                break

            try:
                # Try /health first (deterministically injected), fall back to /docs
                for check_path in ("/health", "/docs"):
                    try:
                        response = requests.get(
                            f"http://127.0.0.1:8001{check_path}",
                            timeout=2
                        )
                        if response.status_code < 500:
                            healthy = True
                            print(f"Health Check {check_path}: {response.status_code}")
                            break
                    except Exception:
                        continue
                if healthy:
                    break

            except Exception:
                pass

            time.sleep(1)

        behavioral_issues = []

        if healthy and architecture:

            print("\n=== ENDPOINT SMOKE TESTS ===")
            behavioral_issues = run_endpoint_smoke_tests(architecture)

            for issue in behavioral_issues:
                print(f"  {issue['method']} {issue['path']} -> {issue['issue']}")

            if not behavioral_issues:
                print("  No 500-level errors found.")

        if process.poll() is None:

            try:

                process.terminate()

                stdout, stderr = process.communicate(
                    timeout=8
                )

            except subprocess.TimeoutExpired:

                process.kill()

                stdout, stderr = process.communicate()

            # Give Windows a moment to release file handles (e.g. test.db)
            time.sleep(1)

            if not healthy:

                return RuntimeResult(
                    success=False,
                    exit_code=1,
                    stdout=stdout,
                    stderr=(
                        stderr
                        + "\nHealth Check Failed"
                    ),
                    startup_time=time.time() - start_time
                )

            return RuntimeResult(
                success=True,
                exit_code=0,
                stdout=stdout,
                stderr=stderr,
                startup_time=time.time() - start_time,
                behavioral_issues=behavioral_issues
            )

        stdout, stderr = process.communicate()

        return RuntimeResult(
            success=False,
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            startup_time=time.time() - start_time
        )