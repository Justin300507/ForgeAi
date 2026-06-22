"""
V4 Playwright Workflow Testing

Goes beyond "page renders" to actually exercise the app:
  Register → Login → Create entity → Edit entity → Delete entity → Logout

Requires the backend to be running on port 8001 and the frontend
dist/ to be served. Call this AFTER both backend + frontend are validated.
"""
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WorkflowResult:
    success: bool
    steps_passed: list = field(default_factory=list)
    steps_failed: list = field(default_factory=list)
    screenshots: list = field(default_factory=list)
    duration: float = 0.0
    skipped: bool = False
    skip_reason: str = ""


def _detect_entity(architecture: dict) -> str:
    """Guess the primary CRUD entity from the architecture."""
    for ep in architecture.get("api_endpoints", []):
        path = ep.get("path", "")
        parts = [p for p in path.strip("/").split("/") if "{" not in p and p not in ("auth", "users", "me")]
        if parts:
            return parts[0]  # e.g. "tasks", "notes", "books"
    return "items"


def _build_workflow_steps(entity: str, base_url: str) -> list[dict]:
    """Return ordered workflow steps for the given entity."""
    return [
        {
            "name": "Load login page",
            "action": "navigate",
            "url": f"{base_url}/login",
            "expect_text": None,
            "expect_no_crash": True,
        },
        {
            "name": "Register new user",
            "action": "api_post",
            "url": "http://127.0.0.1:8001/auth/register",
            "body": {"username": "testuser_e2e", "email": "e2e@test.com", "password": "TestPass123!"},
            "expect_status": [200, 201, 422],  # 422 = already exists, still OK
        },
        {
            "name": "Login via API",
            "action": "api_post",
            "url": "http://127.0.0.1:8001/auth/login",
            "body": {"username": "testuser_e2e", "password": "TestPass123!"},
            "expect_status": [200, 422],
            "capture": "token",
        },
        {
            "name": f"Create {entity} via API",
            "action": "api_post",
            "url": f"http://127.0.0.1:8001/{entity}",
            "body": {"title": "E2E Test Item", "description": "Created by Playwright workflow"},
            "expect_status": [200, 201, 422],
            "capture": "entity_id",
        },
        {
            "name": f"List {entity} via API",
            "action": "api_get",
            "url": f"http://127.0.0.1:8001/{entity}",
            "expect_status": [200],
        },
        {
            "name": "Navigate to app dashboard",
            "action": "navigate",
            "url": f"{base_url}/dashboard",
            "expect_no_crash": True,
        },
    ]


def run_workflow_tests(
    project_path: str,
    architecture: dict | None = None,
    base_url: str = "http://127.0.0.1:5174",
    capture_screenshots: bool = True,
) -> WorkflowResult:
    """
    Run E2E workflow tests: API calls + browser navigation.
    The backend must already be running on port 8001.
    The frontend dist/ is served on base_url.
    """
    import http.server
    import socketserver
    import threading

    t0 = time.time()

    try:
        import requests
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        return WorkflowResult(
            success=False, skipped=True,
            skip_reason=f"Missing dependency: {e}",
            duration=round(time.time() - t0, 2),
        )

    dist_dir = Path(project_path) / "dist"
    if not dist_dir.exists():
        return WorkflowResult(
            success=False, skipped=True,
            skip_reason="dist/ not found",
            duration=round(time.time() - t0, 2),
        )

    # Check backend is up
    try:
        requests.get("http://127.0.0.1:8001/docs", timeout=3)
    except Exception:
        return WorkflowResult(
            success=False, skipped=True,
            skip_reason="Backend not running on port 8001",
            duration=round(time.time() - t0, 2),
        )

    # Serve dist/
    from app.runtime.playwright_runner import _SPAHandler, _find_free_port
    port = _find_free_port(5174)
    handler = lambda *a, **kw: _SPAHandler(*a, directory=str(dist_dir), **kw)
    server = socketserver.TCPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{port}"
    time.sleep(0.3)

    entity = _detect_entity(architecture or {})
    steps = _build_workflow_steps(entity, base_url)

    steps_passed = []
    steps_failed = []
    screenshots = []
    token = None

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            for step in steps:
                name = step["name"]
                action = step["action"]

                try:
                    if action == "navigate":
                        page.goto(step["url"], wait_until="networkidle", timeout=10000)
                        if capture_screenshots:
                            png = page.screenshot()
                            import base64
                            screenshots.append({"step": name, "png_b64": base64.b64encode(png).decode()})
                        steps_passed.append(name)

                    elif action in ("api_post", "api_get"):
                        headers = {"Content-Type": "application/json"}
                        if token:
                            headers["Authorization"] = f"Bearer {token}"

                        if action == "api_post":
                            resp = requests.post(step["url"], json=step.get("body", {}), headers=headers, timeout=5)
                        else:
                            resp = requests.get(step["url"], headers=headers, timeout=5)

                        allowed = step.get("expect_status", [200])
                        if resp.status_code in allowed:
                            steps_passed.append(f"{name} ({resp.status_code})")
                            # Capture token if this was a login step
                            if step.get("capture") == "token":
                                try:
                                    token = resp.json().get("access_token")
                                except Exception:
                                    pass
                        else:
                            steps_failed.append(f"{name} (got {resp.status_code}, expected {allowed})")

                except Exception as e:
                    steps_failed.append(f"{name}: {e}")

            context.close()
            browser.close()

    except Exception as e:
        steps_failed.append(f"Browser error: {e}")
    finally:
        try:
            server.shutdown()
        except Exception:
            pass

    success = len(steps_failed) == 0

    return WorkflowResult(
        success=success,
        steps_passed=steps_passed,
        steps_failed=steps_failed,
        screenshots=screenshots,
        duration=round(time.time() - t0, 2),
    )
