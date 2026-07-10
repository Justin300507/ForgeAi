# Continuous World — App Shell Redesign

**Date:** 2026-07-10
**Scope:** ForgeAI's own web app (frontend/), authenticated pages only (Dashboard, New App,
the generation workspace/Project Detail, Deploy Keys). Not the generated-app theme kit
([2026-07-09 design](2026-07-09-frontend-motion-theme-kit-design.md)) — different surface.
**Goal:** Close the gap between the cinematic landing page and a dashboard that currently
reads as a generic analytics SaaS. The user's framing: "the dashboard should feel like
you've stepped through that window and started building," not like leaving ForgeAI for a
different product.

## Diagnosis

Two redesign cycles already landed this session (commits `9322a92`, `71ec4d9`, `aee429d`,
`9826a13`, `69929ef`): a twilight CSS gradient + aurora glow background, a cinematic veil
route transition, and a split-pane generation workspace. `69929ef` specifically fixed a
bug where the warm glow rendered underneath NavBar's opaque backdrop and was invisible —
found and confirmed via direct before/after screenshot comparison of the live app, not
code inspection alone. Even with that fix live, a follow-up screenshot review showed the
dashboard still reading as "generic dark-purple SaaS."

The user's follow-up identifies the deeper issue isn't color tuning — it's that the
**destination page tells a different story than the landing page**, on two axes:

1. **Content/IA**: stat cards ("50 Apps Built", "79/100 Score") are dashboard-analytics
   furniture that doesn't help someone build an app. The landing page's whole pitch is
   "describe what you imagine" — the destination page should keep that as the hero, not
   bury it under metrics.
2. **Visual world**: a CSS gradient, however tuned, is not "the same landscape, just
   further inside it." The landing's actual photographic/cinematic scenery needs to
   persist (blurred, darkened) rather than being replaced by an abstraction of it.

## Decisions locked via brainstorming Q&A

- **Scope**: all four authenticated pages get the new background treatment. The
  generation workspace's just-shipped split-pane layout and glass panels are UNCHANGED —
  only the background underneath them swaps.
- **Background mechanism**: a single **static blurred still frame** captured from the
  landing's Golden Hour scene (not a live video, not a multi-scene cycle) — zero decode
  cost, safe to run behind the log-heavy generation workspace without competing with
  WebSocket/polling for CPU.
- **Window framing**: kept, but subtle (heavier blur, lower opacity than the landing's
  version) — a hint of "still behind glass," not literal glass.
- **Transition mechanics**: keep the existing opaque veil (already shipped, already
  tested) rather than building a persistent-DOM/shared-element morph transition. The veil
  already fully hides the route swap, so "no flash" is satisfied either way; the
  difference the user wants is that what's *revealed* now matches the world they left.
- **Dashboard IA**: stat cards removed entirely. Prompt-as-hero, unchanged data flow.
  New **Templates** row (quick-fill chips) above the project list, reusing the pattern
  New App's example chips already use. Project list restyled quieter (see below) but
  loses zero functionality.
- **Status/actions preserved**: current pill badges + always-visible Fix/Delete buttons
  become a small colored status dot + word (`● running`, `✓ 90 · A`, `✕ error`), with
  Fix/Delete revealed on hover instead of always-visible. "Delete all" becomes a small
  text link. No behavior removed — purely a visual quieting.

## Architecture

### A. Capturing the still frame (one-time asset, not a runtime dependency)

The landing's scenes are external CloudFront MP4s (`frontend/src/lib/cinematic.js`).
Rather than depend on that external URL at runtime for a background image (adds a
network dependency + no simple way to extract "a frame" from a video via CSS), capture
one frame from the Golden Hour video once, save it as a static asset checked into the
repo (`frontend/src/assets/scenery-golden-hour.jpg`), and reference it locally. This
matches how `OVERLAY_PNG` already works (external Figma URL only for the *animated*
overlay currently in use) — but a captured still is more reliable than depending on the
CDN video for a *background* that must render behind every app page including on slow
connections.

Capture method: headless browser loads the video URL, seeks to a representative
timestamp (a few seconds in, once initial motion settles), screenshots just the video
element's bounding box, saved as JPEG (photographic content compresses far better as
JPEG than PNG). This is a one-time build step, not part of the app's runtime.

### B. `<Scenery />` component (new, shared)

New file: `frontend/src/components/Scenery.jsx`. Renders once, mounted in `App.jsx`
alongside `VeilProvider` (sibling to `Routes`, not per-page) so it isn't recreated on
every navigation — though since it's a static image (no video state to preserve), this
is about avoiding a flash-of-unstyled-background on route change more than genuine DOM
persistence. Layer stack (bottom to top), all `position: fixed`, `pointer-events: none`,
`z-index` below page content:

