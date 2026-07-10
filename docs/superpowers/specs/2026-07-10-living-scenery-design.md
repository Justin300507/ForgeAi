# Living Scenery — Phase 1 of the V18 Scenery Roadmap

**Date:** 2026-07-10
**Scope:** ForgeAI's own web app (frontend/), the shared `<Scenery />` component
(`frontend/src/components/Scenery.jsx`) introduced by the
[2026-07-10 Continuous World App Shell redesign](2026-07-10-continuous-world-app-shell-design.md).
Extends that component in place; does not replace it or change its mount point,
props, or the four pages it already renders behind (Dashboard, New App, Project
Detail/generation workspace, Deploy Keys).

**Goal:** Turn the static blurred backdrop into a barely-noticeable living scene —
slow drifting mist, a soft moving light, and a few twinkling dust/firefly motes —
so the app "feels alive" without the user consciously registering motion. This is
Phase 1 of a four-phase roadmap the user sketched (Phase 2: cursor-reactive
window/glass tilt; Phase 3: generation-stage-driven scenery reactions; Phase 4:
route/state-driven dynamic lighting) — those phases are explicitly out of scope
here and will each get their own spec once this one ships and proves out the
approach.

## Decisions locked via brainstorming Q&A

- **Technical approach**: hybrid CSS-only. No `<canvas>`, no `requestAnimationFrame`
  loop, no per-frame JavaScript of any kind after mount. Mist and light are pure
  `@keyframes` animations on `transform`/`opacity`. Particles get their
  position/delay/duration randomized once at mount time (via `useMemo`, not
  `useState`, so the randomization never recomputes on re-render) and are then
  driven entirely by CSS `@keyframes` — no ongoing JS cost. This was chosen over
  a full canvas particle system specifically because the original Scenery
  architecture chose a static image *for* zero decode/CPU cost behind the
  generation workspace's live WebSocket log stream, and a real render loop would
  reintroduce that exact risk.
- **Runs everywhere, same intensity**: unlike the original static image (whose
  cost profile made the generation workspace a special case worth reasoning
  about), GPU-compositor-only CSS animation is cheap enough that scaling it back
  on one page isn't worth the added complexity. All four authenticated pages get
  identical treatment — preserves the "same world, further inside it" consistency
  the original Scenery work was built to establish.
- **"Extremely slow parallax" is autonomous, not cursor-driven**: the drift in
  this phase is self-running (different layers moving at slightly different
  speeds for a sense of depth), with zero pointer-event listeners. Cursor-reactive
  movement is explicitly Phase 2's concern (bundled with the glass-tilt/reflection
  work, where mouse tracking already has to exist) — not duplicated here.
- **Particle count is fixed at 12**, not randomized, so the smoke-test suite can
  assert an exact count. Only each particle's position/timing is randomized.
- **Reduced motion**: elements always render; only their animations are gated
  behind `@media (prefers-reduced-motion: no-preference)`. Under
  `prefers-reduced-motion: reduce`, mist/light sit static at their base position
  and particles render as still, non-twinkling dots — never removed outright,
  degrades toward (but not fully back to) the previously-shipped fully-static
  look.

## Architecture

### Layer stack (extends the existing 3-layer stack, does not reorder it)

`Scenery.jsx`'s existing layers (image → scrim → frame) are unchanged. Three new
layers are appended, in this order, all inside the same `.scenery-layer` fixed
`z-index: -1` container:

1. **`.scenery-mist`** (new): two large, heavily-blurred (`filter: blur(60px)`)
   radial-gradient blobs, `opacity` in the 0.06–0.10 range, each animated via its
   own `@keyframes` rule moving `transform: translate(...)` slowly across the
   viewport plus a gentle opacity breathe, on a 60–90s loop (the two blobs use
   different durations so they never visibly synchronize). Rendered as two
   separate `<div>`s with distinct class modifiers (`.scenery-mist--a`,
   `.scenery-mist--b`) so their keyframe animations can differ.
2. **`.scenery-light`** (new): a single soft warm/violet radial glow, opacity
   0.08–0.12, drifting position via `@keyframes` animating `transform` on a
   ~120s loop — one source, not scattered, to stay "barely noticeable" rather
   than busy.
3. **`.scenery-particles`** (new component, `frontend/src/components/SceneryParticles.jsx`):
   renders exactly 12 `<span class="scenery-particle">` elements. A `useMemo`
   generates each particle's `{ left, top, delay, duration, size, warm }` once
   per mount (`warm: true` for ~3 of the 12, rendered slightly larger and
   amber-tinted as "fireflies"; the rest are small and cool-white as "dust").
   Each particle's inline `style` sets its randomized `left`/`top`/
   `animation-delay`/`animation-duration` custom properties; the actual drift
   (`translateY` upward over the particle's lifetime) and twinkle (opacity
   pulse) come from a single shared `@keyframes` rule in `index.css`, not
   per-particle inline keyframes.

### Component structure

`Scenery.jsx` imports and renders `<SceneryParticles />` as its new fourth+
child, after the existing `frame`/`scrim` divs. `SceneryParticles.jsx` is a new,
single-responsibility file (randomization + markup only, no props, no external
state) — kept separate from `Scenery.jsx` rather than inlined, since it has its
own `useMemo` logic distinct from the purely-presentational mist/light divs.

### CSS additions (`frontend/src/index.css`)

New rules for `.scenery-mist--a`, `.scenery-mist--b`, `.scenery-light`,
`.scenery-particle` (base positioning/sizing) and their `@keyframes` (drift,
breathe, twinkle). All animation `animation-*` properties for these four
classes are declared only inside `@media (prefers-reduced-motion: no-preference)`;
outside that media query the elements still receive their base positioning/
opacity/size (static, unanimated) so nothing is ever hidden for reduced-motion
users — motion is what's removed, not the elements themselves.

## Error handling / edge cases

- No new failure modes: these layers have no data dependency (unlike the
  captured JPEG, which has an explicit fallback chain already). If CSS fails to
  load, the elements simply render invisibly (transparent divs), same
  degradation path as any other pure-CSS decoration.
- `prefers-reduced-motion: reduce`: covered above — animations suppressed,
  elements remain visible in their static base state.
- No interaction surface (no click handlers, no pointer listeners) — `aria-hidden`
  is inherited from the parent `.scenery-layer` container already, no additional
  accessibility work needed beyond the reduced-motion handling.

## Testing

- New `backend/scripts/frontend_smoke/smoke_scenery_living.py`:
  - Exactly 12 `.scenery-particle` elements present on `/dashboard` (and spot-checked
    on one other authenticated page to confirm the shared component, not
    re-verifying all four in depth — that's already covered by the existing
    `smoke_scenery.py`).
  - `.scenery-mist--a`, `.scenery-mist--b`, `.scenery-light`, and at least one
    `.scenery-particle` each report a non-`"none"` computed `animationName`
    under default media conditions.
  - `page.emulate_media(reduced_motion="reduce")` then re-check: the same
    elements report `animationName: "none"` (or `animation-duration: "0s"`,
    whichever the implementation actually produces — the test asserts whichever
    is true, not both) — confirming the reduced-motion gate actually suppresses
    motion.
  - No console errors.
  - A direct screenshot read (not just computed-style assertions) to visually
    confirm the effect reads as "barely noticeable," not busy or distracting —
    the same manual-judgment step used throughout the parent Scenery work.
- Re-run all five existing smoke scripts (landing/workspace/internal-veil/
  scenery/dashboard-redesign) to confirm zero regression — this phase only adds
  new elements/CSS, never removes or reorders the existing three layers.
