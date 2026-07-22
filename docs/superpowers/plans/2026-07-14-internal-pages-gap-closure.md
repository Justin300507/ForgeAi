# Internal Pages Cinematic Gap-Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the 7 remaining gaps between ForgeAI's internal pages and the landing page's cinematic design language, without touching anything that already matches (see `docs/superpowers/specs/2026-07-14-internal-pages-gap-closure-design.md`).

**Architecture:** Pure frontend change across `frontend/src/lib/pipelineStages.js` (shared stage/icon data), `frontend/src/pages/ProjectDetail.jsx` (live pipeline stepper + log stream), `frontend/src/pages/NewProject.jsx` (pipeline preview card), `frontend/src/components/SceneryBoost.jsx` + `Scenery.jsx` (sustained ambient intensification), `frontend/src/pages/Dashboard.jsx` (status badges), and new CSS rules appended to `frontend/src/index.css`. No backend changes, no new dependencies (icons come from `lucide-react`, already a dependency).

**Tech Stack:** React 18 (JSX), Tailwind (utility classes) + hand-written CSS in `index.css` for bespoke animation classes, `lucide-react` icons, Vite build.

## Global Constraints

- Do not modify `Landing.jsx` or any landing-page-only asset — explicitly out of scope.
- All new CSS animations must have a `@media (prefers-reduced-motion: reduce)` fallback, matching every existing animation rule already in `index.css`.
- No new npm dependencies — `lucide-react` already ships every icon needed.
- No backend/API changes — reuse existing endpoints only (`jobsAPI.retry` for the "Fix" action, no new "redeploy" endpoint).
- This codebase has no frontend unit-test runner (`frontend/package.json` has no `test` script, no Vitest/Jest). Verification is: `npm run build` must succeed after every task, plus a final Playwright/manual pass (Task 7) per this project's CLAUDE.md ("For UI or frontend changes... use Playwright... before reporting complete").
- Preserve every existing inline comment that explains non-obvious behavior (e.g. the header comments in `SceneryBoost.jsx`, `pipelineStages.js`) — extend them, don't delete them.

---

### Task 1: Shared stage icon map

**Files:**
- Modify: `frontend/src/lib/pipelineStages.js` (whole file, currently 22 lines)

**Interfaces:**
- Consumes: nothing new (existing `STAGES` array, `detectStage()`)
- Produces: `STAGES[i].Icon` — a `lucide-react` component reference for each of the 8 stage objects, consumed by Task 2 (`ProjectDetail.jsx`) and Task 3 (`NewProject.jsx`)

- [ ] **Step 1: Replace the file contents**

Replace the entire contents of `frontend/src/lib/pipelineStages.js` with:

```js
import { Lightbulb, LayoutTemplate, Server, MonitorSmartphone, ShieldCheck, Play, Rocket, Flag } from "lucide-react";

// Shared pipeline stage list -- single source of truth for both the live
// generation stepper (ProjectDetail.jsx) and the pre-submit preview card
// (NewProject.jsx), so the two never drift out of sync. Each stage also
// carries an Icon (lucide-react component) shown while the stage is
// waiting/active; completed/failed stages override it with a checkmark/✕
// at the call site.
export const STAGES = [
  { id:"plan",     label:"Planning",     keywords:["PRODUCT MANAGER"],                         Icon: Lightbulb },
  { id:"arch",     label:"Architecture", keywords:["ARCHITECT","TECH LEAD"],                   Icon: LayoutTemplate },
  { id:"backend",  label:"Backend",      keywords:["BACKEND TEAM","Wave 1","Wave 4"],           Icon: Server },
  { id:"frontend", label:"Frontend",     keywords:["FRONTEND TEAM","START FRONTEND"],           Icon: MonitorSmartphone },
  { id:"validate", label:"Validation",   keywords:["VALIDATION LOOP","Fix attempt","PATCHER"],  Icon: ShieldCheck },
  { id:"runtime",  label:"Runtime",      keywords:["RUNTIME","uvicorn","smoke test"],           Icon: Play },
  { id:"deploy",   label:"Deploy",       keywords:["Cloudflare","Render","GitHub","DEPLOY"],    Icon: Rocket },
  { id:"done",     label:"Complete",     keywords:["FINAL STATUS","V6 SCORE","Forge Score"],    Icon: Flag },
];

export function detectStage(logs) {
  for (let i = logs.length - 1; i >= 0; i--) {
    for (let s = STAGES.length - 1; s >= 0; s--) {
      if (STAGES[s].keywords.some(k => logs[i].includes(k))) return STAGES[s].id;
    }
  }
  return null;
}
```

