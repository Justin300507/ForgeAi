"""Verify the shared <Scenery /> blurred backdrop is present and wired on
every authenticated page, and that .app-shell no longer paints its own
background (fully delegated to Scenery). No backend/LLM calls."""
import sys, json
from playwright.sync_api import sync_playwright

BASE = "http://localhost:4173"
failures = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

FAKE_JOB = {
    "id": "fixture-job-1", "status": "done", "idea": "todo app", "provider": "gemini",
    "forge_score": 90, "logs": [], "frontend_url": None, "backend_url": None,
    "github_url": None, "zip_path": None, "error": None,
}

PAGES = ["/dashboard", "/new", "/projects/fixture-job-1", "/settings"]

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.route("**/me", lambda r: r.fulfill(status=200, content_type="application/json", body='{"email":"fixture@test.com"}'))
    page.route("**/jobs/fixture-job-1", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(FAKE_JOB)))
    page.route("**/jobs", lambda r: r.fulfill(status=200, content_type="application/json", body='{"jobs":[]}'))
    page.route("**/credentials/status", lambda r: r.fulfill(status=200, content_type="application/json", body='{}'))
    page.route("**/credentials", lambda r: r.fulfill(status=200, content_type="application/json", body='{}'))
    page.add_init_script("localStorage.setItem('token', 'fixture-token');")

    for path in PAGES:
        page.goto(BASE + path, wait_until="networkidle")
        page.wait_for_timeout(400)

        check(f"{path}: scenery-layer present", page.locator(".scenery-layer").count() == 1)
        bg_image = page.evaluate("getComputedStyle(document.querySelector('.scenery-image')).backgroundImage")
        check(f"{path}: scenery-image has a real background-image", "none" not in bg_image and "scenery-golden-hour" in bg_image)
        blur_filter = page.evaluate("getComputedStyle(document.querySelector('.scenery-image')).filter")
        check(f"{path}: scenery-image is blurred", "blur" in blur_filter)
        shell_bg = page.evaluate("getComputedStyle(document.querySelector('.app-shell')).backgroundImage")
        check(f"{path}: .app-shell no longer paints its own background", shell_bg == "none")

    real_errors = [e for e in console_errors if "Failed to load resource" not in e and "ERR_" not in e]
    check("no console errors across all pages", not real_errors)
    if real_errors:
        print("Console errors:", real_errors[:5])

    browser.close()

print("\n%d failure(s)" % len(failures))
sys.exit(1 if failures else 0)
