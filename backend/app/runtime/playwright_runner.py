"""
Playwright browser automation — serves the built React dist/ and verifies the
UI renders without JS errors across the main routes.

Requires: pip install playwright && python -m playwright install chromium
"""
import http.server
import os
import socketserver
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PlaywrightResult:
    success: bool
    pages_checked: int = 0
    console_errors: list = field(default_factory=list)
    blank_pages: list = field(default_factory=list)
    crashed_pages: list = field(default_factory=list)
    screenshots: list = field(default_factory=list)  # base64 PNGs if captured
    skipped: bool = False
    skip_reason: str = ""
    duration: float = 0.0


def _find_free_port(start: int = 5173) -> int:
    import socket
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free port found in range")


class _SilentHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def end_headers(self):
        # Serve index.html for all unknown paths (SPA routing)
        super().end_headers()


class _SPAHandler(_SilentHandler):
    """Serve index.html for any path that doesn't match a real file."""
    def do_GET(self):
        p = Path(self.directory) / self.path.lstrip("/").split("?")[0]
        if not p.exists() or p.is_dir():
            self.path = "/index.html"
        super().do_GET()


def _start_static_server(dist_dir: str, port: int) -> socketserver.TCPServer:
    handler = lambda *a, **kw: _SPAHandler(*a, directory=dist_dir, **kw)
    server = socketserver.TCPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _default_routes(architecture: dict | None) -> list[str]:
    """Derive a small set of meaningful frontend routes to test."""
    # Start with auth pages — these exist in almost every app and always render
    routes = ["/login", "/register"]

    if architecture:
        seen_prefixes = set(["auth", "users"])
        mapping = {
            "tasks": "/tasks",
            "notes": "/notes",
            "posts": "/posts",
            "books": "/books",
            "products": "/products",
            "habits": "/habits",
            "workouts": "/workouts",
            "events": "/events",
            "tickets": "/tickets",
            "invoices": "/invoices",
        }
        for ep in architecture.get("api_endpoints", []):
            path = ep.get("path", "").split("?")[0]
            prefix = path.strip("/").split("/")[0] if path else ""
            if prefix in seen_prefixes or prefix not in mapping:
                continue
            seen_prefixes.add(prefix)
            routes.append(mapping[prefix])

    return routes[:5]  # cap to avoid slow test runs


def run_playwright_tests(
    project_path: str,
    architecture: dict | None = None,
    capture_screenshots: bool = False,
) -> PlaywrightResult:
    """
    Run headless Chromium against the built dist/ folder.
    Returns a PlaywrightResult regardless of errors — never raises.
    """
    t0 = time.time()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return PlaywrightResult(
            success=False, skipped=True,
            skip_reason="playwright not installed",
            duration=round(time.time() - t0, 2),
        )

    dist_dir = Path(project_path) / "dist"
    if not dist_dir.exists():
        return PlaywrightResult(
            success=False, skipped=True,
            skip_reason="dist/ not found — run vite build first",
            duration=round(time.time() - t0, 2),
        )

    try:
        port = _find_free_port()
        server = _start_static_server(str(dist_dir), port)
        base_url = f"http://127.0.0.1:{port}"
        time.sleep(0.3)  # let server start
    except Exception as e:
        return PlaywrightResult(
            success=False, skipped=True,
            skip_reason=f"Static server failed: {e}",
            duration=round(time.time() - t0, 2),
        )

    console_errors = []
    blank_pages = []
    crashed_pages = []
    screenshots = []
    pages_checked = 0

    try:
        routes = _default_routes(architecture)
        with sync_playwright() as pw:
            # Low-memory flags for the shared 512MB Render instance (Exp140
            # follow-up, live OOM 2026-07-22): an unflagged headless
            # Chromium routinely holds 150-300MB+ RSS on its own, on top of
            # the parent server, the job's forked child, and the frontend's
            # own npm/vite build (already capped via
            # FORGE_FRONTEND_BUILD_HEAP_MB) all sharing the same container.
            # --disable-dev-shm-usage avoids /dev/shm exhaustion in small
            # containers; --disable-gpu skips GPU compositing that headless
            # mode doesn't use anyway; --js-flags caps the V8 heap available
            # to the PAGE's own JS (generated CRUD apps are simple SPAs and
            # do not need more).
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--js-flags=--max-old-space-size=128",
                ],
            )
            context = browser.new_context()

            for route in routes:
                page = context.new_page()
                page_errors = []

                page.on("console", lambda msg: page_errors.append(msg.text)
                        if msg.type == "error" else None)
                page.on("pageerror", lambda err: page_errors.append(str(err)))

                try:
                    page.goto(base_url + route, wait_until="networkidle", timeout=10000)
                    pages_checked += 1

                    # Blank = React never mounted (#root has no children)
                    # Stubs that render minimal text still count as mounted
                    root_children = page.locator("#root > *").count()
                    if root_children == 0:
                        blank_pages.append(route)

                    if capture_screenshots:
                        png = page.screenshot(full_page=True)
                        import base64
                        screenshots.append({
                            "route": route,
                            "png_b64": base64.b64encode(png).decode(),
                        })

                    if page_errors:
                        console_errors.extend([f"{route}: {e}" for e in page_errors])

                except Exception as nav_err:
                    crashed_pages.append(f"{route}: {nav_err}")

                page.close()

            context.close()
            browser.close()

    except Exception as e:
        return PlaywrightResult(
            success=False,
            pages_checked=pages_checked,
            console_errors=console_errors,
            blank_pages=blank_pages,
            crashed_pages=crashed_pages + [f"Browser error: {e}"],
            duration=round(time.time() - t0, 2),
        )
    finally:
        try:
            server.shutdown()
        except Exception:
            pass

    success = (
        len(blank_pages) == 0
        and len(crashed_pages) == 0
        and len([e for e in console_errors if "chunk" not in e.lower()]) == 0
    )

    return PlaywrightResult(
        success=success,
        pages_checked=pages_checked,
        console_errors=console_errors,
        blank_pages=blank_pages,
        crashed_pages=crashed_pages,
        screenshots=screenshots,
        duration=round(time.time() - t0, 2),
    )
