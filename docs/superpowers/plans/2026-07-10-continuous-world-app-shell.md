# Continuous World — App Shell Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every authenticated ForgeAI page (Dashboard, New App, the generation workspace, Deploy Keys) shares one persistent, blurred Golden-Hour backdrop instead of a CSS gradient, and Dashboard's content stops looking like a generic analytics SaaS (no stat cards; prompt-as-hero; a quiet, hover-revealed project list; new Templates quick-fill chips).

**Architecture:** A single `<Scenery />` component, mounted once in `App.jsx` (sibling to `Routes`, never recreated by navigation), renders a fixed, `z-index: -1` layer stack: a captured static still from the landing's Golden Hour video (blurred, scaled), the existing train-window PNG overlay (blurred further, low opacity), and a dark scrim. `.app-shell` (used by all four authenticated pages) stops painting its own gradient and lets Scenery show through; its existing aurora-glow pseudo-elements are untouched. Dashboard.jsx then gets a content pass: remove the three stat cards, add a Templates chip row, and restyle the project list to a quiet dot+word status with hover-revealed actions.

**Tech Stack:** React 18 + Vite (frontend), Tailwind utility classes + a small custom CSS layer in `index.css`, Playwright (Python, via `backend/venv`) for smoke verification — this repo has no `npm test` wired up; all frontend verification in this codebase is ad hoc Playwright scripts run against a `vite preview` server, invoked through the backend's Python venv (already has `playwright` installed; the frontend itself does not).

## Global Constraints

- No backend or LLM API calls in any verification step — everything is mocked via `page.route()` against a static `vite preview` build. This project runs close to its free-tier Gemini/Groq quota; burning it on UI verification is out of scope and unnecessary.
- Every new/moved smoke script must exit 1 on any failed check and print `PASS `/`FAIL ` lines per check plus a final `N failure(s)` count — this is the established convention from this session's existing scripts (see Task 1) and later tasks' scripts must match it exactly for consistency.
- Use `page.wait_for_function(...)` (in-browser polling) for any assertion tied to a CSS transition's opacity — a Python-side `sleep()`-then-`evaluate()` loop has real IPC round-trip jitter and produces false negatives even when the transition is behaving correctly (confirmed this session via `requestAnimationFrame` ground-truth tracing against the veil transition).
- Preserve every existing data-flow behavior in `Dashboard.jsx` (fetch polling interval, delete/delete-all/fix/cancel handlers, empty/loading states) — this plan changes markup and CSS classes only, never the handlers or `jobsAPI` calls.
- Windows/PowerShell environment: use forward slashes are fine in JS import paths; shell commands in this plan are written for the Bash tool (Git Bash), which is what this session uses.

---

### Task 1: Promote the session's Playwright smoke scripts into the repo

**Files:**
- Create: `backend/scripts/frontend_smoke/smoke_landing.py`
- Create: `backend/scripts/frontend_smoke/smoke_workspace.py`
- Create: `backend/scripts/frontend_smoke/smoke_internal_veil.py`
- Create: `backend/scripts/frontend_smoke/README.md`

**Interfaces:**
- Produces: a durable `backend/scripts/frontend_smoke/` directory. Every later task's smoke script lives here too (`smoke_scenery.py` in Task 3, `smoke_dashboard_redesign.py` in Task 4) and is run the same way: `cd frontend && npm run build && (npx vite preview --port 4173 &) && cd ../backend && ./venv/Scripts/python scripts/frontend_smoke/<name>.py`.

These three scripts already exist and pass as of this session (in an ephemeral per-session scratch directory) — this task just gives them a permanent, version-controlled home so future sessions don't reconstruct them from scratch, and so Task 3/4's new scripts have a natural place to live.

- [ ] **Step 1: Create the directory and README**

```bash
mkdir -p "C:/Users/jerry/onedrive/Desktop/forgeai/backend/scripts/frontend_smoke"
```

Write `backend/scripts/frontend_smoke/README.md`:

```markdown
# Frontend smoke tests

Ad hoc Playwright scripts that verify frontend behavior against a built
`vite preview` server. No `npm test` is wired up in this repo — these are
it. All scripts mock the backend API via `page.route()`, so they need no
running backend, no `.env`, and cost zero LLM credits.

## Running

```bash
cd frontend
npm run build
npx vite preview --port 4173 --strictPort &   # background it
cd ../backend
./venv/Scripts/python scripts/frontend_smoke/smoke_landing.py
./venv/Scripts/python scripts/frontend_smoke/smoke_workspace.py
./venv/Scripts/python scripts/frontend_smoke/smoke_internal_veil.py
./venv/Scripts/python scripts/frontend_smoke/smoke_scenery.py
./venv/Scripts/python scripts/frontend_smoke/smoke_dashboard_redesign.py
```

Kill the preview server when done (`netstat -ano | grep :4173` on Windows to find the PID).

## Convention

Every script prints `PASS <name>` / `FAIL <name>` per assertion and exits 1
if any failed. Any check tied to a CSS transition's opacity uses
`page.wait_for_function(...)` (in-browser polling), never a fixed
`sleep()` then `evaluate()` — that pattern produced false negatives against
a transition that was actually behaving correctly (see git history on the
veil transition, 2026-07-10).
```