1. `--ink-bg` base color (existing, unchanged) — fallback if the image fails to load.
2. The captured still (`background-image`, `background-size: cover`), rendered on an
   element with `filter: blur(28px)` and `transform: scale(1.08)` (the scale hides the
   blur's edge artifacts) — blur applied to this dedicated layer only, never to content.
3. A dark overlay (`rgba(9, 6, 13, 0.88)` flat, tuned by eye against the actual image
   once captured — target is "reads as atmosphere, not a photo," roughly matching the
   user's "darken to 90%" instruction) between the image and the aurora glows.
4. The window-frame PNG (`OVERLAY_PNG` from `cinematic.js`, reused), blurred more
   heavily than the landscape (e.g. `blur(40px)`) and at reduced opacity (~0.25) so it
   reads as a soft framing cue at the edges, not visible glass smudge.
5. The existing warm/violet aurora radial-gradients (from the `69929ef` fix), opacity
   reduced slightly from their current values since the photographic still now carries
   most of the warm-sky color itself — exact values tuned visually once layers 2-4 are
   in place, verified via screenshot comparison (same method used to catch the
   under-navbar bug).

`.app-shell` (used by all four authenticated pages today) stops rendering its own
gradient background-image and instead just provides the content-layer positioning
(`position: relative`, scrolling, `z-index: 1` stacking context) — `<Scenery />` supplies
everything visual. This is a single shared implementation instead of four pages each
carrying their own copy of the gradient CSS.

### C. Dashboard content changes (`frontend/src/pages/Dashboard.jsx`)

- Remove the three-stat-card grid (`jobs.length` / `done.length` / `avgScore`) entirely.
- Add a `TEMPLATES` constant (SaaS / CRM / Habit Tracker / AI Agent — four strings,
  mirroring `NewProject.jsx`'s existing `EXAMPLES` array pattern) rendered as a chip row
  directly below the hero prompt form. Clicking a chip sets the idea textarea's value
  (same `setIdea` used by the existing prompt form) — no navigation, no new state.
- Restyle the project list row:
  - Replace `STATUS_STYLE` pill badges with a small dot (reuse `.live-dot` for
    active states) + plain-text status word in the row's leading position.
  - `ScoreBadge` renders inline next to the status word for `done` jobs instead of in
    its own right-aligned column.
  - Fix/Delete buttons: `opacity-0 group-hover:opacity-100 transition-opacity` (the row
    already has `group` for its hover-lift styling) instead of always visible.
  - "Delete all" becomes a plain text link (small, muted) instead of a bordered pill
    button, unless jobs are running (existing guard logic unchanged).
- No changes to `fetchJobs`, polling interval, delete/retry/cancel handlers, or routing
  — this is styling and information-density only, zero data-flow changes.

### D. Other three pages (New App, Project Detail, Deploy Keys)

No content/IA changes. Each page's root `.app-shell` wrapper picks up the new shared
background automatically once `<Scenery />` replaces the CSS-gradient approach — no
per-page edits needed beyond confirming nothing currently assumes an opaque
`.app-shell` background (e.g. any text relying on background contrast should already be
using `.glass-panel`/`.workspace-shell`, which sets its own background — confirmed true
for all three pages from this session's earlier work).

## Error handling / edge cases

- If the captured JPEG fails to load (404, slow network): `--ink-bg` solid color remains
  as the base layer regardless (layer 1 is independent of the image loading), so the app
  never shows a broken-image icon or transparent background — same defensive pattern
  `OVERLAY_PNG`'s `onError` handler already uses on Landing.
- `prefers-reduced-motion`: no new animation is introduced by this change (the
  background is static, not cycling) — nothing to gate beyond what's already handled.
- Existing job-list empty/loading states (skeleton, "The anvil is quiet") are unaffected
  by the stat-card removal or list restyle — same conditional rendering, different
  row markup only.

## Testing

- Rebuild + screenshot comparison (the method that caught the `69929ef` bug) before/after
  for all four authenticated pages, confirming the blurred scenery is visibly present and
  not hidden behind any opaque element (repeat the navbar-occlusion check specifically).
- Extend the existing Playwright suites (`smoke_workspace.py`, `smoke_internal_veil.py`)
  rather than replace them: assert stat cards are gone from Dashboard, template chips
  exist and fill the textarea on click, Fix/Delete are hover-only (hidden at rest,
  visible on `:hover` — checked via computed opacity, not literal mouse simulation which
  is flakier), and the veil/navigation checks continue passing unchanged (this redesign
  doesn't touch `Veil.jsx` itself).
- No backend/LLM calls required for any of this verification (same mocked-API approach
  used throughout this session).
