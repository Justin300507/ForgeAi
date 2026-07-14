// Generated media for the landing page's forge sections. Every visual
// asset the scroll journey uses is wired through this one manifest —
// swap a URL here and the page picks it up everywhere.
//
// Stills live in frontend/public/forge/, generated for free via
// Pollinations (scratchpad fetch script, fixed seed for a consistent
// silhouette across the 8 stage states). All renders sit on pure black:
// the Three.js slab shaders luma-key that black to transparency, so no
// background-removal pass is needed. Any `src: null` entry falls back to
// a procedural stand-in (canvas texture / CSS gradient) so the page
// never renders broken while an asset is being regenerated.

// ── The forge object: one silhouette, eight states ─────────────────────
// Order MUST match STAGES in lib/pipelineStages.js (the product's real
// pipeline naming) — the scroll morph indexes into this array directly.
export const STAGE_TEXTURES = [
  { id: "plan",     src: "/forge/stage_plan.jpg" },     // glowing spark / raw idea
  { id: "arch",     src: "/forge/stage_arch.jpg" },     // wireframe blueprint lines forming
  { id: "backend",  src: "/forge/stage_backend.jpg" },  // structural lattice solidifying
  { id: "frontend", src: "/forge/stage_frontend.jpg" }, // surface/skin rendering over the lattice
  { id: "validate", src: "/forge/stage_validate.jpg" }, // light sweep / scan pass
  { id: "runtime",  src: "/forge/stage_runtime.jpg" },  // powered on, steady glow
  { id: "deploy",   src: "/forge/stage_deploy.jpg" },   // launching / releasing upward
  { id: "done",     src: "/forge/stage_done.jpg" },     // polished complete UI, calm light
];

// Accent color arc across the forge: cool blue-white while the idea is
// still thought, warming through amber at the strike, settling into a
// calm resolved glow. Synced to the background + stage label live.
export const STAGE_COLORS = [
  { id: "plan",     accent: "#9db8ff", glow: "rgba(157, 184, 255, 0.22)" },
  { id: "arch",     accent: "#8fd0ff", glow: "rgba(143, 208, 255, 0.20)" },
  { id: "backend",  accent: "#b49aff", glow: "rgba(180, 154, 255, 0.22)" },
  { id: "frontend", accent: "#e8a2b5", glow: "rgba(232, 162, 181, 0.22)" },
  { id: "validate", accent: "#ffb457", glow: "rgba(255, 180, 87, 0.24)" },
  { id: "runtime",  accent: "#ff8a3d", glow: "rgba(255, 138, 61, 0.26)" },
  { id: "deploy",   accent: "#ffa96b", glow: "rgba(255, 169, 107, 0.22)" },
  { id: "done",     accent: "#7ee0b0", glow: "rgba(126, 224, 176, 0.20)" },
];

// One-line honest descriptions of what each stage actually does — kept
// in the marketing voice but true to the real V15 pipeline.
export const STAGE_COPY = {
  plan:     "A product-manager agent breaks your sentence into stories, features, and entities.",
  arch:     "Endpoints, schemas, and contracts are drawn before a line of code exists.",
  backend:  "Models, routes, and a live database generated in parallel.",
  frontend: "A themed React interface skins the lattice — responsive, animated, alive.",
  validate: "Static checks and a tech-lead review sweep every generated file.",
  runtime:  "The app boots for real: endpoints smoke-tested, user journeys clicked through.",
  deploy:   "Pushed to GitHub, released to the edge.",
  done:     "A living application, Forge Score attached.",
};

// ── Shipped example mockups — the three real deployed apps ─────────────
export const EXAMPLE_MOCKUPS = [
  { id: "fitness", name: "Fitness Tracker",            src: "/forge/mock_fitness.jpg" },
  { id: "library", name: "Library Management System",  src: "/forge/mock_library.jpg" },
  { id: "habit",   name: "Habit Tracker",              src: "/forge/mock_habit.jpg" },
];

// ── Ambient / background media ─────────────────────────────────────────
// (Ember particles and the forge-strike sequence are procedural Three.js
// — no sprite media needed.)
export const BG_TEXTURES = {
  blueprintGrid: "/forge/bg_blueprint.jpg", // abstract blueprint-grid backdrop
  moltenLight: "/forge/bg_molten.jpg",      // molten-light gradient backdrop
};