- [ ] **Step 2: Write `smoke_landing.py`**

```python
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
```

- [ ] **Step 3: Write `smoke_workspace.py`**

```python
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
```

- [ ] **Step 4: Write `smoke_internal_veil.py`**

```python
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
```

- [ ] **Step 5: Run all three to confirm they still pass from the new location**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai/frontend" && npm run build
```
Expected: `✓ built in <Ns>` with no errors.

```bash
netstat -ano | grep :4173 | grep LISTENING | awk '{print $NF}' | sort -u | while read pid; do taskkill //PID $pid //F 2>/dev/null; done
cd "C:/Users/jerry/onedrive/Desktop/forgeai/frontend" && (npx vite preview --port 4173 --strictPort &> /dev/null &) && sleep 3
```

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai/backend" && ./venv/Scripts/python scripts/frontend_smoke/smoke_landing.py && ./venv/Scripts/python scripts/frontend_smoke/smoke_workspace.py && ./venv/Scripts/python scripts/frontend_smoke/smoke_internal_veil.py
```
Expected: `0 failure(s)` printed three times (once per script).

- [ ] **Step 6: Commit**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai"
git add backend/scripts/frontend_smoke/
git commit -m "Promote session Playwright smoke scripts into the repo

Gives this session's ad hoc frontend verification scripts a durable,
version-controlled home instead of an ephemeral per-session scratch
directory, so future sessions (and this plan's later tasks) don't
reconstruct them from scratch."
```

---

### Task 2: Capture the Golden Hour still frame asset

**Files:**
- Create: `frontend/src/assets/scenery-golden-hour.jpg` (binary asset, committed)

**Interfaces:**
- Produces: a JPEG file at `frontend/src/assets/scenery-golden-hour.jpg` that Task 3's `Scenery.jsx` imports directly (`import sceneryStill from "../assets/scenery-golden-hour.jpg"` — Vite resolves this to a URL string at build time, no config needed).

The landing's Golden Hour scene is an external CloudFront video (`VIDEOS[0].src` in `frontend/src/lib/cinematic.js`). Rather than depend on that video at runtime for a *background* image (network dependency, no simple way to pin "a frame" via CSS), capture one frame once via a headless browser and commit it as a static asset — this is a one-time build step, not a runtime dependency.

- [ ] **Step 1: Create the assets directory**

```bash
mkdir -p "C:/Users/jerry/onedrive/Desktop/forgeai/frontend/src/assets"
```

- [ ] **Step 2: Write and run the capture script**

This is a one-off tool, not part of the app — write it into the repo at a
throwaway path, run it, then delete it in Step 3 so it never gets committed.

Write `frontend/scripts/_capture_scenery_still.py`:

```python
"""One-time asset capture: grab a still frame from the landing's Golden
Hour video and save it as a static JPEG for the app-wide blurred Scenery
background. Run once; the output is committed as a binary asset, never
regenerated at build or runtime. This script itself is deleted after use
(see the plan step that runs it) -- it is not part of the app."""
from playwright.sync_api import sync_playwright

VIDEO_URL = "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260702_081127_0992a171-d3c6-4978-8213-0ec5df8b6d63.mp4"
OUTPUT = "C:/Users/jerry/onedrive/Desktop/forgeai/frontend/src/assets/scenery-golden-hour.jpg"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.set_content(f"""
      <html><body style="margin:0">
        <video id="v" src="{VIDEO_URL}" autoplay muted playsinline
          style="width:1920px;height:1080px;object-fit:cover;display:block"></video>
      </body></html>
    """)
    page.wait_for_selector("#v")
    page.wait_for_timeout(1500)  # let it start buffering before we try to seek
    page.evaluate("""
      () => new Promise((resolve) => {
        const v = document.querySelector('#v');
        const onSeeked = () => { v.removeEventListener('seeked', onSeeked); resolve(); };
        v.addEventListener('seeked', onSeeked);
        v.pause();
        v.currentTime = 3;
      })
    """)
    page.wait_for_timeout(500)  # let the seeked frame actually paint
    page.locator("#v").screenshot(path=OUTPUT, type="jpeg", quality=82)
    browser.close()
print("Saved:", OUTPUT)
```

Run it:

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai/backend" && ./venv/Scripts/python ../frontend/scripts/_capture_scenery_still.py
```
Expected output: `Saved: C:/Users/jerry/onedrive/Desktop/forgeai/frontend/src/assets/scenery-golden-hour.jpg`

- [ ] **Step 3: Verify the captured frame actually shows the landscape (not a black/blank frame)**

Use the Read tool on `frontend/src/assets/scenery-golden-hour.jpg` to view it directly. Confirm it shows a warm sunset mountain landscape (matching the landing page's Golden Hour scene), not a black or corrupted frame — headless video capture can occasionally screenshot before the seek has actually painted. If it comes back black, increase the second `wait_for_timeout` from 500ms to 1200ms and re-run.

Also confirm the file size is reasonable for a background asset (expect roughly 150–400 KB at quality 82, 1920x1080 JPEG — if it's under 20 KB, that's a strong signal of a mostly-blank/black frame worth re-checking visually).

```bash
ls -la "C:/Users/jerry/onedrive/Desktop/forgeai/frontend/src/assets/scenery-golden-hour.jpg"
```

- [ ] **Step 4: Delete the one-off capture script**

```bash
rm "C:/Users/jerry/onedrive/Desktop/forgeai/frontend/scripts/_capture_scenery_still.py"
rmdir "C:/Users/jerry/onedrive/Desktop/forgeai/frontend/scripts" 2>/dev/null || true
```

- [ ] **Step 5: Commit**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai"
git add frontend/src/assets/scenery-golden-hour.jpg
git commit -m "Add captured Golden Hour still frame for the app-wide Scenery background

One-time capture from the landing's Golden Hour video (via headless
browser, seeked to t=3s), used as the blurred backdrop behind every
authenticated page (Task 3). Static image, not a runtime video
dependency -- zero decode cost."
```

---

### Task 3: Build the shared `<Scenery />` layer

**Files:**
- Create: `frontend/src/components/Scenery.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/index.css:104-125` (the `.app-shell` rule)
- Create: `backend/scripts/frontend_smoke/smoke_scenery.py`

**Interfaces:**
- Consumes: `OVERLAY_PNG` from `frontend/src/lib/cinematic.js` (existing export, unchanged); the Task 2 asset at `frontend/src/assets/scenery-golden-hour.jpg`.
- Produces: `Scenery` (default export of `Scenery.jsx`) — a component taking no props, rendered once. `.scenery-layer` / `.scenery-image` / `.scenery-frame` / `.scenery-scrim` CSS classes, referenced by the new smoke test and available for any future page.

- [ ] **Step 1: Write the failing test first**

Create `backend/scripts/frontend_smoke/smoke_scenery.py`:

```python
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
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai/frontend" && npm run build
netstat -ano | grep :4173 | grep LISTENING | awk '{print $NF}' | sort -u | while read pid; do taskkill //PID $pid //F 2>/dev/null; done
(npx vite preview --port 4173 --strictPort &> /dev/null &) && sleep 3
cd "C:/Users/jerry/onedrive/Desktop/forgeai/backend" && ./venv/Scripts/python scripts/frontend_smoke/smoke_scenery.py
```
Expected: FAILs on every `scenery-layer present` check (element doesn't exist yet), several `N failure(s)` at the end.

- [ ] **Step 3: Create `Scenery.jsx`**

```jsx
import React from "react";
import { OVERLAY_PNG } from "../lib/cinematic";
import sceneryStill from "../assets/scenery-golden-hour.jpg";

// The persistent blurred backdrop behind every authenticated page --
// mounted once in App.jsx (sibling to Routes) so route changes never
// recreate it. A static image, not video: zero decode cost behind the
// generation workspace's live WebSocket log stream.
export default function Scenery() {
  return (
    <div className="scenery-layer" aria-hidden="true">
      <div className="scenery-image" style={{ backgroundImage: `url(${sceneryStill})` }} />
      <div className="scenery-frame" style={{ backgroundImage: `url(${OVERLAY_PNG})` }} />
      <div className="scenery-scrim" />
    </div>
  );
}
```

- [ ] **Step 4: Add the CSS layer**

In `frontend/src/index.css`, insert this new block immediately before the `/* ── App shell: ambient brand glow behind glass panels ── */` comment (currently around line 104):

```css
/* ── Scenery: the persistent blurred backdrop behind every app page ──── */
/* One captured still from the landing's Golden Hour scene, blurred and
   darkened -- "the same world, further inside it" instead of an abstract
   gradient. Mounted once in App.jsx (not per-page), z-index -1 so it sits
   behind every page's content without any per-page wiring. */

.scenery-layer {
  position: fixed;
  inset: 0;
  z-index: -1;
  overflow: hidden;
  pointer-events: none;
}

.scenery-image {
  position: absolute;
  inset: 0;
  background-size: cover;
  background-position: center;
  filter: blur(28px);
  transform: scale(1.08); /* hides the blur's soft edge artifacts */
}

/* The train-window silhouette, present but subtle -- a hint of "still
   behind glass" rather than literal glass smudge. */
.scenery-frame {
  position: absolute;
  inset: 0;
  background-size: cover;
  background-position: center;
  filter: blur(40px);
  opacity: 0.25;
}

.scenery-scrim {
  position: absolute;
  inset: 0;
  background: rgba(9, 6, 13, 0.88);
}

```

- [ ] **Step 5: Strip `.app-shell`'s own background**

In `frontend/src/index.css`, replace the existing `.app-shell` rule (currently lines ~109-125):

```css
.app-shell {
  position: relative;
  min-height: 100vh;
  min-height: 100dvh;
  background-color: var(--ink-bg);
  /* The 85%/-8% glow used to sit almost entirely under NavBar's own
     ~85%-opaque backdrop (a sticky bar covering the top ~70px) -- it read
     as invisible because it never had a chance to hit visible content.
     Pushed down + widened so its falloff actually bleeds into the page,
     and bumped opacity since "subtle" was reading as "absent". */
  background-image:
    radial-gradient(720px 720px at 82% 14%, rgba(217, 119, 146, 0.22), transparent 65%),
    radial-gradient(700px 460px at -10% 30%, rgba(59, 130, 246, 0.07), transparent 60%),
    linear-gradient(135deg, #0b0813 0%, #1c1230 45%, #33182f 100%);
  background-attachment: fixed;
  overflow-x: hidden;
}
```

with:

```css
/* Background now lives in the shared, persistent <Scenery /> layer
   (mounted once in App.jsx) instead of a per-page CSS gradient -- this
   class only establishes the page's own stacking/scroll context and lets
   Scenery's blurred still show through beneath it. The aurora glow
   pseudo-elements below are unchanged and still layer on top of Scenery. */
.app-shell {
  position: relative;
  min-height: 100vh;
  min-height: 100dvh;
  overflow-x: hidden;
}
```

Leave `.app-shell::before`, `.app-shell::after`, and `.app-shell > *` (the aurora glows and content stacking) exactly as they are — do not modify those rules.

- [ ] **Step 6: Mount `<Scenery />` in `App.jsx`**

In `frontend/src/App.jsx`, add the import:

```jsx
import Scenery from "./components/Scenery";
```

(add it after the `VeilProvider` import line)

Change the `App` component's return from:

```jsx
  return (
    <AuthProvider>
      <VeilProvider>
      <Routes>
```

to:

```jsx
  return (
    <>
      <Scenery />
      <AuthProvider>
        <VeilProvider>
        <Routes>
```

And its closing tags from:

```jsx
      </Routes>
      </VeilProvider>
    </AuthProvider>
  );
```

to:

```jsx
        </Routes>
        </VeilProvider>
      </AuthProvider>
    </>
  );
```

- [ ] **Step 7: Run the test to verify it passes**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai/frontend" && npm run build
netstat -ano | grep :4173 | grep LISTENING | awk '{print $NF}' | sort -u | while read pid; do taskkill //PID $pid //F 2>/dev/null; done
(npx vite preview --port 4173 --strictPort &> /dev/null &) && sleep 3
cd "C:/Users/jerry/onedrive/Desktop/forgeai/backend" && ./venv/Scripts/python scripts/frontend_smoke/smoke_scenery.py
```
Expected: `0 failure(s)`.

- [ ] **Step 8: Visually confirm via screenshot (not just the assertions)**

The same method that caught the earlier navbar-occlusion bug — don't trust computed-style checks alone for a purely visual change:

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.route("**/me", lambda r: r.fulfill(status=200, content_type="application/json", body='{"email":"fixture@test.com"}'))
    page.route("**/jobs", lambda r: r.fulfill(status=200, content_type="application/json", body='{"jobs":[]}'))
    page.add_init_script("localStorage.setItem('token', 'fixture-token');")
    page.goto("http://localhost:4173/dashboard", wait_until="networkidle")
    page.wait_for_timeout(500)
    page.screenshot(path="/tmp/scenery_check.png")
    browser.close()
```

Read `/tmp/scenery_check.png` with the Read tool. Confirm the blurred sunset landscape is visibly present behind the dashboard content (not hidden under NavBar, not just a flat dark color) before moving on.

- [ ] **Step 9: Re-run Task 1's three existing smoke scripts to confirm no regression**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai/backend"
./venv/Scripts/python scripts/frontend_smoke/smoke_landing.py
./venv/Scripts/python scripts/frontend_smoke/smoke_workspace.py
./venv/Scripts/python scripts/frontend_smoke/smoke_internal_veil.py
```
Expected: `0 failure(s)` for all three (Landing has its own opaque video background and is unaffected by the `.app-shell` change; the other two only assert `.workspace-shell`/veil behavior, not `.app-shell`'s background).

- [ ] **Step 10: Commit**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai"
git add frontend/src/components/Scenery.jsx frontend/src/App.jsx frontend/src/index.css backend/scripts/frontend_smoke/smoke_scenery.py
git commit -m "Add persistent blurred Scenery backdrop shared by all authenticated pages

Replaces .app-shell's per-page CSS gradient with a single <Scenery />
layer (mounted once in App.jsx, never recreated by navigation): the
captured Golden Hour still, blurred and darkened, plus the existing
train-window overlay at low opacity. Closes the gap the user flagged
between the landing's cinematic scenery and the dashboard's abstract
gradient -- 'the same world, further inside it' instead of a color
approximation of it.

Verified via a new 21-check Playwright suite (scenery present + blurred
+ real asset on all 4 authenticated pages, .app-shell background fully
delegated) plus a direct screenshot check, and confirmed the existing
landing/workspace/veil suites still pass unchanged."
```

---

### Task 4: Redesign Dashboard's content

**Files:**
- Modify: `frontend/src/pages/Dashboard.jsx` (entire file rewritten below)
- Create: `backend/scripts/frontend_smoke/smoke_dashboard_redesign.py`

**Interfaces:**
- Consumes: `jobsAPI` (unchanged), `useAuth`, `useVeil`, `IDEA_DRAFT_KEY` (all unchanged imports).
- Produces: no new exports — this is a content/markup change to the default-exported `Dashboard` component only. No other file imports anything new from this one.

- [ ] **Step 1: Write the failing test first**

Create `backend/scripts/frontend_smoke/smoke_dashboard_redesign.py`:

```python
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
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai/frontend" && npm run build
netstat -ano | grep :4173 | grep LISTENING | awk '{print $NF}' | sort -u | while read pid; do taskkill //PID $pid //F 2>/dev/null; done
(npx vite preview --port 4173 --strictPort &> /dev/null &) && sleep 3
cd "C:/Users/jerry/onedrive/Desktop/forgeai/backend" && ./venv/Scripts/python scripts/frontend_smoke/smoke_dashboard_redesign.py
```
Expected: FAILs on "Templates chip present", "delete button hidden at rest" (buttons are currently always visible, so opacity will be 1 not 0), and possibly the stat-card checks depending on current copy. Some checks may pass already (e.g. "done job shows score inline" — the old `ScoreBadge` already renders `90 (A)`) — that's fine, TDD doesn't require every check to fail, only the ones testing genuinely new behavior.

- [ ] **Step 3: Rewrite `Dashboard.jsx`**

Replace the entire file with:

```jsx
import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { jobsAPI } from "../api";
import NavBar from "../components/NavBar";
import { useAuth } from "../AuthContext";
import { useVeil } from "../components/Veil";
import { IDEA_DRAFT_KEY } from "../lib/cinematic";
import { Trash2, Wrench, Zap, ArrowRight } from "lucide-react";

const STATUS_DOT = {
  pending:   "#facc15",
  running:   "#818cf8",
  done:      "#4ade80",
  error:     "#f87171",
  cancelled: "#9ca3af",
};

const TEMPLATES = [
  { label: "SaaS", idea: "A multi-tenant SaaS starter with team workspaces, billing, and role-based permissions" },
  { label: "CRM", idea: "A CRM with contacts, deals, and activity timeline" },
  { label: "Habit Tracker", idea: "A habit tracker with streaks, badges, dark mode, and weekly reports" },
  { label: "AI Agent", idea: "An AI agent dashboard with conversation history, tool-call logs, and usage analytics" },
];

function timeAgo(iso) {
  if (!iso) return "";
  const s = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s/60)}m ago`;
  if (s < 86400) return `${Math.floor(s/3600)}h ago`;
  return `${Math.floor(s/86400)}d ago`;
}

function greeting() {
  const h = new Date().getHours();
  if (h < 5) return "Working late";
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

export default function Dashboard() {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(null);
  const [deletingAll, setDeletingAll] = useState(false);
  const [idea, setIdea] = useState("");
  const { user } = useAuth();
  const { veilNav } = useVeil();
  const navigate = useNavigate();

  const fetchJobs = () => jobsAPI.list().then(r => setJobs(r.data.jobs || [])).catch(console.error).finally(() => setLoading(false));
  useEffect(() => { fetchJobs(); const t = setInterval(fetchJobs, 4000); return () => clearInterval(t); }, []);

  const forgeIdea = (e) => {
    e.preventDefault();
    if (idea.trim()) sessionStorage.setItem(IDEA_DRAFT_KEY, idea.trim());
    // Same cinematic hand-off as the landing page's "Forge It" — every
    // "start something new" moment in the app gets the same veil sweep.
    veilNav("/new");
  };

  const handleDelete = async (e, jobId) => {
    e.preventDefault();
    e.stopPropagation();
    if (!window.confirm("Delete this project and all its files? This cannot be undone.")) return;
    setDeleting(jobId);
    try {
      await jobsAPI.delete(jobId);
      setJobs(prev => prev.filter(j => j.id !== jobId));
    } catch (err) {
      alert(err.response?.data?.detail || "Delete failed");
    } finally {
      setDeleting(null);
    }
  };

  const handleDeleteAll = async () => {
    const deletableCount = jobs.filter(j => j.status !== "pending" && j.status !== "running").length;
    if (!deletableCount) return;
    if (!window.confirm(
      `Delete all ${deletableCount} project${deletableCount === 1 ? "" : "s"} and their files? This cannot be undone.`
    )) return;
    setDeletingAll(true);
    try {
      const res = await jobsAPI.deleteAll();
      if (res.data.skipped) {
        alert(`Deleted ${res.data.deleted} project(s). ${res.data.skipped} running/pending job(s) were skipped — cancel them first to delete.`);
      }
      await fetchJobs();
    } catch (err) {
      alert(err.response?.data?.detail || "Delete all failed");
    } finally {
      setDeletingAll(false);
    }
  };

  const handleFix = async (e, jobId) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      const res = await jobsAPI.retry(jobId);
      navigate(`/projects/${res.data.job_id}`);
    } catch (err) {
      alert(err.response?.data?.detail || "Fix failed");
    }
  };

  const running = jobs.filter(j => j.status === "running" || j.status === "pending").length;
  const firstName = user?.email ? user.email.split("@")[0] : null;

  return (
    <div className="app-shell">
      <NavBar />
      <div className="max-w-5xl mx-auto px-6 py-12">
        {/* Greeting + Forge bar — the hero, unchanged from the landing's
            prompt-first framing. Delayed 150ms so it slides in just after
            the veil lifts from the camera-zoom exit off the landing page */}
        <div className="anim-fade-up mb-6" style={{ "--d": "150ms" }}>
          <h1 className="hero-serif text-4xl sm:text-5xl text-white leading-tight">
            {greeting()}{firstName ? <span className="italic">, {firstName}</span> : ""}.
          </h1>
          <p className="text-gray-500 text-sm mt-2">What shall we forge today?</p>
          <form
            onSubmit={forgeIdea}
            className="glass-panel glow-focus rounded-full flex items-center gap-2 p-1.5 pl-5 mt-6 w-full max-w-xl"
          >
            <input
              type="text"
              value={idea}
              onChange={(e) => setIdea(e.target.value)}
              placeholder="Describe the app you imagine…"
              aria-label="Describe the app you want to build"
              className="flex-1 min-w-0 bg-transparent outline-none text-sm text-white placeholder:text-gray-600"
            />
            <button
              type="submit"
              className="flex items-center gap-1 bg-white text-slate-900 text-sm font-medium pl-4 pr-3 py-2 rounded-full whitespace-nowrap hover:bg-white/90 transition-colors"
            >
              Forge It
              <ArrowRight size={14} aria-hidden="true" />
            </button>
          </form>
        </div>

        {/* Templates — quick-fill chips, same pattern as New App's example
            chips. No navigation, just prefills the prompt above. */}
        <div className="anim-fade-up flex flex-wrap gap-2 mt-4 mb-10" style={{ "--d": "220ms" }}>
          {TEMPLATES.map((t) => (
            <button
              key={t.label}
              type="button"
              onClick={() => setIdea(t.idea)}
              className="text-xs text-gray-400 hover:text-gray-100 glass-panel hover:border-white/15 rounded-full px-3.5 py-1.5 transition-colors"
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="flex items-center justify-between mb-4">
          <h2 className="hero-serif text-2xl text-white">Recent Projects</h2>
          <div className="flex items-center gap-2">
            {running > 0 && (
              <span className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full border" style={{background:"rgba(99,102,241,0.1)",color:"#818cf8",borderColor:"rgba(99,102,241,0.2)"}}>
                <span className="live-dot bg-indigo-400" aria-hidden="true" /> {running} running
              </span>
            )}
            {jobs.some(j => j.status !== "pending" && j.status !== "running") && (
              <button
                onClick={handleDeleteAll}
                disabled={deletingAll}
                className="text-xs text-gray-600 hover:text-red-400 transition-colors disabled:opacity-40 underline underline-offset-2 decoration-white/20 hover:decoration-red-400/50">
                {deletingAll ? "Deleting…" : "Delete all"}
              </button>
            )}
          </div>
        </div>

        {loading ? (
          <div className="space-y-2" aria-label="Loading projects">
            {[0, 1, 2].map(i => (
              <div key={i} className="skeleton h-16 rounded-xl" />
            ))}
          </div>
        ) : jobs.length === 0 ? (
          <div className="anim-fade-up text-center py-20 glass-panel rounded-2xl border-dashed">
            <div className="mx-auto w-14 h-14 rounded-full liquid-glass flex items-center justify-center mb-5">
              <Zap size={22} className="text-violet-300" aria-hidden="true" />
            </div>
            <p className="hero-serif text-xl text-white mb-1">The anvil is quiet</p>
            <p className="text-gray-600 text-sm mb-6">Describe an idea above — your forged apps will live here</p>
          </div>
        ) : (
          <div className="space-y-2">
            {jobs.map((job, i) => {
              const isActive = job.status === "pending" || job.status === "running";
              const isDone = job.status === "done";
              const needsFix = isDone && (job.forge_score == null || job.forge_score < 80 || !job.backend_url);
              const grade = job.forge_score != null
                ? (job.forge_score >= 90 ? "A" : job.forge_score >= 80 ? "B" : job.forge_score >= 70 ? "C" : job.forge_score >= 60 ? "D" : "F")
                : null;
              const gradeColor = job.forge_score != null
                ? (job.forge_score >= 80 ? "#4ade80" : job.forge_score >= 60 ? "#facc15" : "#f87171")
                : null;
              return (
                <div key={job.id}
                  className="anim-fade-up hover-lift flex items-center gap-4 glass-panel rounded-xl px-4 py-3.5 hover:border-white/15 group cursor-pointer"
                  style={{ "--d": `${Math.min(i, 8) * 40}ms` }}
                  onClick={() => navigate(`/projects/${job.id}`)}>
                  <span className="flex items-center gap-1.5 text-xs shrink-0 w-24"
                    style={{color: isDone && job.forge_score != null ? gradeColor : "#666"}}>
                    {isDone && job.forge_score != null ? (
                      <>✓ {job.forge_score} · {grade}</>
                    ) : (
                      <>
                        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${isActive ? "live-dot" : ""}`}
                          style={{background: STATUS_DOT[job.status] || STATUS_DOT.error}} aria-hidden="true" />
                        {job.status}
                      </>
                    )}
                  </span>
                  <p className="flex-1 text-sm text-gray-300 group-hover:text-white transition-colors line-clamp-1">{job.idea}</p>
                  <span className="text-xs text-gray-600 shrink-0">{timeAgo(job.created_at)}</span>
                  {needsFix && (
                    <button
                      onClick={(e) => handleFix(e, job.id)}
                      title={`Score ${job.forge_score ?? '?'}/100 — click to fix and improve`}
                      className="opacity-0 group-hover:opacity-100 transition-opacity duration-200 shrink-0 flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs font-medium text-orange-400 border border-orange-500/30 hover:bg-orange-500/10">
                      <Wrench size={12} aria-hidden="true" /> Fix
                    </button>
                  )}
                  {!isActive && (
                    <button
                      onClick={(e) => handleDelete(e, job.id)}
                      disabled={deleting === job.id}
                      title="Delete project"
                      aria-label="Delete project"
                      className="opacity-0 group-hover:opacity-100 transition-opacity duration-200 shrink-0 p-2 rounded-lg text-gray-600 hover:text-red-400 hover:bg-red-500/10 disabled:opacity-40">
                      <Trash2 size={15} aria-hidden="true" />
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai/frontend" && npm run build
netstat -ano | grep :4173 | grep LISTENING | awk '{print $NF}' | sort -u | while read pid; do taskkill //PID $pid //F 2>/dev/null; done
(npx vite preview --port 4173 --strictPort &> /dev/null &) && sleep 3
cd "C:/Users/jerry/onedrive/Desktop/forgeai/backend" && ./venv/Scripts/python scripts/frontend_smoke/smoke_dashboard_redesign.py
```
Expected: `0 failure(s)`.

- [ ] **Step 5: Visually confirm via screenshot**

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.route("**/me", lambda r: r.fulfill(status=200, content_type="application/json", body='{"email":"jerrythomas05@test.com"}'))
    page.route("**/jobs", lambda r: r.fulfill(status=200, content_type="application/json", body='{"jobs":[{"id":"1","status":"done","idea":"CRM with contacts","forge_score":90,"backend_url":"https://x.up.railway.app","created_at":"2026-07-10T09:00:00"},{"id":"2","status":"running","idea":"Habit tracker","forge_score":None,"backend_url":None,"created_at":"2026-07-10T09:30:00"}]}'))
    page.add_init_script("localStorage.setItem('token', 'fixture-token');")
    page.goto("http://localhost:4173/dashboard", wait_until="networkidle")
    page.wait_for_timeout(500)
    page.screenshot(path="/tmp/dashboard_redesign_check.png")
    browser.close()
```

Read `/tmp/dashboard_redesign_check.png`. Confirm: no stat cards, the prompt reads as the hero, Templates chips are visible, the project list reads quiet (no bordered pill badges), and the blurred scenery from Task 3 is visible behind everything.

- [ ] **Step 6: Re-run all four prior smoke scripts to confirm no regression**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai/backend"
./venv/Scripts/python scripts/frontend_smoke/smoke_landing.py
./venv/Scripts/python scripts/frontend_smoke/smoke_workspace.py
./venv/Scripts/python scripts/frontend_smoke/smoke_internal_veil.py
./venv/Scripts/python scripts/frontend_smoke/smoke_scenery.py
```
Expected: `0 failure(s)` for all four. (`smoke_internal_veil.py` specifically depends on Dashboard's "Forge It" form and NavBar's "New App" button, both unchanged by this task — confirms the rewrite didn't break them.)

- [ ] **Step 7: Commit**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai"
git add frontend/src/pages/Dashboard.jsx backend/scripts/frontend_smoke/smoke_dashboard_redesign.py
git commit -m "Redesign Dashboard content: remove stat cards, add Templates, quiet project list

Stat cards (Apps Built / Completed / Avg Score) removed entirely -- they
don't help anyone build an app and were the biggest 'generic analytics
SaaS' signal on the page. New Templates chip row (SaaS/CRM/Habit
Tracker/AI Agent) reuses New App's existing quick-fill pattern. Project
list restyled to a quiet dot+word status (was bordered pill badges) with
Fix/Delete revealed on hover instead of always-visible -- zero handlers,
routes, or data flow changed, purely information density.

Verified via a new 9-check Playwright suite plus a direct screenshot
check, and confirmed all four prior smoke suites (landing, workspace,
internal-veil, scenery) still pass."
```

---

### Task 5: Final regression pass and push

**Files:** none (verification only)

- [ ] **Step 1: Full clean rebuild**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai/frontend" && rm -rf dist && npm run build
```
Expected: `✓ built in <Ns>` with zero errors.

- [ ] **Step 2: Run every smoke script in sequence**

```bash
netstat -ano | grep :4173 | grep LISTENING | awk '{print $NF}' | sort -u | while read pid; do taskkill //PID $pid //F 2>/dev/null; done
cd "C:/Users/jerry/onedrive/Desktop/forgeai/frontend" && (npx vite preview --port 4173 --strictPort &> /dev/null &) && sleep 3
cd "C:/Users/jerry/onedrive/Desktop/forgeai/backend"
for f in smoke_landing smoke_workspace smoke_internal_veil smoke_scenery smoke_dashboard_redesign; do
  echo "=== $f ==="
  ./venv/Scripts/python scripts/frontend_smoke/$f.py
done
```
Expected: `0 failure(s)` for all five scripts.

- [ ] **Step 3: Stop the preview server**

```bash
netstat -ano | grep :4173 | grep LISTENING | awk '{print $NF}' | sort -u | while read pid; do taskkill //PID $pid //F 2>/dev/null; done
```

- [ ] **Step 4: Refresh the graphify knowledge graph**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai" && graphify update .
```
Expected: `Code graph updated.`

- [ ] **Step 5: Push**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai" && git push
```

All five prior commits (Tasks 1–4 each commit independently) push together here if they weren't already pushed per-task. Confirm `git log --oneline -6` shows all five task commits before pushing.