- [ ] **Step 2: Verify the build**

Run (from `frontend/`): `npm run build`
Expected: exits 0, no errors mentioning `pipelineStages.js` or unresolved `lucide-react` imports.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/pipelineStages.js
git commit -m "Add per-stage icons to the shared pipeline stage list"
```

---

### Task 2: Live pipeline stepper — icons, celebration pop, shake, connector fill

**Files:**
- Modify: `frontend/src/pages/ProjectDetail.jsx:1-69` (imports + `PipelineBar`)
- Modify: `frontend/src/index.css` (append new rules near the existing `.stepper-node-active` block, currently around line 579-599)

**Interfaces:**
- Consumes: `STAGES[i].Icon` from Task 1
- Produces: `.stepper-node-complete`, `.stepper-node-shake`, `.stepper-connector--h`, `.stepper-connector--v`, `.stepper-connector__fill` CSS classes — not consumed elsewhere in this plan, but must not collide with any existing class name (verified: none of these exist yet in `index.css`)

- [ ] **Step 1: Append new CSS rules to `frontend/src/index.css`**

Insert immediately after the existing block that ends with:
```css
@media (prefers-reduced-motion: reduce) {
  .train-bob { animation: none; transform: scale(1.03); }
  .anim-fade-up, .anim-scale-in { animation: none; opacity: 1; transform: none; }
  .skeleton { animation: none; }
  .live-dot { animation: none; }
  .app-shell::before, .app-shell::after { animation: none; }
  .forge-veil { display: none; }
  .stepper-node-active { animation: none; }
  .hover-lift:hover { transform: none; }
  * { transition-duration: 0.01ms !important; }
}
```

Add this new block right after it (still inside `index.css`, end of file):

```css

/* ── Stepper node: one-shot completion pop + failure shake ───────────── */

@keyframes stepper-node-complete {
  0%   { transform: scale(1);    filter: brightness(1); }
  45%  { transform: scale(1.28); filter: brightness(1.6); }
  100% { transform: scale(1);    filter: brightness(1); }
}

.stepper-node-complete {
  animation: stepper-node-complete 450ms cubic-bezier(0.34, 1.56, 0.64, 1) both;
}

@keyframes stepper-node-shake {
  0%, 100% { transform: translateX(0); }
  20%      { transform: translateX(-3px); }
  40%      { transform: translateX(3px); }
  60%      { transform: translateX(-2px); }
  80%      { transform: translateX(2px); }
}

.stepper-node-shake {
  animation: stepper-node-shake 400ms ease-in-out;
}

/* ── Stepper connector: animated left-to-right (or top-to-bottom) fill
   when a stage completes, instead of an instant color swap ──────────── */

.stepper-connector--h,
.stepper-connector--v {
  position: relative;
  overflow: hidden;
  background: #2a2a3d;
}

.stepper-connector__fill {
  position: absolute;
  inset: 0;
  background: #4ade8050;
  transition: transform 600ms cubic-bezier(0.16, 1, 0.3, 1);
}

.stepper-connector--h .stepper-connector__fill {
  transform: scaleX(0);
  transform-origin: left;
}

.stepper-connector--v .stepper-connector__fill {
  transform: scaleY(0);
  transform-origin: top;
}

