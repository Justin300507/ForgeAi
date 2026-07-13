# Internal Pages Cinematic Gap-Closure — Design

## Context

The user asked to restyle ForgeAI's internal pages (Dashboard, Generation
screen, pipeline visualization, Recent Projects, Deploy Keys) to match the
landing page's cinematic aesthetic. Investigation found this restyle
already shipped 2026-07-10 (commit `1f91fe0`, see
`2026-07-10-continuous-world-app-shell-design.md` and
`2026-07-09-frontend-motion-theme-kit-design.md`): persistent `<Scenery>`
atmospheric background, `hero-serif` display type, `.glass-panel` /
`.liquid-glass` surfaces, pill CTAs, a premium pill model-selector, icon
deploy-cards, a live per-stage stepper (`PipelineBar` in
`ProjectDetail.jsx`) with waiting/active/done/error states, and a styled
Deploy Accounts page (`CredentialsPage.jsx`).

This is therefore a **gap-closure**, not a rebuild. Scope is the 7 concrete
mismatches found against the user's brief, confirmed with the user
(defaulting to the recommended option on every open question per standing
preference — see `feedback_default_to_recommended` memory):

1. Pipeline nodes have no per-stage icon (currently number/✓/✕/…)
2. Error/failed state has no shake animation
3. Stage completion has no micro-celebration flourish
4. Connecting lines flat-swap color instead of animating as progress advances
5. Ambient particle intensification (`SceneryBoost`) is a single ~900ms
   flash on submit, not sustained through the whole active generation
6. Log lines append instantly with no per-line entrance motion
7. Recent Projects rows have a plain dot+text status, not a colored badge,
   and a lighter stagger entrance than the spec implies

Out of scope: card-grid relayout of Recent Projects (keep row list per
decision), a new "redeploy" action/endpoint (reuse existing Fix/Retry),
literal character-by-character log typewriter (line-by-line reveal
instead), and anything already matching the spec (landing page itself is
untouched, as instructed).

## Components

### 1. Shared stage-icon map (`frontend/src/lib/pipelineStages.js`)

Add one `lucide-react` icon per stage to the existing `STAGES` array:

| stage id | icon |
|---|---|
| plan | `Lightbulb` |
| arch | `LayoutTemplate` |
| backend | `Server` |
| frontend | `MonitorSmartphone` |
| validate | `ShieldCheck` |
| runtime | `Play` |
| deploy | `Rocket` |
| done | `Flag` |

`STAGES` becomes the single source of truth consumed by both
`NewProject.jsx` (pre-submit preview) and `ProjectDetail.jsx`'s
`PipelineBar` (live stepper), so the two can never drift, matching the
existing file's stated purpose in its header comment.

### 2. Pipeline node states (`PipelineBar` in `ProjectDetail.jsx` + preview in `NewProject.jsx`)

- **Waiting**: stage icon at low opacity, existing `pipeline-node-dot`
  breathing animation kept (now wraps an icon instead of a bare dot).
- **Active**: existing `stepper-node-active` neon-emerald pulse kept;
  icon swaps to a `Loader2` spin (matches existing `Loader2` use
  elsewhere in the codebase) layered behind/beside the stage icon.
- **Completed**: existing green ✓ treatment, plus a new
  `stepper-node-complete` one-shot keyframe (scale 1 → 1.25 → 1, brief
  brightness pop, ~450ms) that fires once when a stage's `past` flag
  flips true — the "micro-celebration." Implemented as a CSS animation
  gated the same way `pipeline-node-ignite` already is (class added
  briefly via a small `useEffect` diffing previous vs. current
  `activeIdx`, same pattern already used for `igniting`).
- **Failed**: new `stepper-node-shake` keyframe (small horizontal
  translate wobble, ~400ms, 3 cycles) added to the existing red ✕
  treatment, gated behind `prefers-reduced-motion` like every other
  animation in `index.css`.

### 3. Connecting lines

Replace the instant `background` color swap with a left-to-right animated
fill: each connector becomes a 2-layer element (base track + a fill
overlay whose `width`/`transform: scaleX()` animates 0→1 over ~600ms
`cubic-bezier(0.16, 1, 0.3, 1)` — the same easing curve already used
throughout `index.css` — when its preceding stage completes). No new
animation library needed; pure CSS transition on `transform`.

### 4. Sustained scenery intensification

Extend `SceneryBoost.jsx`'s existing context (currently only a one-shot
`boost()` / `boosted` flag) with a second, independent `sustained` flag:

```js
const [sustained, setSustained] = React.useState(false);
// api: { boosted, boost, sustained, setSustained }
```

`ProjectDetail.jsx` calls `setSustained(true)` when `job.status` is
`pending`/`running` and `setSustained(false)` on done/error/cancel/unmount.
`index.css` gets a new `.scenery-layer.sustained` rule: a gentler,
persistent version of the existing `.boosted` brightness bump (e.g.
`brightness(1.18)` vs. boosted's `1.6`) plus a ~30% speed-up on the drift
animation durations — subtle enough not to distract from the log panel,
noticeable enough to read as "the machine is working." `boosted` and
`sustained` compose (boosted's punchier value wins) since both are just
CSS classes on the same element.

### 5. Log line entrance

Each `<p>` rendered per log line in `ProjectDetail.jsx` gets a new
`log-line-in` class: a ~180ms fade + 4px translateY-in animation. Because
React only mounts each `<p>` once (new lines are appended, not
re-rendered), the animation naturally plays exactly once per line with no
extra state tracking — existing lines already on screen never re-animate.
Gated under `prefers-reduced-motion` like the rest of the motion tokens.

### 6. Recent Projects status badge + stagger

`Dashboard.jsx`'s per-job status indicator (currently a small dot +
plain-text status) becomes a small pill badge (rounded-full, colored
background/border/text per status — pending=amber, running=indigo with
existing `live-dot`, done=existing score-based grade color, error=red,
cancelled=gray), reusing the same badge visual language already present
in `ProjectDetail.jsx`'s own status pill so the two pages agree. Stagger
entrance delay widened slightly (from `40ms` to `60ms` per row, capped at
the same 8-row max) so the list reads more clearly as a sequential
reveal.

## Error handling / edge cases

- All new animations respect the existing global
  `@media (prefers-reduced-motion: reduce)` block in `index.css` — shake,
  celebration-pop, connector-fill, and log-line-in all get `animation:
  none` fallbacks there, consistent with every existing motion rule in
  the file.
- `sustained` scenery state must reset on WebSocket error/close and on
  component unmount (existing `ws.onerror`/cleanup path in
  `ProjectDetail.jsx`) so a page navigation away from an in-flight
  generation never leaves the backdrop stuck intensified.
- Stage-complete/shake trigger logic uses a ref to track previous
  `activeIdx`/`status` (mirroring the existing `igniting` state pattern
  in `NewProject.jsx`) so the one-shot animations don't replay on every
  re-render triggered by unrelated log updates.

## Testing

- Manual verification via `run` skill / dev server: drive one real
  generation end-to-end (`npm run dev` + backend), observe each pipeline
  stage transition (waiting→active→done, and one forced error case),
  confirm icons render, connector fill animates, celebration pop fires
  once per stage, log lines animate in, scenery visibly (but subtly)
  brightens for the duration and recedes on completion.
- Verify `prefers-reduced-motion: reduce` in devtools disables all new
  animations without breaking layout.
- Visual check of Dashboard's Recent Projects list across all status
  values (pending/running/done-good/done-bad/error/cancelled) for badge
  color correctness.
- No backend changes in this pass, so no API/contract tests needed.
