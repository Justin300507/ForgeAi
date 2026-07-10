"""Smoke test for the reworked landing page + veil transition."""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://localhost:4173"
failures = []
console_errors = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.goto(BASE + "/", wait_until="networkidle")

    body = page.inner_text("body")
    check("no scene option labels", all(x not in body for x in ["Golden Hour", "Still Water", "Deep Woods", "Quiet Dawn"]))
    check("no 12,000 badge/stats", "12,000" not in body)
    check("no dead nav links", all(x not in body for x in ["How It Works", "Features", "Pricing", "Community"]))
    check("no bottom stats bar", "3-Min Average Build" not in body)
    check("hero title present", "Software Born from" in body)
    check("Forge It CTA present", page.locator("button:has-text('Forge It')").count() == 1)
    check("Get Started CTA present", page.locator("button:has-text('Get Started')").count() == 1)

    def active_index():
        vids = page.locator("video")
        for i in range(vids.count()):
            if "opacity-100" in (vids.nth(i).get_attribute("class") or ""):
                return i
        return -1
    first = active_index()
    page.wait_for_timeout(3600)
    second = active_index()
    check(f"scenes auto-advance ({first} -> {second})", second != first and second != -1)

    page.click("button:has-text('Get Started')")
    page.wait_for_timeout(150)
    zoom = page.evaluate("getComputedStyle(document.querySelector('.scene-zoom')).transform")
    check("scene zooms on exit (matrix scale > 1)", zoom.startswith("matrix(") and float(zoom.split("(")[1].split(",")[0]) > 1.05)
    try:
        page.wait_for_function(
            "() => parseFloat(getComputedStyle(document.querySelector('.forge-veil')).opacity) > 0.3",
            timeout=1500,
        )
        veil_covered = True
    except Exception:
        veil_covered = False
    check("veil covers on CTA click", veil_covered)
    page.wait_for_url(BASE + "/register", timeout=5000)
    check("lands on /register", page.url.endswith("/register"))
    try:
        page.wait_for_function(
            "() => parseFloat(getComputedStyle(document.querySelector('.forge-veil')).opacity) < 0.2",
            timeout=2000,
        )
        veil_lifted = True
    except Exception:
        veil_lifted = False
    check("veil lifts after navigation", veil_lifted)
    check("register page rendered", "Create your account" in page.inner_text("body"))

    real_errors = [e for e in console_errors if "Failed to load resource" not in e and "ERR_" not in e]
    check("no console errors", not real_errors)
    if real_errors:
        print("Console errors:", real_errors[:5])

    browser.close()

print("\n%d failure(s)" % len(failures))
sys.exit(1 if failures else 0)
