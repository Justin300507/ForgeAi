"""
V4 Playwright Workflow Testing

Goes beyond "page renders" to actually exercise the app. The CRUD/auth
portion (Register -> Login -> Create -> List -> Edit -> Delete -> ...) is
NOT reimplemented here -- it delegates to
app.runtime.user_journey_runner.run_user_journey(), the schema-aware,
architecture-aware implementation that introspects OpenAPI to build valid
request bodies and correctly picks the CRUD-capable entity.

This module used to carry its OWN hardcoded copy of that logic (bare
`/auth/register` + `/auth/login` paths, a username/password-only login
body, and a naive "first non-auth endpoint" entity guess with no
CRUD-capability check). That duplicate drifted from user_journey_runner.py
and produced deterministic false-negative CRUD failures: apps whose
architecture lists a POST-only `/seed` endpoint before the real resource
had "seed" picked as the CRUD entity (guaranteed 405 on the GET-list
check), and apps whose login schema needs `email` rather than `username`
silently "passed" login on a 422 without ever capturing a token, so every
subsequent authenticated call 401'd. Both reproduced on effectively every
canary run of 2 of the 3 fixed canary apps (blog_cms/crm and todo,
respectively) and fed the Integration score dimension + the repair loop
with a phantom bug. Reusing run_user_journey() eliminates that whole class
of harness drift by construction -- there is now exactly one
implementation of "run the CRUD journey" in the codebase.

What THIS module still owns, because it is genuinely Playwright-only work
user_journey_runner.py (API-only, no browser) cannot do: loading real
pages in a real browser and screenshotting them.
"""
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WorkflowResult:
    success: bool
    steps_passed: list = field(default_factory=list)
    steps_failed: list = field(default_factory=list)
    # Subset of steps_failed that came from the Playwright navigation checks
    # (not from the reused CRUD journey) -- the only failures that represent
    # genuinely new information the caller hasn't already diagnosed.
    nav_steps_failed: list = field(default_factory=list)
    screenshots: list = field(default_factory=list)
    duration: float = 0.0
    skipped: bool = False
    skip_reason: str = ""


def _journey_to_steps(journey: dict) -> tuple[list[str], list[str]]:
    """Translate a run_user_journey() result dict's `steps` into
    (passed, failed) name+detail strings for the workflow step lists."""
    passed, failed = [], []
    for step in journey.get("steps", []):
        name = step.get("name", "?")
        detail = step.get("detail", "")
        label = f"{name}: {detail}" if detail else name
        (passed if step.get("passed") else failed).append(label)
    return passed, failed


def _build_nav_steps(base_url: str) -> list[dict]:
    """Browser-only checks: does the page load without crashing."""
    return [
        {"name": "Load login page", "url": f"{base_url}/login"},
        {"name": "Navigate to app dashboard", "url": f"{base_url}/dashboard"},
    ]


def run_workflow_tests(
    project_path: str,
    architecture: dict | None = None,
    base_url: str = "http://127.0.0.1:5174",
    capture_screenshots: bool = True,
    journey: dict | None = None,
    backend_port: int = 8001,
) -> WorkflowResult:
    """
    Run E2E workflow checks: the CRUD/auth journey (reused from `journey` if
    the caller already ran one this generation, otherwise run fresh here)
    plus Playwright browser navigation checks.

    The backend must already be running on `backend_port`.
    The frontend dist/ is served on base_url.
    """
    import http.server
    import socketserver
    import threading

    t0 = time.time()

    try:
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
        import requests
        requests.get(f"http://127.0.0.1:{backend_port}/docs", timeout=3)
    except Exception:
        return WorkflowResult(
            success=False, skipped=True,
            skip_reason=f"Backend not running on port {backend_port}",
            duration=round(time.time() - t0, 2),
        )

    steps_passed: list[str] = []
    steps_failed: list[str] = []

    # ── CRUD/auth journey: reuse if already run this generation, else run it ──
    if journey and journey.get("steps") and not journey.get("skipped"):
        j_passed, j_failed = _journey_to_steps(journey)
    elif journey and journey.get("skipped"):
        j_passed, j_failed = [], []
    else:
        try:
            from app.runtime.user_journey_runner import run_user_journey
            fresh = run_user_journey(project_path, architecture, backend_port=backend_port)
            j_passed, j_failed = _journey_to_steps({
                "steps": [{"name": s.name, "passed": s.passed, "detail": s.detail}
                          for s in fresh.steps],
                "skipped": fresh.skipped,
            }) if not fresh.skipped else ([], [])
        except Exception as e:
            j_passed, j_failed = [], [f"CRUD journey crashed: {e}"]
    steps_passed += j_passed
    steps_failed += j_failed

    # ── Browser navigation checks (genuinely Playwright-only) ────────────────
    screenshots = []
    nav_failed: list[str] = []

    # Serve dist/
    from app.runtime.playwright_runner import _SPAHandler, _find_free_port
    port = _find_free_port(5174)
    handler = lambda *a, **kw: _SPAHandler(*a, directory=str(dist_dir), **kw)
    server = socketserver.TCPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    served_base_url = f"http://127.0.0.1:{port}"
    time.sleep(0.3)

    nav_steps = _build_nav_steps(served_base_url)

    try:
        with sync_playwright() as pw:
            # Low-memory flags for the shared 512MB Render instance -- see
            # playwright_runner.py's identical fix (Exp140 follow-up, live
            # OOM 2026-07-22) for the full rationale.
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--js-flags=--max-old-space-size=128",
                ],
            )
            context = browser.new_context()
            page = context.new_page()

            for step in nav_steps:
                name = step["name"]
                try:
                    page.goto(step["url"], wait_until="networkidle", timeout=10000)
                    if capture_screenshots:
                        png = page.screenshot()
                        import base64
                        screenshots.append({"step": name, "png_b64": base64.b64encode(png).decode()})
                    steps_passed.append(name)
                except Exception as e:
                    label = f"{name}: {e}"
                    steps_failed.append(label)
                    nav_failed.append(label)

            context.close()
            browser.close()

    except Exception as e:
        label = f"Browser error: {e}"
        steps_failed.append(label)
        nav_failed.append(label)
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
        nav_steps_failed=nav_failed,
        screenshots=screenshots,
        duration=round(time.time() - t0, 2),
    )