.stepper-connector--h .stepper-connector__fill.filled {
  transform: scaleX(1);
}

.stepper-connector--v .stepper-connector__fill.filled {
  transform: scaleY(1);
}

/* ── Log stream: each new line eases in as it arrives ─────────────────── */

@keyframes log-line-in {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
}

.log-line-in {
  animation: log-line-in 180ms ease-out both;
}

/* ── Scenery sustain: gentle, persistent intensification for the whole
   duration of an active generation (vs. the punchy ~900ms Forge-press
   .boosted above). Set via ProjectDetail.jsx while job.status is
   pending/running. Composes with .boosted -- boosted's stronger values
   win since both are just classes on the same element. ─────────────── */

.scenery-layer.sustained .scenery-mist--a,
.scenery-layer.sustained .scenery-mist--b,
.scenery-layer.sustained .scenery-light {
  filter: brightness(1.18);
}

.scenery-layer.sustained .scenery-scrim {
  background: rgba(9, 6, 13, 0.80);
}

@media (prefers-reduced-motion: no-preference) {
  .scenery-layer.sustained .scenery-mist--a { animation-duration: 55s; }
  .scenery-layer.sustained .scenery-mist--b { animation-duration: 69s; }
  .scenery-layer.sustained .scenery-light   { animation-duration: 92s; }
}

