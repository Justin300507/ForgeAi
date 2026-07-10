"""Smoke test for the NewProject page 'alive' pass: AI Pipeline preview
card, textarea focus glow, chip hover, deployment card grid, model
selector icons, and the Forge-press wow moment (ignition + scenery boost).
No backend/LLM calls -- /me, /credentials/status, /jobs (GET+POST) mocked."""
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

def jobs_handler(route):
    if route.request.method == "POST":
        # No artificial delay needed: NewProject.jsx sets igniting/boost
        # state synchronously, BEFORE awaiting this call, and doesn't clear
        # it on success (the page unmounts on navigate) -- so the ignition
        # classes are already present the instant click() returns, and
        # stay present through the whole 550ms veil-cover window regardless
        # of API latency. (An earlier version of this test delayed the
        # response via time.sleep() in this handler -- don't: that blocks
        # Playwright's whole sync driver process, corrupting every
        # timing-sensitive assertion below.)
        route.fulfill(status=200, content_type="application/json", body='{"job_id":"fixture-job-1"}')
    else:
        route.fulfill(status=200, content_type="application/json", body='{"jobs":[]}')

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.route("**/me", lambda r: r.fulfill(status=200, content_type="application/json", body='{"email":"fixture@test.com"}'))
    page.route("**/credentials/status", lambda r: r.fulfill(status=200, content_type="application/json", body='{}'))
    page.route("**/jobs", jobs_handler)
    page.add_init_script("localStorage.setItem('token', 'fixture-token');")

    page.goto(BASE + "/new", wait_until="networkidle")
    page.wait_for_timeout(400)

    # AI Pipeline preview card
    check("'AI Pipeline' heading present", "AI Pipeline" in page.inner_text("body"))
    check(".pipeline-node-dot count is 8 (matches STAGES)", page.locator(".pipeline-node-dot").count() == 8)
    check("pipeline node idles with a breathing animation", animation_name(page, ".pipeline-node-dot") != "none")

    page.emulate_media(reduced_motion="reduce")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(300)
    check("pipeline node breathing suppressed under reduced-motion", animation_name(page, ".pipeline-node-dot") == "none")
    page.emulate_media(reduced_motion="no-preference")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(300)

    # Model selector icons
    body = page.inner_text("body")
    check("Gemini model has an icon", "⚡" in body and "Gemini" in body)
    check("Groq model has an icon", "🧠" in body and "Groq" in body)

    # Deployment cards
    check(".deploy-card count is 3", page.locator(".deploy-card").count() == 3)

    # Chip hover
    check(".chip-magnetic count is 3 (example chips)", page.locator(".chip-magnetic").count() == 3)

    # Textarea focus glow
    before = page.evaluate("getComputedStyle(document.querySelector('.glow-focus')).borderColor")
    page.click("#idea")
    page.wait_for_timeout(500)  # let the 420ms border/box-shadow transition settle
    after = page.evaluate("getComputedStyle(document.querySelector('.glow-focus')).borderColor")
    check("textarea panel border brightens on focus", before != after)
    caret = page.evaluate("getComputedStyle(document.querySelector('#idea')).caretColor")
    check("textarea caret is brand-purple", "167" in caret or "168" in caret)

    # Forge-press wow moment
    page.fill("#idea", "a recipe manager with meal plans")
    page.click("button:has-text('Forge It')")
    page.wait_for_timeout(150)  # inside the ~1.2s mocked POST delay, mid-ignition

    check("submit button compresses (igniting class)", "igniting" in (page.locator("button[type=submit]").get_attribute("class") or ""))
    check("form fields recede (igniting class)", page.locator(".forge-recede.igniting").count() == 1)
    check("pipeline nodes ignite", page.locator(".pipeline-node-dot.igniting").count() == 8)
    check("scenery backdrop boosts", page.locator(".scenery-layer.boosted").count() == 1)

    page.wait_for_url(BASE + "/projects/fixture-job-1", timeout=5000)
    check("navigates to the project workspace after ignition", page.url.endswith("/projects/fixture-job-1"))

    real_errors = [e for e in console_errors if "Failed to load resource" not in e and "ERR_" not in e]
    check("no console errors", not real_errors)
    if real_errors:
        print("Console errors:", real_errors[:5])

    browser.close()

print("\n%d failure(s)" % len(failures))
sys.exit(1 if failures else 0)
