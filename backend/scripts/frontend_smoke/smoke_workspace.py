"""Smoke test for the ProjectDetail 2-pane workspace redesign.
No backend, no LLM calls: /jobs/:id is intercepted with a fixture, and
localStorage is seeded with a fake token so the PrivateRoute lets us in."""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://localhost:4173"
failures = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

FAKE_JOB = {
    "id": "fixture-job-1",
    "status": "done",
    "idea": "A habit tracker with streaks, badges, dark mode, and weekly reports",
    "provider": "gemini",
    "forge_score": 90.4,
    "logs": [f"line {i}: some pipeline output" for i in range(80)],
    "frontend_url": "https://example.pages.dev",
    "backend_url": "https://example.up.railway.app",
    "github_url": "https://github.com/example/example",
    "zip_path": "/tmp/example.zip",
    "error": None,
}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

    page.route("**/me", lambda r: r.fulfill(status=200, content_type="application/json", body='{"email":"fixture@test.com"}'))
    page.route("**/jobs/fixture-job-1", lambda r: r.fulfill(status=200, content_type="application/json", body=__import__("json").dumps(FAKE_JOB)))

    page.add_init_script("localStorage.setItem('token', 'fixture-token');")
    page.goto(BASE + "/projects/fixture-job-1", wait_until="networkidle")
    page.wait_for_timeout(500)

    body = page.inner_text("body")
    check("job idea rendered", "habit tracker" in body)
    check("forge score rendered", "90.4" in body)

    panes = page.locator(".workspace-shell")
    check("workspace-shell panes present (>=3: stepper/info/log)", panes.count() >= 3)

    left_box = page.locator("div.lg\\:col-span-2").first.bounding_box()
    right_box = page.locator("div.lg\\:col-span-3").first.bounding_box()
    check("left pane present", left_box is not None)
    check("right pane present", right_box is not None)
    if left_box and right_box:
        check("right pane wider than left (60/40 split)", right_box["width"] > left_box["width"])
        check("panes side-by-side (right starts after left ends)", right_box["x"] >= left_box["x"] + left_box["width"] - 5)

    doc_height = page.evaluate("document.documentElement.scrollHeight")
    check(f"page height bounded near viewport (doc={doc_height}px)", doc_height < 1000)

    log_scroll_h = page.evaluate("document.querySelector('.pane-scroll').scrollHeight")
    log_client_h = page.evaluate("document.querySelector('.pane-scroll').clientHeight")
    check("log pane is independently scrollable (content taller than viewport)", log_scroll_h > log_client_h)

    check("stepper renders stage labels", "Complete" in body and "Planning" in body)

    real_errors = [e for e in console_errors if "Failed to load resource" not in e and "ERR_" not in e]
    check("no console errors", not real_errors)
    if real_errors:
        print("Console errors:", real_errors[:5])

    browser.close()

print("\n%d failure(s)" % len(failures))
sys.exit(1 if failures else 0)
