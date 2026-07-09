# Forge Motion & Theme Kit — Frontend Generation Design

**Date:** 2026-07-09
**Scope:** Frontend generation quality only (per V16 RC1 brief). No backend, validation, or repair changes.
**Goal:** Every generated app ships with a premium, animated, category-themed design system — deterministically, not "if the LLM remembers".

## Diagnosis (from studying the generator + real output)

1. **Polish is LLM-applied, so it's inconsistent.** The prompt *asks* for glass surfaces,
   entrance animations, hover lifts — and the model applies them unevenly. Measured in
   `generated_projects/todo_list_app`: `DashboardPage` has 8 glass-surface markers,
   `UsersPage` has 0; Login/Register/Users pages have no entrance animation at all.
2. **The motion system is one keyframe.** `src/index.css` defines only `fadeIn`. No
   scale-in for dialogs/toasts, no shimmer for skeletons, no spring/pop for success
   feedback, no ambient motion. "Skeletons" are flat gray `animate-pulse` boxes.
3. **Style-pack fonts are a dead feature.** `style_system.py` tells the LLM to add a
   Google Fonts `@import` — but `src/index.css` is a static template the LLM is
   forbidden from regenerating, and external CSS files are banned. Every app renders
   in Inter regardless of assigned style.
4. **The static scaffold ignores the detected category.** `tailwind.config.js` carries a
   hardcoded indigo `primary` palette; `index.html` says "ForgeAI App" with an indigo
   theme-color for every app.
5. **No error boundary.** Any runtime render error white-screens the entire app — the
   worst possible first impression.

## Design

Move the *foundation* of the design system out of the LLM's hands and into
deterministic Python-rendered scaffolding; keep the LLM responsible only for
composing pages *with* the system. Three parts:

### A. `theme_builder.py` — per-app themed scaffold (new module)

`build_themed_templates(idea, project_name, frontend_target)` renders three files
from the same category (`design_system.detect_category`) and style
(`style_system.select_style`) the frontend prompt already uses (same idea string →
identical selection):

- **`src/index.css`** — style font imports made real (fixes the dead feature); brand
  CSS variables from category hexes; a motion token library (fade-in, fade-in-up,
  scale-in, slide-in-right, shimmer, pop w/ spring overshoot, ping, gradient-pan,
  float-slow/float-slower); component classes `.skeleton` (shimmer sweep),
  `.live-dot` (pulsing live indicator), `.gradient-animated` (slow gradient pan).
  Existing `.card`/`.btn-*`/`.input`/`.badge` classes and the `fadeIn` keyframe kept
  verbatim (backward compatible with `animate-[fadeIn_0.3s_ease-out]`). The
  `prefers-reduced-motion` kill-switch stays and covers all new animation.
- **`tailwind.config.js`** — named animation utilities (`animate-fade-in-up`,
  `animate-scale-in`, …) via `theme.extend.keyframes/animation`; per-style
  `fontFamily` (body + display); category color mapped to a `brand` scale.
- **`index.html`** — real app title, category theme-color, style's Google Fonts link
  (link tag, not @import — non-blocking). Applied to both web and PWA variants.

All animations are transform/opacity only (60fps, no layout thrash).

### B. ErrorBoundary — deterministic, zero LLM coordination

Static `src/components/ErrorBoundary.jsx` (class component, theme-aware premium
fallback with a reload button) added to the template map and wrapped around
`<App/>` inside the static `main.jsx` template. It `console.error`s the caught
error so browser-console failure detection keeps working. Safe because the
orphan-route patcher only scans `src/pages/`, and template files are written after
LLM files so they always win path collisions.

### C. Prompt upgrades — compose with the system, don't rebuild it

- New **MOTION TOKENS** section in `frontend_prompt.py`: the named `animate-*`
  utilities and `.skeleton` / `.live-dot` / `.gradient-animated` exist — use them.
  Rules: page content enters with `animate-fade-in-up`; mapped lists stagger via
  `animationDelay`; toasts/dialogs enter with `animate-scale-in` (spring pop);
  loading uses `.skeleton` shimmer blocks shaped like the content they replace;
  dashboards show a `.live-dot` live-activity indicator; ambient blobs get
  `animate-float-slow`/`animate-float-slower`.
- `design_system.py`: ambient blob example gains the float classes.
- `style_system.py`: font instruction changes from "add this @import" (impossible)
  to "the font pairing is already wired into index.css/index.html".
- Final verification checklist extended with the motion/skeleton/live-dot items.

## Rollback / risk

- `FORGE_THEMED_SCAFFOLD=0` env flag reverts to the static scaffold; the overlay is
  wrapped in try/except so a theming bug can never break generation.
- Battle-tested prompt patterns (nav-link ternary rule, toast variable extraction,
  retry loops, auth rules) are untouched.

## Verification ($0, no LLM spend)

1. Matrix test: render all 7 categories × 5 styles; assert balanced CSS braces,
   font import presence, keyframe presence, valid HTML substitutions.
2. Real build: scratchpad Vite project from the new scaffold + a sample App.jsx
   exercising the new utilities → `npm install && npm run build` must pass.
3. `build_frontend_prompt` renders for several ideas (f-string brace safety).
4. Canary (3-app) deferred to the next funded run — logged honestly in
   experiments.md.
