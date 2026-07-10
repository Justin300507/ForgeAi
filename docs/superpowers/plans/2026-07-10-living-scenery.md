# Living Scenery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the shared `<Scenery />` component with autonomous drifting mist, a slow-moving soft light, and 12 twinkling dust/firefly particles, so the shared backdrop behind all four authenticated pages feels alive without being consciously noticeable.

**Architecture:** Three new layers are appended (never reordering the existing three) inside `Scenery.jsx`'s `.scenery-layer` container: two mist blobs + one light glow (pure CSS `@keyframes`, no new component), and a new `<SceneryParticles />` component rendering exactly 12 mount-randomized dust/firefly motes driven by one shared `@keyframes` rule. All motion is GPU-compositor-only (`transform`/`opacity`) with zero per-frame JavaScript, and gated behind `@media (prefers-reduced-motion: no-preference)` so elements always render but only animate when the user hasn't opted out.

**Tech Stack:** React 18 + Vite (frontend), plain CSS in `frontend/src/index.css`, Playwright (Python, via `backend/venv`) for smoke verification — same ad hoc `vite preview`-based testing approach as the parent Continuous World App Shell plan.

## Global Constraints

- No `<canvas>`, no `requestAnimationFrame`, no per-frame JavaScript of any kind after mount — every animation is a CSS `@keyframes` rule on `transform`/`opacity` only.
- Particle randomization happens exactly once at mount via `useMemo` (never `useState`) — re-renders must never reshuffle particle positions.
- Particle count is fixed at exactly 12 (not randomized) so tests can assert an exact count.
- All new animation is wrapped in `@media (prefers-reduced-motion: no-preference)`. Under `prefers-reduced-motion: reduce`, the elements still render (static, unanimated) — never removed from the DOM.
- Do not reorder, remove, or modify the existing `.scenery-image`, `.scenery-scrim`, `.scenery-frame` layers, `Scenery.jsx`'s mount point, or its (currently prop-less) signature. New layers are appended after the existing three.
- No backend or LLM API calls in any verification step — everything mocked via `page.route()` against a static `vite preview` build, same as the parent plan.
- Every smoke script prints `PASS `/`FAIL ` lines per check plus a final `N failure(s)` count and exits 1 on any failure — same convention as `backend/scripts/frontend_smoke/README.md`.
- Windows/PowerShell environment: shell commands in this plan are written for the Bash tool (Git Bash).

---

### Task 1: Ambient mist + light layers

**Files:**
- Modify: `frontend/src/components/Scenery.jsx` (currently 17 lines — see below for full current content)
- Modify: `frontend/src/index.css:104-142` (the existing Scenery CSS block)
- Create: `backend/scripts/frontend_smoke/smoke_scenery_living.py`

**Interfaces:**
- Produces: `.scenery-mist--a`, `.scenery-mist--b`, `.scenery-light` CSS classes and their `@keyframes` (`scenery-mist-drift-a`, `scenery-mist-drift-b`, `scenery-light-drift`), referenced by this task's smoke test. Task 2 does not depend on any JS export from this task — only on these layers existing in `Scenery.jsx`'s render output so it can append `<SceneryParticles />` after them.

- [ ] **Step 1: Write the failing test first**

Create `backend/scripts/frontend_smoke/smoke_scenery_living.py`:

