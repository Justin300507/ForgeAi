"""Verify Dashboard's content redesign: stat cards gone, Templates chips
present and fill the prompt, project list shows quiet status (dot/grade)
with Fix/Delete hidden until hover. No backend/LLM calls."""
import sys, json
from playwright.sync_api import sync_playwright

BASE = "http://localhost:4173"
failures = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

JOBS = {
    "jobs": [
        {"id": "1", "status": "done", "idea": "CRM with contacts", "forge_score": 90, "backend_url": "https://x.up.railway.app", "created_at": "2026-07-10T09:00:00"},
        {"id": "2", "status": "running", "idea": "Habit tracker", "forge_score": None, "backend_url": None, "created_at": "2026-07-10T09:30:00"},
        {"id": "3", "status": "error", "idea": "Inventory system", "forge_score": None, "backend_url": None, "created_at": "2026-07-08T09:00:00"},
    ]
}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.route("**/me", lambda r: r.fulfill(status=200, content_type="application/json", body='{"email":"jerrythomas05@test.com"}'))
    page.route("**/jobs", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(JOBS)))
    page.add_init_script("localStorage.setItem('token', 'fixture-token');")

    page.goto(BASE + "/dashboard", wait_until="networkidle")
    page.wait_for_timeout(500)

    body = page.inner_text("body")
    check("no 'Apps built' stat card", "Apps built" not in body and "APPS BUILT" not in body.upper())
    check("no 'Avg score' stat card", "Avg score" not in body and "AVG SCORE" not in body.upper())

    chip_count = page.locator("button:has-text('Habit Tracker')").count()
    check("Templates chip present ('Habit Tracker')", chip_count >= 1)
    page.click("button:has-text('Habit Tracker')")
    idea_value = page.input_value("input[placeholder*='imagine']")
    check("clicking a template fills the prompt", "habit tracker" in idea_value.lower())

    check("done job shows score inline", "90" in body and "A" in body)
    check("running job shows plain status word", "running" in body.lower())

    delete_btn = page.locator("button[aria-label='Delete project']").first
    check("delete button exists", delete_btn.count() == 1)
    rest_opacity = float(page.evaluate(
        "getComputedStyle(document.querySelectorAll(\"button[aria-label='Delete project']\")[0]).opacity"
    ))
    check("delete button hidden at rest (opacity 0)", rest_opacity == 0)

    row = page.locator(".glass-panel.cursor-pointer").first
    row.hover()
    page.wait_for_timeout(300)
    hover_opacity = float(page.evaluate(
        "getComputedStyle(document.querySelectorAll(\"button[aria-label='Delete project']\")[0]).opacity"
    ))
    check("delete button visible on row hover (opacity 1)", hover_opacity > 0.9)

    real_errors = [e for e in console_errors if "Failed to load resource" not in e and "ERR_" not in e]
    check("no console errors", not real_errors)
    if real_errors:
        print("Console errors:", real_errors[:5])

    browser.close()

print("\n%d failure(s)" % len(failures))
sys.exit(1 if failures else 0)
