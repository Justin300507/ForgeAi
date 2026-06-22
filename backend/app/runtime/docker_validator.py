"""
V5.1 Docker Deployment Validation

Verifies every generated app can actually deploy:
  Dockerfile generation → docker build → docker run → health check → teardown

Gracefully skips if Docker is not installed.
"""
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


_DOCKERFILE_TEMPLATE = """\
FROM python:3.11-slim

WORKDIR /app

COPY app/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
"""


@dataclass
class DockerResult:
    success: bool
    build_passed: bool = False
    run_passed: bool = False
    health_passed: bool = False
    duration: float = 0.0
    error: str = ""
    skipped: bool = False
    skip_reason: str = ""


def _find_free_port(start: int = 18000) -> int:
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free port found")


def _run(cmd: list[str], cwd: str, timeout: int) -> tuple[int, str, str]:
    result = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True,
        timeout=timeout, errors="replace",
    )
    return result.returncode, result.stdout, result.stderr


def _ensure_dockerfile(project_path: Path) -> None:
    dockerfile = project_path / "Dockerfile"
    if not dockerfile.exists():
        dockerfile.write_text(_DOCKERFILE_TEMPLATE, encoding="utf-8")


def _health_check(port: int, timeout: int = 10) -> bool:
    deadline = time.time() + timeout
    import urllib.request
    import urllib.error
    while time.time() < deadline:
        for path in ("/health", "/docs"):
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}{path}", timeout=2
                )
                return True
            except Exception:
                pass
        time.sleep(0.5)
    return False


def run_docker_validation(project_path: str) -> DockerResult:
    """
    Build and run the generated project in Docker, health-check it, then tear down.
    Returns DockerResult with skipped=True if Docker is unavailable.
    """
    t0 = time.time()

    if not shutil.which("docker"):
        return DockerResult(
            success=False, skipped=True,
            skip_reason="Docker not installed",
            duration=0.0,
        )

    project_path = Path(project_path)
    project_name = project_path.name.lower().replace(" ", "_").replace("-", "_")
    image_tag = f"forgeai-test-{project_name}"
    container_name = f"forgeai-run-{project_name}"
    port = _find_free_port()

    try:
        _ensure_dockerfile(project_path)
    except Exception as e:
        return DockerResult(
            success=False, skipped=True,
            skip_reason=f"Dockerfile generation failed: {e}",
            duration=round(time.time() - t0, 2),
        )

    build_passed = False
    run_passed = False
    health_passed = False
    error_msg = ""

    # ── Build ────────────────────────────────────────────────────────────
    try:
        print(f"\n[Docker] Building {image_tag} ...")
        code, out, err = _run(
            ["docker", "build", "-t", image_tag, "."],
            cwd=str(project_path),
            timeout=120,
        )
        build_passed = (code == 0)
        if not build_passed:
            error_msg = (err or out)[:500]
            print(f"[Docker] Build failed: {error_msg[:200]}")
    except subprocess.TimeoutExpired:
        error_msg = "docker build timed out after 120s"
        print(f"[Docker] {error_msg}")
    except Exception as e:
        error_msg = str(e)
        print(f"[Docker] Build error: {e}")

    # ── Run ──────────────────────────────────────────────────────────────
    if build_passed:
        try:
            print(f"[Docker] Running container on port {port} ...")
            code, out, err = _run(
                ["docker", "run", "-d",
                 "-p", f"{port}:8000",
                 "--name", container_name,
                 image_tag],
                cwd=str(project_path),
                timeout=30,
            )
            run_passed = (code == 0)
            if not run_passed:
                error_msg = (err or out)[:500]
                print(f"[Docker] Run failed: {error_msg[:200]}")
        except Exception as e:
            error_msg = str(e)
            print(f"[Docker] Run error: {e}")

    # ── Health check ─────────────────────────────────────────────────────
    if run_passed:
        try:
            health_passed = _health_check(port, timeout=10)
            print(f"[Docker] Health check: {'PASS' if health_passed else 'FAIL'}")
        except Exception as e:
            print(f"[Docker] Health check error: {e}")

    # ── Teardown (always) ─────────────────────────────────────────────────
    for cmd in (
        ["docker", "stop", container_name],
        ["docker", "rm", container_name],
        ["docker", "rmi", "-f", image_tag],
    ):
        try:
            subprocess.run(cmd, capture_output=True, timeout=30)
        except Exception:
            pass

    success = build_passed and run_passed and health_passed
    return DockerResult(
        success=success,
        build_passed=build_passed,
        run_passed=run_passed,
        health_passed=health_passed,
        duration=round(time.time() - t0, 2),
        error=error_msg,
    )
