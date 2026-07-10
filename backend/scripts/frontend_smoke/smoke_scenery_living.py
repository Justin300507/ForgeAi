"""Smoke test for Living Scenery (Phase 1): ambient mist + light drift,
gated behind prefers-reduced-motion. No backend/LLM calls."""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://localhost:4173"
failures = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

def animation_name(page, selector):
    return page.evaluate(
        f"getComputedStyle(document.querySelector('{selector}')).animationName"
    )

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.route("**/me", lambda r: r.fulfill(status=200, content_type="application/json", body='{"email":"fixture@test.com"}'))
    page.route("**/jobs", lambda r: r.fulfill(status=200, content_type="application/json", body='{"jobs":[]}'))
    page.add_init_script("localStorage.setItem('token', 'fixture-token');")

    page.goto(BASE + "/dashboard", wait_until="networkidle")
    page.wait_for_timeout(400)

    check(".scenery-mist--a present", page.locator(".scenery-mist--a").count() == 1)
    check(".scenery-mist--b present", page.locator(".scenery-mist--b").count() == 1)
    check(".scenery-light present", page.locator(".scenery-light").count() == 1)

    check("mist--a animates by default", animation_name(page, ".scenery-mist--a") != "none")
    check("mist--b animates by default", animation_name(page, ".scenery-mist--b") != "none")
    check("light animates by default", animation_name(page, ".scenery-light") != "none")

    page.emulate_media(reduced_motion="reduce")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(400)

    check("mist--a suppressed under reduced-motion", animation_name(page, ".scenery-mist--a") == "none")
    check("mist--b suppressed under reduced-motion", animation_name(page, ".scenery-mist--b") == "none")
    check("light suppressed under reduced-motion", animation_name(page, ".scenery-light") == "none")

    real_errors = [e for e in console_errors if "Failed to load resource" not in e and "ERR_" not in e]
    check("no console errors", not real_errors)
    if real_errors:
        print("Console errors:", real_errors[:5])

    browser.close()

print("\n%d failure(s)" % len(failures))
sys.exit(1 if failures else 0)