@media (prefers-reduced-motion: reduce) {
  .stepper-node-complete { animation: none; }
  .stepper-node-shake { animation: none; }
  .stepper-connector__fill { transition: none; }
  .log-line-in { animation: none; opacity: 1; transform: none; }
}
```

- [ ] **Step 2: Update imports in `frontend/src/pages/ProjectDetail.jsx`**

Change line 3 from:
```js
import { Globe, BookOpen, Github, Download, ShieldCheck } from "lucide-react";
```
to:
```js
import { Globe, BookOpen, Github, Download, ShieldCheck, Loader2 } from "lucide-react";
```

- [ ] **Step 3: Rewrite the `PipelineBar` function**

Replace the entire `PipelineBar` function (lines 8-69) with:

```js
function PipelineBar({ logs, status, vertical }) {
  const active = detectStage(logs);
  const activeIdx = STAGES.findIndex(s => s.id === active);
  const prevActiveIdx = useRef(activeIdx);
  const [celebrateIdx, setCelebrateIdx] = useState(-1);
  const [shaking, setShaking] = useState(false);

  // A stage just finished the moment the detected active stage advances --
  // celebrate the one immediately before the new active stage with a
  // one-shot pop, then clear the flag so it never replays on unrelated
  // re-renders (e.g. new log lines arriving).
  useEffect(() => {
    if (activeIdx > prevActiveIdx.current) {
      setCelebrateIdx(activeIdx - 1);
      const t = setTimeout(() => setCelebrateIdx(-1), 450);
      prevActiveIdx.current = activeIdx;
      return () => clearTimeout(t);
    }
    prevActiveIdx.current = activeIdx;
  }, [activeIdx]);

  useEffect(() => {
    if (status !== "error") return;
    setShaking(true);
    const t = setTimeout(() => setShaking(false), 400);
    return () => clearTimeout(t);
  }, [status]);

  return (
    <div className={vertical
      ? "flex flex-col gap-3"
      : "flex items-center gap-1 overflow-x-auto py-1"}>
      {STAGES.map((stage, i) => {
        const StageIcon = stage.Icon;
        const past = activeIdx > i;
        const curr = activeIdx === i;
        const err  = status === "error" && curr;
        const fin  = status === "done" && stage.id === "done";
        // Live, in-progress node gets the neon-emerald glow; everything
        // else (done/error/upcoming) stays on the plain palette below.
        const isLiveActive = curr && !err && !fin;
        const nodeClass = [
          "w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold border-2 transition-all shrink-0",
          isLiveActive ? "stepper-node-active" : "",
          celebrateIdx === i ? "stepper-node-complete" : "",
          err && shaking ? "stepper-node-shake" : "",
        ].filter(Boolean).join(" ");
        const node = (
          <div className={nodeClass}
            style={{
              borderColor: err ? "#ef4444" : (fin||past) ? "#4ade80" : isLiveActive ? "#34d399" : "#2a2a3d",
              background:  err ? "rgba(239,68,68,0.15)" : (fin||past) ? "rgba(74,222,128,0.15)" : isLiveActive ? "rgba(52,211,153,0.15)" : "#12121f",
              color:       err ? "#f87171" : (fin||past) ? "#4ade80" : isLiveActive ? "#34d399" : "#666"
            }}>
            {(fin || past) ? "✓" : err ? "✕" : isLiveActive
              ? <Loader2 size={12} className="animate-spin" aria-hidden="true" />
              : <StageIcon size={12} aria-hidden="true" />}
          </div>
        );
        const label = (
          <span className="text-[9px] whitespace-nowrap font-medium"
            style={{color: err ? "#f87171" : (fin||past) ? "#4ade80" : isLiveActive ? "#34d399" : "#444"}}>
            {stage.label}
          </span>
        );
        if (vertical) {
          return (
            <div key={stage.id} className="flex items-center gap-3">
              <div className="flex flex-col items-center shrink-0">
                {node}
                {i < STAGES.length - 1 && (
                  <div className="stepper-connector--v w-px h-4 mt-1">
                    <div className={`stepper-connector__fill${past ? " filled" : ""}`} />
                  </div>
                )}
              </div>
              <span className="text-xs font-medium"
                style={{color: err ? "#f87171" : (fin||past) ? "#4ade80" : isLiveActive ? "#34d399" : "#666"}}>
                {stage.label}
              </span>
            </div>
          );
        }
        return (
          <React.Fragment key={stage.id}>
            <div className="flex flex-col items-center shrink-0 min-w-0">
              {node}
              <span className="mt-1">{label}</span>
            </div>
            {i < STAGES.length - 1 && (
              <div className="stepper-connector--h h-px flex-1 min-w-[8px]">
                <div className={`stepper-connector__fill${past ? " filled" : ""}`} />
              </div>
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: Wire the sustained scenery boost and log-line entrance class**

In `frontend/src/pages/ProjectDetail.jsx`, add the import (near the top, with the other imports):
```js
import { useSceneryBoost } from "../components/SceneryBoost";
```

Inside `export default function ProjectDetail()`, right after the existing `const bottom = useRef(true);` line, add:
```js
  const sceneryBoost = useSceneryBoost();
```

Right after the existing WebSocket `useEffect` (the one starting `useEffect(() => { if (!job || ...`), add a new effect:
```js
  useEffect(() => {
    const active = job?.status === "pending" || job?.status === "running";
    sceneryBoost?.setSustained(active);
    return () => sceneryBoost?.setSustained(false);
  }, [job?.status]);
```

In the same WebSocket effect, inside `ws.onerror = () => ws.close();`, change it to also drop the sustained boost:
```js
    ws.onerror = () => { sceneryBoost?.setSustained(false); ws.close(); };
```

Finally, find the log-line render line:
```jsx
: logs.map((line, i) => <p key={i} className="leading-relaxed whitespace-pre-wrap" style={{color:LOG_CLS(line)}}>{line}</p>)
```
and change the className to:
```jsx
: logs.map((line, i) => <p key={i} className="log-line-in leading-relaxed whitespace-pre-wrap" style={{color:LOG_CLS(line)}}>{line}</p>)
```

- [ ] **Step 5: Verify the build**

Run (from `frontend/`): `npm run build`
Expected: exits 0, no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/ProjectDetail.jsx frontend/src/index.css
git commit -m "Add stage icons, completion/shake animations, connector fill, sustained scenery boost, and log-line entrance to the live pipeline stepper"
```

---

### Task 3: Pipeline preview card icons (`NewProject.jsx`)

**Files:**
- Modify: `frontend/src/pages/NewProject.jsx:229-240`

**Interfaces:**
- Consumes: `STAGES[i].Icon` from Task 1

- [ ] **Step 1: Replace the pipeline preview list**

Replace:
```jsx
              <ol className="space-y-3.5">
                {STAGES.map((stage, i) => (
                  <li key={stage.id} className="flex items-center gap-3 text-sm text-gray-400">
                    <span
                      className={`pipeline-node-dot w-2 h-2 rounded-full bg-violet-300 shrink-0${igniting ? " igniting" : ""}`}
                      style={{ "--node-delay": `${i * 420}ms`, "--ignite-delay": `${i * 65}ms` }}
                      aria-hidden="true" />
                    <span>{stage.label}</span>
                    <span className="text-xs text-gray-600 ml-auto">{igniting ? "…" : "Waiting…"}</span>
                  </li>
                ))}
              </ol>
```
with:
```jsx
              <ol className="space-y-3.5">
                {STAGES.map((stage, i) => {
                  const StageIcon = stage.Icon;
                  return (
                    <li key={stage.id} className="flex items-center gap-3 text-sm text-gray-400">
                      <span
                        className={`pipeline-node-dot w-5 h-5 rounded-full flex items-center justify-center bg-violet-300/15 text-violet-300 shrink-0${igniting ? " igniting" : ""}`}
                        style={{ "--node-delay": `${i * 420}ms`, "--ignite-delay": `${i * 65}ms` }}
                        aria-hidden="true">
                        <StageIcon size={11} />
                      </span>
                      <span>{stage.label}</span>
                      <span className="text-xs text-gray-600 ml-auto">{igniting ? "…" : "Waiting…"}</span>
                    </li>
                  );
                })}
              </ol>
```

- [ ] **Step 2: Verify the build**

Run (from `frontend/`): `npm run build`
Expected: exits 0, no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/NewProject.jsx
git commit -m "Show per-stage icons in the pre-submit AI Pipeline preview card"
```

---

### Task 4: Sustained scenery boost context

**Files:**
- Modify: `frontend/src/components/SceneryBoost.jsx` (whole file, 33 lines)
- Modify: `frontend/src/components/Scenery.jsx:16`

**Interfaces:**
- Consumes: nothing new
- Produces: `useSceneryBoost()` now returns `{ boosted, boost, sustained, setSustained }` — `setSustained` is consumed by Task 2's `ProjectDetail.jsx` wiring (already written assuming this shape)

- [ ] **Step 1: Replace `frontend/src/components/SceneryBoost.jsx`**

Replace the whole file with:

```jsx
import React from "react";

// Lets a routed page (e.g. NewProject's Forge-press wow moment) briefly
// intensify the globally-mounted Scenery backdrop, without Scenery needing
// to know who asked. Mirrors the Veil.jsx context pattern: a provider held
// above both the trigger and the consumer, one hook to read the flag.
//
// Two independent intensity flags:
// - `boosted` / `boost()`: a punchy one-shot flash (~900ms), e.g. the
//   Forge-press moment.
// - `sustained` / `setSustained(bool)`: a gentler, persistent elevation
//   held for as long as the caller wants (e.g. the whole duration of an
//   active generation in ProjectDetail.jsx). The two compose in CSS --
//   see .scenery-layer.boosted / .sustained in index.css.

const SceneryBoostContext = React.createContext(null);
export const useSceneryBoost = () => React.useContext(SceneryBoostContext);

const BOOST_MS = 900; // covers the 550ms veil-cover window plus an ease-out tail

export function SceneryBoostProvider({ children }) {
  const [boosted, setBoosted] = React.useState(false);
  const [sustained, setSustained] = React.useState(false);
  const timer = React.useRef(null);

  React.useEffect(() => () => clearTimeout(timer.current), []);

  const boost = React.useCallback(() => {
    setBoosted(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setBoosted(false), BOOST_MS);
  }, []);

  const api = React.useMemo(
    () => ({ boosted, boost, sustained, setSustained }),
    [boosted, boost, sustained]
  );

  return (
    <SceneryBoostContext.Provider value={api}>
      {children}
    </SceneryBoostContext.Provider>
  );
}
```

- [ ] **Step 2: Update `frontend/src/components/Scenery.jsx`**

Change line 16 from:
```jsx
    <div className={`scenery-layer${boost?.boosted ? " boosted" : ""}`} aria-hidden="true">
```
to:
```jsx
    <div className={`scenery-layer${boost?.boosted ? " boosted" : ""}${boost?.sustained ? " sustained" : ""}`} aria-hidden="true">
```

- [ ] **Step 3: Verify the build**

Run (from `frontend/`): `npm run build`
Expected: exits 0, no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/SceneryBoost.jsx frontend/src/components/Scenery.jsx
git commit -m "Add a sustained (persistent) scenery intensification flag alongside the existing one-shot boost"
```

---

### Task 5: Recent Projects status badges + stronger stagger (`Dashboard.jsx`)

**Files:**
- Modify: `frontend/src/pages/Dashboard.jsx:10-16` (the `STATUS_DOT` constant)
- Modify: `frontend/src/pages/Dashboard.jsx:196-245` (the job row rendering)

**Interfaces:**
- Consumes: nothing new
- Produces: nothing consumed elsewhere in this plan

- [ ] **Step 1: Replace the `STATUS_DOT` constant**

Replace:
```js
const STATUS_DOT = {
  pending:   "#facc15",
  running:   "#818cf8",
  done:      "#4ade80",
  error:     "#f87171",
  cancelled: "#9ca3af",
};
```
with:
```js
// Colored pill badge per status -- mirrors the status-pill visual
// language already used in ProjectDetail.jsx so the two pages agree.
const STATUS_BADGE = {
  pending:   { bg: "rgba(250,204,21,0.12)",  color: "#facc15", border: "rgba(250,204,21,0.25)" },
  running:   { bg: "rgba(99,102,241,0.12)",  color: "#818cf8", border: "rgba(99,102,241,0.25)" },
  error:     { bg: "rgba(239,68,68,0.12)",   color: "#f87171", border: "rgba(239,68,68,0.25)" },
  cancelled: { bg: "rgba(156,163,175,0.12)", color: "#9ca3af", border: "rgba(156,163,175,0.25)" },
};
```

- [ ] **Step 2: Replace the job row status span and stagger delay**

Replace:
```jsx
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
```
with:
```jsx
                <div key={job.id}
                  className="anim-fade-up hover-lift flex items-center gap-4 glass-panel rounded-xl px-4 py-3.5 hover:border-white/15 group cursor-pointer"
                  style={{ "--d": `${Math.min(i, 8) * 60}ms` }}
                  onClick={() => navigate(`/projects/${job.id}`)}>
                  {isDone && job.forge_score != null ? (
                    <span className="text-xs font-medium px-2.5 py-1 rounded-full shrink-0 w-24 text-center"
                      style={{background: `${gradeColor}20`, color: gradeColor, border: `1px solid ${gradeColor}40`}}>
                      ✓ {job.forge_score} · {grade}
                    </span>
                  ) : (
                    <span className="flex items-center justify-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full shrink-0 w-24"
                      style={{
                        background: (STATUS_BADGE[job.status] || STATUS_BADGE.error).bg,
                        color: (STATUS_BADGE[job.status] || STATUS_BADGE.error).color,
                        border: `1px solid ${(STATUS_BADGE[job.status] || STATUS_BADGE.error).border}`,
                      }}>
                      {isActive && <span className="live-dot" style={{width:5, height:5, background:"currentColor", borderRadius:9999}} aria-hidden="true" />}
                      {job.status}
                    </span>
                  )}
```

Leave the rest of the row (the `<p>` idea text, timestamp, Fix/Delete buttons, closing `</div>`) exactly as-is.

- [ ] **Step 3: Verify the build**

Run (from `frontend/`): `npm run build`
Expected: exits 0, no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Dashboard.jsx
git commit -m "Give Recent Projects rows colored status badges and a stronger stagger entrance"
```

---

### Task 6: End-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Build the frontend**

Run (from `frontend/`): `npm run build`
Expected: exits 0.

- [ ] **Step 2: Start the app**

```bash
cd backend
$env:PYTHONIOENCODING = "utf-8"
.\venv\Scripts\activate
uvicorn main:app --reload
```
Confirm `http://localhost:8000` serves the built frontend.

- [ ] **Step 3: Playwright pass — Dashboard**

Navigate to `/dashboard`. Confirm:
- Recent Projects rows show a colored pill badge (not a plain dot) whose color matches status (amber=pending, indigo=running w/ live-dot, red=error, gray=cancelled, green/yellow/red=done by grade).
- Rows fade up with a visibly staggered delay.

- [ ] **Step 4: Playwright pass — Generation screen**

Navigate to `/new`. Confirm the AI Pipeline preview card shows a distinct icon per stage (Lightbulb, LayoutTemplate, Server, MonitorSmartphone, ShieldCheck, Play, Rocket, Flag), still breathing at idle.

- [ ] **Step 5: Playwright pass — live pipeline stepper**

Open an existing job's detail page (`/projects/:id`) — pick one already `done` and, if one exists, one `error` from the current jobs list (check via the Dashboard first; use an existing job rather than spending new LLM credits on a fresh run if a suitable one already exists). Confirm:
- Completed stages show a checkmark (not the stage icon).
- Waiting stages show their distinct icon, dim/breathing.
- An `error` job shows the failed stage in red — trigger the shake by observing a fresh error transition if possible, or confirm the CSS class is present via devtools if no live error transition is available to watch.
- Connector segments before a completed stage are visibly filled (green), segments after are not.
- If a job is still active, confirm the ambient `<Scenery>` backdrop is subtly brighter than on Dashboard, and log lines animate in as they stream via the WebSocket.

- [ ] **Step 6: Reduced-motion check**

In Chrome DevTools, enable "Emulate CSS prefers-reduced-motion: reduce" (Rendering tab). Reload `/new` and a `/projects/:id` page. Confirm no animation-related jank: stagger entrances, connector fills, shake, and completion pop all render in their end state instantly with no motion.

- [ ] **Step 7: Fix any issues found, then final commit if changes were needed**

If Steps 3-6 surface any visual bug, fix it in the relevant file from Tasks 1-5, re-run `npm run build`, and commit:
```bash
git add -A
git commit -m "Fix visual issues found during end-to-end verification of the pipeline gap-closure"
```
If nothing needed fixing, no commit is required for this task.

---

## Self-Review Notes

- **Spec coverage:** Gap 1 (icons) → Tasks 1-3. Gap 2 (shake) → Task 2. Gap 3 (celebration) → Task 2. Gap 4 (connector fill) → Task 2. Gap 5 (sustained particles) → Tasks 2 (wiring) + 4 (context). Gap 6 (log entrance) → Task 2. Gap 7 (status badge + stagger) → Task 5. All 7 gaps covered; Task 6 verifies all of them together.
- **Placeholder scan:** no TBD/TODO; every step has complete, exact code.
- **Type/name consistency:** `STAGES[i].Icon` (Task 1) is the name used identically in Tasks 2 and 3. `useSceneryBoost()` returning `{ boosted, boost, sustained, setSustained }` (Task 4) matches exactly how Task 2 calls `sceneryBoost?.setSustained(...)`. CSS class names (`stepper-node-complete`, `stepper-node-shake`, `stepper-connector--h/--v`, `stepper-connector__fill`, `log-line-in`, `.scenery-layer.sustained`) are defined once in Task 2's CSS step and referenced with matching spelling everywhere else.
