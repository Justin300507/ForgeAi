"""Verify the veil transition covers internal app navigation too
(Dashboard 'Forge It' -> /new, NavBar 'New App' -> /new), not just the
landing-page entry/exit. No backend, no LLM calls: /me and /jobs mocked."""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://localhost:4173"
failures = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

def veil_opacity_peaks_above(page, threshold, timeout_ms=1500):
    try:
        page.wait_for_function(
            f"() => parseFloat(getComputedStyle(document.querySelector('.forge-veil')).opacity) > {threshold}",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.route("**/me", lambda r: r.fulfill(status=200, content_type="application/json", body='{"email":"fixture@test.com"}'))
    page.route("**/jobs", lambda r: r.fulfill(status=200, content_type="application/json", body='{"jobs":[]}'))
    page.route("**/credentials/status", lambda r: r.fulfill(status=200, content_type="application/json", body='{}'))
    page.add_init_script("localStorage.setItem('token', 'fixture-token');")

    page.goto(BASE + "/dashboard", wait_until="networkidle")
    page.wait_for_timeout(300)
    check("dashboard loaded", "shall we forge" in page.inner_text("body").lower())

    page.fill("input[placeholder*='imagine']", "a recipe manager")
    page.click("button:has-text('Forge It')")
    check("dashboard Forge It triggers veil cover", veil_opacity_peaks_above(page, 0.3))
    page.wait_for_url(BASE + "/new", timeout=5000)
    check("lands on /new", page.url.endswith("/new"))
    page.wait_for_timeout(1200)
    veil_op2 = float(page.evaluate("getComputedStyle(document.querySelector('.forge-veil')).opacity"))
    check("veil lifts after landing on /new", veil_op2 < 0.2)

    page.goto(BASE + "/dashboard", wait_until="networkidle")
    page.wait_for_timeout(300)
    page.click("button:has-text('New App')")
    check("NavBar New App button triggers veil cover", veil_opacity_peaks_above(page, 0.3))
    page.wait_for_url(BASE + "/new", timeout=5000)
    check("NavBar New App lands on /new", page.url.endswith("/new"))

    browser.close()

print("\n%d failure(s)" % len(failures))
sys.exit(1 if failures else 0)