```python
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
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai/frontend" && npm run build
netstat -ano | grep :4173 | grep LISTENING | awk '{print $NF}' | sort -u | while read pid; do taskkill //PID $pid //F 2>/dev/null; done
(npx vite preview --port 4173 --strictPort &> /dev/null &) && sleep 3
cd "C:/Users/jerry/onedrive/Desktop/forgeai/backend" && ./venv/Scripts/python scripts/frontend_smoke/smoke_scenery_living.py
```
Expected: FAILs on all three "present" checks (elements don't exist yet) and both "animates by default" checks that depend on them (via `animationName` on a `null` element — Playwright's `getComputedStyle` on a missing selector throws, which will surface as an uncaught exception rather than a clean FAIL line; that's expected and confirms the elements are genuinely absent before implementation).

- [ ] **Step 3: Add the CSS**

In `frontend/src/index.css`, the current Scenery block (lines 104-142) ends with:

```css
.scenery-scrim {
  position: absolute;
  inset: 0;
  background: rgba(9, 6, 13, 0.88);
}
```

Insert this new block immediately after that `.scenery-scrim` rule (before the blank line and `/* ── App shell...` comment that currently follows at line 144):

```css

/* ── Living Scenery: ambient mist + light drift (Phase 1) ────────────── */
/* Autonomous, GPU-compositor-only (transform/opacity), no per-frame JS --
   safe to run behind the generation workspace's live WebSocket log stream.
   Elements always render; only the animation is gated behind
   prefers-reduced-motion, so nothing is ever hidden outright. */

.scenery-mist--a,
.scenery-mist--b {
  position: absolute;
  inset: -10%;
  border-radius: 50%;
  filter: blur(60px);
  pointer-events: none;
}

.scenery-mist--a {
  background: radial-gradient(ellipse at 30% 40%, rgba(168, 132, 255, 0.10), transparent 60%);
}

.scenery-mist--b {
  background: radial-gradient(ellipse at 70% 65%, rgba(217, 119, 146, 0.08), transparent 60%);
}

.scenery-light {
  position: absolute;
  inset: 0;
  background: radial-gradient(480px 480px at 50% 35%, rgba(255, 214, 170, 0.10), transparent 70%);
  pointer-events: none;
}

@media (prefers-reduced-motion: no-preference) {
  .scenery-mist--a {
    animation: scenery-mist-drift-a 72s ease-in-out infinite;
  }
  .scenery-mist--b {
    animation: scenery-mist-drift-b 90s ease-in-out infinite;
  }
  .scenery-light {
    animation: scenery-light-drift 120s ease-in-out infinite;
  }
}

@keyframes scenery-mist-drift-a {
  0%   { transform: translate(0, 0); opacity: 0.8; }
  50%  { transform: translate(6%, 4%); opacity: 1; }
  100% { transform: translate(0, 0); opacity: 0.8; }
}

@keyframes scenery-mist-drift-b {
  0%   { transform: translate(0, 0); opacity: 0.7; }
  50%  { transform: translate(-5%, -6%); opacity: 1; }
  100% { transform: translate(0, 0); opacity: 0.7; }
}

@keyframes scenery-light-drift {
  0%   { transform: translate(0, 0); }
  50%  { transform: translate(-4%, 3%); }
  100% { transform: translate(0, 0); }
}
```

- [ ] **Step 4: Render the three new divs in `Scenery.jsx`**

Current full content of `frontend/src/components/Scenery.jsx`:

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
      <div className="scenery-scrim" />
      <div className="scenery-frame" style={{ backgroundImage: `url(${OVERLAY_PNG})` }} />
    </div>
  );
}
```

Replace the `return (...)` block with:

```jsx
  return (
    <div className="scenery-layer" aria-hidden="true">
      <div className="scenery-image" style={{ backgroundImage: `url(${sceneryStill})` }} />
      <div className="scenery-scrim" />
      <div className="scenery-frame" style={{ backgroundImage: `url(${OVERLAY_PNG})` }} />
      <div className="scenery-mist--a" />
      <div className="scenery-mist--b" />
      <div className="scenery-light" />
    </div>
  );
}
```

(Only the `return` block changes — the imports and the doc comment above `export default function Scenery()` stay exactly as they are.)

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai/frontend" && npm run build
netstat -ano | grep :4173 | grep LISTENING | awk '{print $NF}' | sort -u | while read pid; do taskkill //PID $pid //F 2>/dev/null; done
(npx vite preview --port 4173 --strictPort &> /dev/null &) && sleep 3
cd "C:/Users/jerry/onedrive/Desktop/forgeai/backend" && ./venv/Scripts/python scripts/frontend_smoke/smoke_scenery_living.py
```
Expected: `0 failure(s)`.

