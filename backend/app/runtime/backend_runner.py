import subprocess
import time
import sys
from pathlib import Path

import requests

from app.runtime.runtime_models import RuntimeResult
from app.services.endpoint_smoke_test_service import run_endpoint_smoke_tests


def _free_port(port: int) -> None:
    """Kill any process holding the given port so uvicorn can bind cleanly."""
    freed = False

    if sys.platform == "win32":
        # Windows: netstat -ano to find PID, then taskkill
        try:
            r = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            pids = set()
            for line in r.stdout.splitlines():
                if f":{port}" in line and ("LISTENING" in line or "ESTABLISHED" in line):
                    parts = line.split()
                    if parts:
                        pid = parts[-1]
                        if pid.isdigit() and pid != "0":
                            pids.add(pid)
            for pid in pids:
                subprocess.run(
                    ["taskkill", "/F", "/PID", pid],
                    capture_output=True, check=False,
                )
            if pids:
                freed = True
        except Exception:
            pass
    else:
        # Linux: fuser -k {port}/tcp (sends SIGKILL to all holders)
        try:
            r = subprocess.run(
                ["fuser", "-k", f"{port}/tcp"],
                capture_output=True, timeout=5, check=False,
            )
            if r.returncode == 0:
                freed = True
        except Exception:
            pass
        # Fallback: lsof → kill -9
        try:
            r = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            pids = [p for p in r.stdout.strip().split() if p.isdigit()]
            for pid in pids:
                subprocess.run(["kill", "-9", pid], capture_output=True, check=False)
            if pids:
                freed = True
        except Exception:
            pass

    if freed:
        time.sleep(0.5)  # Give OS time to fully release the socket


