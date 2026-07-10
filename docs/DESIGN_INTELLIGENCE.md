# ForgeAI Design Intelligence

How ForgeAI decides what a generated app should *look and feel* like, and
how it verifies the result. The goal: every generated frontend reads as if
a senior design team built it — its own identity per app, not a template
with swapped colors.

## Pipeline (agent order)

```
idea
 ├─ Product Analysis      app/design/product_analysis.py   audience, tone, posture,
 │                                                         data density, device priority
 ├─ Experience Composer   app/design/experience.py         first 30 seconds, trust,
 │                                                         delight, empty states, success
 ├─ Category axis         app/prompts/design_system.py     domain colors, icons,
 │                                                         signature components (13 categories)
 ├─ Style axis            app/prompts/style_system.py      glass / bento / neubrutalist /
 │                                                         soft clay / minimal editorial,
 │                                                         each with fonts + motion intensity
 ├─ Layout Planner        app/design/layout.py             sidebar shell vs top-nav
 │                                                         content shell (restaurant/
 │                                                         travel/portfolio)
 ├─ Inspiration Engine    app/design/inspiration.py        2-3 synthesized design
 │                                                         principles per brief
 ├─ Component Planner     app/prompts/component_library.py curated JSX patterns with
 │                                                         metadata (category/style/
 │                                                         motion/complexity/a11y)
 ├─ Design Brief          app/design/brief.py              compose_design_brief(idea)
 │                                                         → one frozen dataclass
 └─ Renderer              app/design/render.py             prompt sections appended to
                                                           the design-system injection
```

Every stage is a **deterministic pure function of the idea string** — the
same idea always composes the same brief. This is the same stability
contract `select_style()` documents: a Check & Fix re-run days later must
never reskin an app out from under its owner. It also costs $0 in LLM
calls; curated, hand-tuned catalogs beat an extra LLM call per generation
on both reliability and cost.

## The two LLM design agents

1. **Frontend Critic** (`app/services/frontend_critic_service.py`) — one
   review call after generation. Brief-aware: it knows the assigned shell
   (never flags a designed top-nav app for "missing sidebar") and the
   experience goal. Judges hierarchy, design-system compliance, polish +
   motion quality, consistency + originality. Critical/high issues feed
   the bounded UI Polish pass (max 5 files, snapshot + revert on
   regression) in `v6_orchestrator.py`.
2. **Vision Judge** (`app/runtime/vision_validator.py`) — screenshot
   scoring during runtime validation. Its rubric is layout-neutral
   (sidebar OR top nav both count as premium navigation).

## Design Memory (V19)

Every generation saves a full design record to
`failure_memory/design_fingerprints.json` (via
`app/memory/design_fingerprint.py`): design_id, category, style, layout,
navigation, motion, typography, component mix, density, hero style,
palette, interaction style, experience flow.

Before the next generation, `app/design/design_memory.py` compares the
planned record against the last 20 records:

```
similarity vs the most similar recent, DIFFERENT-idea record
    < 0.75  ->  safe — generate as planned
    >= 0.75 ->  "NEW DIRECTION REQUIRED" directive injected: two concrete,
                deterministic composition changes WITHIN the assigned
                style's rules (lead with a different signature component,
                invert accent emphasis, change the dashboard rhythm, ...)
```

Similarity is a weighted score: palette 0.20, style 0.20, typography 0.15,
component-mix Jaccard 0.15, layout 0.10, category 0.10, hero 0.05,
density 0.05.

Two contracts the memory never breaks:
1. It only shapes prompt-level variation — the deterministic
   idea→category/style/layout mapping is untouched.
2. Records of the *same idea* are excluded from similarity, so a
   Check & Fix re-run can never trigger a new direction against the app's
   own history.

## Extending the system (future agents)

`compose_design_brief()` returns a plain frozen dataclass, so new agents
plug in without refactoring:

- **Vision/screenshot critic, A/B design generation, brand-kit
  generation** — consume the brief after generation, or enrich it before
  `render_brief_sections()`.
- **New categories** — add to `CATEGORIES` (design_system.py) + one entry
  each in `_ANALYSIS` (product_analysis.py) and `_EXPERIENCES`
  (experience.py); layout defaults to sidebar unless added to
  `_TOPNAV_CATEGORIES`.
- **New components** — add to `COMPONENTS` + `COMPONENT_META`
  (component_library.py). Keep snippets brace-balanced after `.format()`
  substitution — `tests/design_intelligence/test_design_brief.py` enforces
  this for every category (the JSX-templating failure class that has
  repeatedly bitten this codebase).

## Tests

`backend/tests/design_intelligence/test_design_brief.py` — plain
assert-based, run directly:

```
cd backend && venv\Scripts\python tests\design_intelligence\test_design_brief.py
```

Covers: determinism, category detection for all 13 categories, layout
assignments, brace balance of every fenced JSX block in every category's
injection, component metadata completeness, style-aware selection, critic
shell-awareness, fingerprint recording (with and without extra dims).