- [ ] **Step 6: Visually confirm via screenshot**

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
    page.screenshot(path="C:/Users/jerry/AppData/Local/Temp/claude_living_scenery_check.png")
    browser.close()
```

Read the screenshot with the Read tool. Confirm the mist/light additions read as a barely-noticeable warmth/depth shift over the existing Scenery backdrop — not a visible glowing blob, not distracting. If it reads as too strong, that's a values-only fix (lower the `rgba` alpha values in Step 3), not a structural change.

- [ ] **Step 7: Re-run the parent plan's five existing smoke scripts to confirm no regression**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai/backend"
./venv/Scripts/python scripts/frontend_smoke/smoke_landing.py
./venv/Scripts/python scripts/frontend_smoke/smoke_workspace.py
./venv/Scripts/python scripts/frontend_smoke/smoke_internal_veil.py
./venv/Scripts/python scripts/frontend_smoke/smoke_scenery.py
./venv/Scripts/python scripts/frontend_smoke/smoke_dashboard_redesign.py
```
Expected: `0 failure(s)` for all five (none of them assert anything about the new mist/light layers, and the existing `.scenery-image`/`.scenery-scrim`/`.scenery-frame` layers are untouched).

- [ ] **Step 8: Commit**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai"
git add frontend/src/components/Scenery.jsx frontend/src/index.css backend/scripts/frontend_smoke/smoke_scenery_living.py
git commit -m "Add ambient mist + light drift to the shared Scenery backdrop

Two slow-drifting mist blobs and one soft warm light glow, appended
after the existing image/scrim/frame layers -- pure CSS @keyframes,
no per-frame JS, gated behind prefers-reduced-motion so elements
always render but only animate when the user hasn't opted out.
Phase 1 of the Living Scenery spec
(docs/superpowers/specs/2026-07-10-living-scenery-design.md).

Verified via a new 10-check Playwright suite (elements present,
animation applied by default, animation suppressed under emulated
reduced-motion) plus a direct screenshot check, and confirmed all
five prior smoke suites still pass."
```

---

### Task 2: Twinkling dust/firefly particles

**Files:**
- Create: `frontend/src/components/SceneryParticles.jsx`
- Modify: `frontend/src/components/Scenery.jsx` (add import + render `<SceneryParticles />`)
- Modify: `frontend/src/index.css` (append particle CSS after Task 1's Living Scenery block)
- Modify: `backend/scripts/frontend_smoke/smoke_scenery_living.py` (append particle checks)

**Interfaces:**
- Consumes: nothing from Task 1 beyond `Scenery.jsx`'s existing render structure (appends after the mist/light divs Task 1 added).
- Produces: `SceneryParticles` (default export, no props) — a component rendering exactly 12 `<span class="scenery-particle scenery-particle--dust|--firefly">` elements. No other file will import this component except `Scenery.jsx`.

- [ ] **Step 1: Write the failing test first**

Append to `backend/scripts/frontend_smoke/smoke_scenery_living.py` — insert the following **immediately after** the existing `check("light animates by default", ...)` line and **before** the `page.emulate_media(reduced_motion="reduce")` line:

```python
    check(".scenery-particle count is exactly 12", page.locator(".scenery-particle").count() == 12)
    first_particle_animation = animation_name(page, ".scenery-particle")
    check("first particle animates by default", first_particle_animation != "none")
```

Then insert the following **immediately after** the existing `check("light suppressed under reduced-motion", ...)` line and **before** the `real_errors = [...]` line:

```python
    check("particle suppressed under reduced-motion", animation_name(page, ".scenery-particle") == "none")
```

Finally, insert this spot-check on a second authenticated page (confirming the shared component, not re-verifying all four pages in depth — that's already covered by `smoke_scenery.py`) **immediately before** the `real_errors = [...]` line (i.e. after the reduced-motion block, still inside the `with sync_playwright()` block, before `browser.close()`):

```python
    page.emulate_media(reduced_motion="no-preference")
    page.goto(BASE + "/new", wait_until="networkidle")
    page.wait_for_timeout(400)
    check("/new: .scenery-particle count is exactly 12", page.locator(".scenery-particle").count() == 12)