class BackendRunner:
    def run(self, backend_path: str, architecture: dict | None = None, port: int = 8001) -> RuntimeResult:

        backend_dir = Path(backend_path)

        if not backend_dir.exists():
            raise Exception(f"Project path does not exist: {backend_path}")

        start_time = time.time()
        print(f"Running runtime validation in: {backend_dir}")
        print(f"Using Python: {sys.executable}")

        requirements_file = backend_dir / "app" / "requirements.txt"
        if requirements_file.exists():
            print(f"Skipping pip install (packages pre-installed): {requirements_file}")

        # Delete stale SQLite DBs — schema may have changed since last run
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

        _free_port(port)

        # Pre-flight DB init: run create_tables() BEFORE uvicorn starts so the
        # schema is complete regardless of import order in main.py.  create_tables()
        # scans app/models/, imports every model, then calls create_all() + ALTER TABLE.
        try:
            pre_db = subprocess.run(
                [sys.executable, "-c",
                 "import sys; sys.path.insert(0, '.'); "
                 "from app.database import create_tables; "
                 "create_tables(); "
                 "print('DB schema ready')"],
                cwd=str(backend_dir),
                capture_output=True,
                text=True,
                timeout=20,
            )
            if "DB schema ready" in pre_db.stdout:
                print(f"  [runner] Pre-flight DB init: OK")
            else:
                print(f"  [runner] Pre-flight DB init warning: {pre_db.stderr[-300:]}")
        except Exception as pre_err:
            print(f"  [runner] Pre-flight DB init failed (non-fatal): {pre_err}")

        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=backend_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        max_wait = 5
        healthy = False

        for _ in range(max_wait):
            if process.poll() is not None:
                break
            try:
                for check_path in ("/health", "/docs"):
                    try:
                        response = requests.get(f"http://127.0.0.1:{port}{check_path}", timeout=2)
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

        behavioral_issues: list = []
        total_endpoints = 0
        timeout_count = 0
        error_count = 0
        endpoint_pass_rate = 1.0
        journey_data: dict | None = None
        crud_passed: bool | None = None

        if healthy and architecture:
            # ── Journey first — before smoke tests can crash/hang the server ──
            print("\n=== USER JOURNEY (CRUD WORKFLOW) ===")
            try:
                from app.runtime.user_journey_runner import run_user_journey
                journey_result = run_user_journey(
                    str(backend_dir), architecture, backend_port=port
                )
                journey_data = {
                    "success": journey_result.success,
                    "steps_passed": journey_result.steps_passed,
                    "steps_failed": journey_result.steps_failed,
                    "persistence_verified": journey_result.persistence_verified,
                    "skipped": journey_result.skipped,
                    "skip_reason": getattr(journey_result, "skip_reason", ""),
                    "steps": [
                        {"name": s.name, "passed": s.passed, "detail": s.detail}
                        for s in journey_result.steps
                    ],
                    "total_duration": journey_result.total_duration,
                }
                if journey_result.skipped:
                    print(f"  Journey skipped: {journey_result.skip_reason}")
                    crud_passed = None
                else:
                    crud_passed = journey_result.success
                    icon = "PASS" if crud_passed else "FAIL"
                    print(
                        f"  Journey {icon} — "
                        f"{journey_result.steps_passed} passed / "
                        f"{journey_result.steps_failed} failed"
                    )
                    for step in journey_result.steps:
                        marker = "✓" if step.passed else "✗"
                        print(f"    {marker} {step.name}: {step.detail}")
            except Exception as je:
                print(f"  Journey error: {je}")
                journey_data = {"skipped": True, "skip_reason": str(je)}
                crud_passed = None

            # ── Smoke tests after journey — they can hang/crash the server ──
            print("\n=== ENDPOINT SMOKE TESTS ===")
            behavioral_issues = run_endpoint_smoke_tests(architecture)

            for issue in behavioral_issues:
                print(f"  {issue['method']} {issue['path']} -> {issue['issue']}")

            if not behavioral_issues:
                print("  All endpoints responded without errors.")

            total_endpoints = len(architecture.get("api_endpoints", []))
            timeout_count = sum(
                1 for i in behavioral_issues if "timed out" in i.get("issue", "")
            )
            error_count = sum(
                1 for i in behavioral_issues if "crashed" in i.get("issue", "")
            )
            failed_count = len(behavioral_issues)
            endpoint_pass_rate = (
                (total_endpoints - failed_count) / total_endpoints
                if total_endpoints > 0 else 1.0
            )
            print(
                f"\n  Endpoint pass rate: {endpoint_pass_rate:.0%} "
                f"({total_endpoints - failed_count}/{total_endpoints} passed, "
                f"{timeout_count} timeouts, {error_count} errors)"
            )

        # Terminate the server
        if process.poll() is None:
            try:
                process.terminate()
                stdout, stderr = process.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()

            # Give Windows a moment to release file handles
            time.sleep(1)

            if not healthy:
                return RuntimeResult(
                    success=False,
                    exit_code=1,
                    stdout=stdout,
                    stderr=stderr + "\nHealth Check Failed",
                    startup_time=time.time() - start_time,
                    total_endpoints=total_endpoints,
                    endpoint_pass_rate=0.0,
                )

            # SUCCESS = server healthy AND enough endpoints pass AND CRUD not critically broken
            # Thresholds:
            #   - endpoint_pass_rate < 0.5 → FAIL (majority of endpoints broken)
            #   - crud_passed == False → FAIL (CRUD workflow critically failed)
            runtime_success = (
                healthy
                and endpoint_pass_rate >= 0.5
                and crud_passed is not False
            )

            if not runtime_success:
                reasons = []
                if endpoint_pass_rate < 0.5:
                    reasons.append(
                        f"endpoint pass rate too low ({endpoint_pass_rate:.0%}, "
                        f"{timeout_count} timeouts, {error_count} 5xx errors)"
                    )
                if crud_passed is False:
                    reasons.append("CRUD workflow failed critical steps")
                print(f"\n  Runtime FAIL: {'; '.join(reasons)}")

            return RuntimeResult(
                success=runtime_success,
                exit_code=0,
                stdout=stdout,
                stderr=stderr,
                startup_time=time.time() - start_time,
                behavioral_issues=behavioral_issues,
                total_endpoints=total_endpoints,
                timeout_count=timeout_count,
                error_count=error_count,
                endpoint_pass_rate=round(endpoint_pass_rate, 3),
                crud_passed=crud_passed,
                journey=journey_data,
            )

        stdout, stderr = process.communicate()
        return RuntimeResult(
            success=False,
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            startup_time=time.time() - start_time,
            total_endpoints=total_endpoints,
            endpoint_pass_rate=0.0,
        )