```

- [ ] **Step 2: Run it to confirm the new checks fail**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai/frontend" && npm run build
netstat -ano | grep :4173 | grep LISTENING | awk '{print $NF}' | sort -u | while read pid; do taskkill //PID $pid //F 2>/dev/null; done
(npx vite preview --port 4173 --strictPort &> /dev/null &) && sleep 3
cd "C:/Users/jerry/onedrive/Desktop/forgeai/backend" && ./venv/Scripts/python scripts/frontend_smoke/smoke_scenery_living.py
```
Expected: the three new checks FAIL (`.scenery-particle` doesn't exist yet — count is 0, not 12; `animationName` on a missing element throws or reads `"none"` since there's no element). All of Task 1's checks continue to PASS (regression-safe).

- [ ] **Step 3: Create `SceneryParticles.jsx`**

```jsx
import React, { useMemo } from "react";

const PARTICLE_COUNT = 12;
const FIREFLY_COUNT = 3;

function randomParticle(index) {
  const isFirefly = index < FIREFLY_COUNT;
  return {
    id: index,
    isFirefly,
    left: `${Math.random() * 100}%`,
    top: `${Math.random() * 100}%`,
    size: isFirefly ? `${3 + Math.random() * 1.5}px` : `${1.5 + Math.random()}px`,
    duration: `${14 + Math.random() * 10}s`,
    delay: `${Math.random() * -20}s`,
    baseOpacity: isFirefly ? 0.35 + Math.random() * 0.15 : 0.15 + Math.random() * 0.1,
  };
}

// Twelve ambient dust/firefly motes for the Living Scenery backdrop --
// position/timing randomized once at mount (useMemo, not state) so the
// randomization never recomputes on re-render. Purely decorative, driven
// by CSS @keyframes only after mount (no per-frame JS).
export default function SceneryParticles() {
  const particles = useMemo(
    () => Array.from({ length: PARTICLE_COUNT }, (_, i) => randomParticle(i)),
    []
  );

  return (
    <>
      {particles.map((p) => (
        <span
          key={p.id}
          className={`scenery-particle ${p.isFirefly ? "scenery-particle--firefly" : "scenery-particle--dust"}`}
          style={{
            "--particle-left": p.left,
            "--particle-top": p.top,
            "--particle-size": p.size,
            "--particle-duration": p.duration,
            "--particle-delay": p.delay,
            "--particle-base-opacity": p.baseOpacity,
          }}
        />
      ))}
    </>
  );
}
```

- [ ] **Step 4: Wire it into `Scenery.jsx`**

Add the import after the existing `sceneryStill` import:

```jsx
import sceneryStill from "../assets/scenery-golden-hour.jpg";
import SceneryParticles from "./SceneryParticles";
```

Change the end of the `return` block from:

```jsx
      <div className="scenery-light" />
    </div>
  );
}
```

to:

```jsx
      <div className="scenery-light" />
      <SceneryParticles />
    </div>
  );
}
```

- [ ] **Step 5: Append the particle CSS**

In `frontend/src/index.css`, add this block immediately after Task 1's `@keyframes scenery-light-drift { ... }` rule:

```css

.scenery-particle {
  position: absolute;
  border-radius: 50%;
  pointer-events: none;
  left: var(--particle-left);
  top: var(--particle-top);
  width: var(--particle-size);
  height: var(--particle-size);
  opacity: var(--particle-base-opacity);
}

.scenery-particle--dust {
  background: rgba(226, 218, 255, 0.4);
}

.scenery-particle--firefly {
  background: rgba(255, 200, 140, 0.55);
  box-shadow: 0 0 6px rgba(255, 200, 140, 0.5);
}

@media (prefers-reduced-motion: no-preference) {
  .scenery-particle {
    animation-name: scenery-particle-drift;
    animation-timing-function: ease-in-out;
    animation-iteration-count: infinite;
    animation-duration: var(--particle-duration);
    animation-delay: var(--particle-delay);
  }
}

@keyframes scenery-particle-drift {
  0%   { transform: translateY(0); opacity: var(--particle-base-opacity); }
  50%  { transform: translateY(-18px); opacity: calc(var(--particle-base-opacity) * 1.6); }
  100% { transform: translateY(0); opacity: var(--particle-base-opacity); }
}
```

- [ ] **Step 6: Run the test to verify it passes**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai/frontend" && npm run build
netstat -ano | grep :4173 | grep LISTENING | awk '{print $NF}' | sort -u | while read pid; do taskkill //PID $pid //F 2>/dev/null; done
(npx vite preview --port 4173 --strictPort &> /dev/null &) && sleep 3
cd "C:/Users/jerry/onedrive/Desktop/forgeai/backend" && ./venv/Scripts/python scripts/frontend_smoke/smoke_scenery_living.py
```
Expected: `0 failure(s)` (all 14 checks — Task 1's 10 plus this task's 4 new ones — pass).

- [ ] **Step 7: Visually confirm via screenshot**

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
    page.screenshot(path="C:/Users/jerry/AppData/Local/Temp/claude_living_scenery_particles_check.png")
    browser.close()
```

Read the screenshot with the Read tool. Confirm the particles read as faint, barely-visible motes (not a busy "snow" effect covering the screen) — this is the same manual-judgment gate used throughout the parent Scenery work. If particles read as too visible/busy, that's a values-only fix (lower `baseOpacity` ranges or particle `size` in `SceneryParticles.jsx` Step 3), not a structural change.

- [ ] **Step 8: Re-run the parent plan's five existing smoke scripts plus Task 1's own suite to confirm no regression**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai/backend"
./venv/Scripts/python scripts/frontend_smoke/smoke_landing.py
./venv/Scripts/python scripts/frontend_smoke/smoke_workspace.py
./venv/Scripts/python scripts/frontend_smoke/smoke_internal_veil.py
./venv/Scripts/python scripts/frontend_smoke/smoke_scenery.py
./venv/Scripts/python scripts/frontend_smoke/smoke_dashboard_redesign.py
```
Expected: `0 failure(s)` for all five.

- [ ] **Step 9: Commit**

```bash
cd "C:/Users/jerry/onedrive/Desktop/forgeai"
git add frontend/src/components/SceneryParticles.jsx frontend/src/components/Scenery.jsx frontend/src/index.css backend/scripts/frontend_smoke/smoke_scenery_living.py
git commit -m "Add 12 twinkling dust/firefly particles to the Living Scenery backdrop

New SceneryParticles.jsx renders exactly 12 motes (3 warm 'fireflies',
9 dim 'dust'), position/timing randomized once at mount via useMemo
(never recomputed on re-render), driven entirely by one shared CSS
@keyframes rule after mount -- no per-frame JS. Gated behind
prefers-reduced-motion like the mist/light layers from the prior
commit. Completes Phase 1 of the Living Scenery spec.

Verified via 4 new Playwright checks (exact count on two pages,
animates by default, suppressed under emulated reduced-motion) plus a
direct screenshot check, and confirmed all five prior smoke suites
still pass."
```

---

### Task 3: Final regression pass and push

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
for f in smoke_landing smoke_workspace smoke_internal_veil smoke_scenery smoke_dashboard_redesign smoke_scenery_living; do
  echo "=== $f ==="
  ./venv/Scripts/python scripts/frontend_smoke/$f.py
done
```
Expected: `0 failure(s)` for all six scripts. If `smoke_landing.py` times out on `networkidle` (the landing page's video streams from an external CDN and can occasionally cause a slow first `networkidle` wait — a known flake from the parent plan's execution, unrelated to any Scenery change), retry it alone once before treating it as a real failure.

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
cd "C:/Users/jerry/onedrive/Desktop/forgeai" && git log --oneline -4
```
Confirm the log shows this plan's two task commits (Task 2's, then Task 1's, most recent first) before pushing.

```bash
git push
```
