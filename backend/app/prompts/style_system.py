"""
ForgeAI Style System v1

Independent of design_system.py's CATEGORY axis (which picks a color anchor
and icon vocabulary from the app's domain), this module picks a STYLE axis
(the structural/textural treatment: glass, bento, neubrutalist, soft clay,
minimal editorial) deterministically from the idea text. Crossing 7
categories x 5 styles gives 35 distinct looks instead of one fixed "premium
glass SaaS" template for every generated app — two apps in the same category
no longer look like re-skins of each other.

The base frontend prompt (frontend_prompt.py) is written entirely in terms
of the glass style (backdrop-blur, bg-white/80, ring-1 ring-black/5,
shadow-sm hover:shadow-md). Rather than rewriting that already-tuned prompt
to be conditional per style (high risk of regressing the one style that's
actually been battle-tested), build_style_injection() returns an explicit
override block for the other four styles: keep the same structural layout
(sidebar shape, card grid, button placement, stagger/motion rules) the base
prompt already specifies, but replace its literal glass-specific classes
with this style's surface/shadow/radius/border treatment. When the selected
style IS glass, no override is needed and this returns "".

Concrete tokens (hex values, shadow/radius specs, font pairings) are drawn
from ui-ux-pro-max's style/typography database, adapted from its native
mobile/React-Native specs to plain Tailwind + React.

NOTE ON BRACES: every string below is a *plain* string, not an f-string —
none of it is evaluated by Python at import time. Single braces (e.g.
`size={16}`, `{primary_name}`) appear in the output exactly as written, so
that's what a real JSX expression / literal placeholder token looks like.
Only build_style_injection()'s own return statement is an f-string, and its
`{style[...]}` interpolations are genuine Python substitutions.
"""
import hashlib

STYLES: dict[str, dict] = {
    "glass": {
        "label": "Glassmorphism",
    },
    "bento": {
        "label": "Bento Grid",
        "mood": "Apple-style modular dashboard: varied card sizes, clean hierarchy, soft depth",
        "motion_tier": "Crisp, not slow — hover transitions at duration-150/200 (not longer), "
                        "no exaggerated overshoot. Entrance stagger stays at the base 40ms.",
        "font_import": "@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');",
        "font_heading": "'Plus Jakarta Sans', sans-serif",
        "font_body": "'Plus Jakarta Sans', sans-serif",
        "avoid": "backdrop-blur, translucent bg-white/80 surfaces, ambient blurred gradient blobs, ring-1 ring-black/5",
        "example": """
BENTO GRID STYLE — this app is assigned the Bento Grid style, not glass.
Wherever the base prompt above says to use `backdrop-blur-xl`,
`bg-white/80 dark:bg-slate-800/70`, or `ring-1 ring-black/5 dark:ring-white/5`
on a card, use this instead — a fully opaque, crisp card with a soft shadow
that EXPANDS (not just darkens) on hover, no blur, no translucency, no ring:

```jsx
<div className="bg-white dark:bg-slate-800 rounded-2xl border border-slate-100 dark:border-slate-700 p-5 shadow-[0_2px_8px_rgba(0,0,0,0.04)] hover:shadow-[0_8px_24px_rgba(0,0,0,0.08)] hover:scale-[1.02] transition-all duration-200">
  ...
</div>
```
Vary card spans in the dashboard's main stat/summary grid instead of a
uniform 4-equal-column row — mix a `col-span-2` "hero" card (e.g. the
biggest/most important metric or the chart) with regular `col-span-1` cards:
```jsx
<div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 auto-rows-[minmax(140px,auto)]">
  <div className="lg:col-span-2 lg:row-span-2 ...">{/* hero stat or chart */}</div>
  <div className="...">{/* regular stat */}</div>
  <div className="...">{/* regular stat */}</div>
</div>
```
No ambient background blobs, no gradient sidebar — the sidebar is a plain
surface matching the cards (`bg-white dark:bg-slate-900 border-r
border-slate-100 dark:border-slate-800`), with the active nav link as a
solid filled pill in the category accent color (`bg-{primary_name}-500
text-white`), not a gradient. Primary buttons are a solid
`bg-{primary_name}-600 hover:bg-{primary_name}-700` fill, not a gradient —
this style's polish comes from grid rhythm and shadow depth, not gradients.
""",
    },
    "neubrutalist": {
        "label": "Neubrutalist",
        "mood": "Flat bold color blocks, hard offset shadows, thick borders, punchy and confident",
        "motion_tier": "Snappy and mechanical, not soft — shorten every transition/active duration "
                        "shown elsewhere in this prompt to duration-100 (buttons, cards, hovers alike). "
                        "Tighten entrance stagger to 25ms per item (not the base 40ms) so lists feel "
                        "like they're clicking into place. Never use animate-pop or animate-scale-in's "
                        "springy overshoot here — use animate-fade-in-up only, it reads as more precise.",
        "font_import": "@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&display=swap');",
        "font_heading": "'Space Grotesk', sans-serif",
        "font_body": "'Space Grotesk', sans-serif",
        "avoid": "backdrop-blur, gradients, soft/blurred shadows, rounded-xl+ corners, ring-1 ring-black/5",
        "example": """
NEUBRUTALIST STYLE — this app is assigned the Neubrutalist style, not
glass. This is deliberately the most different-looking style in the
catalog: flat, bold, no blur, no gradients, no soft shadows. Wherever the
base prompt above says `backdrop-blur-xl`, `bg-white/80
dark:bg-slate-800/70`, `ring-1 ring-black/5 dark:ring-white/5`, or
`shadow-sm hover:shadow-md`, use this instead — a fully opaque card with a
thick solid border and a HARD offset shadow (no blur radius) that grows and
shifts on hover:

```jsx
<div className="bg-white dark:bg-slate-900 rounded-md border-2 border-slate-900 dark:border-slate-100 p-5 shadow-[3px_3px_0_#0f172a] dark:shadow-[3px_3px_0_#e2e8f0] hover:shadow-[5px_5px_0_#0f172a] dark:hover:shadow-[5px_5px_0_#e2e8f0] hover:-translate-x-0.5 hover:-translate-y-0.5 transition-all duration-150">
  ...
</div>
```
Corners are small (`rounded-md`/`rounded-lg`, never `rounded-xl`+). Stat
icon badges are a flat solid block in the category accent color (no
gradient tint): `bg-{primary_name}-500 text-white p-2 rounded-md` (not
`bg-gradient-to-br from-{color}-50 to-{color}-100`). Primary buttons are
a flat solid fill with the same hard-shadow treatment as cards, not a
gradient:
```jsx
<button className="px-4 py-2 rounded-md border-2 border-slate-900 dark:border-slate-100 bg-{primary_name}-500 text-white font-bold shadow-[3px_3px_0_#0f172a] dark:shadow-[3px_3px_0_#e2e8f0] hover:shadow-[4px_4px_0_#0f172a] dark:hover:shadow-[4px_4px_0_#e2e8f0] active:shadow-none active:translate-x-0.5 active:translate-y-0.5 transition-all duration-100">
  <Plus size={16} className="inline mr-1" /> Add Item
</button>
```
No ambient background blobs, no gradient sidebar/brand text — the sidebar
is `bg-white dark:bg-slate-900 border-r-2 border-slate-900
dark:border-slate-100`, brand text is bold flat color (no gradient
bg-clip-text), and the active nav link is a flat filled block in the
category accent color with the same border/shadow treatment as buttons.
Section headings can go slightly bolder/uppercase for labels
(`text-xs font-bold uppercase tracking-wide`) to lean into the style.
""",
    },
    "soft_clay": {
        "label": "Soft Clay",
        "mood": "Pastel, rounded, tactile — bouncy dual soft shadows instead of blur or hard edges",
        "motion_tier": "Bouncy and exaggerated. Loosen entrance stagger to 60ms per item (not the base "
                        "40ms) so lists feel like they're settling into place one at a time. Prefer "
                        "animate-pop over animate-scale-in for anything that appears (toasts, modals, "
                        "new badges/achievements) — its overshoot reads as more tactile/springy, matching "
                        "this style's whole point.",
        "font_import": "@import url('https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700;800&display=swap');",
        "font_heading": "'Nunito', sans-serif",
        "font_body": "'Nunito', sans-serif",
        "avoid": "backdrop-blur, hard offset shadows, sharp corners, pure white backgrounds",
        "example": """
SOFT CLAY STYLE — this app is assigned the Soft Clay style, not glass.
Wherever the base prompt above says `backdrop-blur-xl`, `bg-white/80
dark:bg-slate-800/70`, `ring-1 ring-black/5 dark:ring-white/5`, or
`shadow-sm hover:shadow-md`, use this instead — chunky, very rounded, dual
soft shadow (a dark shadow + a light highlight, no blur-filter) that feels
tactile/pressable:

```jsx
<div className="bg-white dark:bg-slate-800 rounded-3xl border border-slate-100 dark:border-slate-700 p-5 shadow-[6px_6px_16px_rgba(0,0,0,0.06),-4px_-4px_12px_rgba(255,255,255,0.7)] dark:shadow-[6px_6px_16px_rgba(0,0,0,0.3)] hover:shadow-[8px_8px_20px_rgba(0,0,0,0.08),-4px_-4px_12px_rgba(255,255,255,0.8)] transition-all duration-200">
  ...
</div>
```
Corners are large everywhere (`rounded-3xl` on cards, `rounded-2xl` on
buttons/badges/inputs — never a sharp or barely-rounded corner). The page
background itself is a very soft tint, never pure white:
`bg-{primary_name}-50/50 dark:bg-slate-900`. Buttons get an extra bouncy
press: `active:scale-90 transition-transform duration-150` (more exaggerated
than the base prompt's `active:scale-[0.97]`) so presses feel springy.
Stat icon badges keep the gradient tint from the design system above but
in a bigger, rounder badge (`rounded-2xl` not `rounded-lg`). No ambient
blurred blobs — the softness comes from the shadows and corners, not
background decoration.
""",
    },
    "minimal_editorial": {
        "label": "Minimal Editorial",
        "mood": "Swiss-grid, black/white/single-accent, oversized type, huge whitespace, near-zero chrome",
        "motion_tier": "Restrained — motion should be nearly invisible, never decorative. Use "
                        "animate-fade-in (plain opacity, no rise) instead of animate-fade-in-up for page "
                        "content. Halve entrance stagger to 20ms per item. Never use animate-pop or "
                        "animate-scale-in's spring overshoot anywhere in this style — a toast/modal here "
                        "gets a plain animate-fade-in instead. Skip the ambient float-slow/float-slower "
                        "background blobs entirely (this style avoids decorative background elements by "
                        "design) — the fixed-blob background block from the design system above does not "
                        "apply to this style.",
        "font_import": "@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700;800;900&family=Inter:wght@400;500&display=swap');",
        "font_heading": "'Archivo', sans-serif",
        "font_body": "'Inter', sans-serif",
        "avoid": "gradients, backdrop-blur, colored icon badges, decorative shadows, more than one accent color",
        "example": """
MINIMAL EDITORIAL STYLE — this app is assigned the Minimal Editorial
style, not glass. This style has almost no card chrome at all: content is
separated by whitespace and thin dividers, not boxes with shadows.
Wherever the base prompt above shows a card with `backdrop-blur-xl`,
`bg-white/80 dark:bg-slate-800/70`, `ring-1 ring-black/5
dark:ring-white/5`, and `shadow-sm hover:shadow-md`, replace ALL of it with
a borderless block separated by a divider, with a colored top border that
only appears on hover (no shadow, no elevation):
```jsx
<div className="py-6 border-b border-slate-200 dark:border-slate-800 border-t-2 border-t-transparent hover:border-t-{primary_name}-500 transition-colors duration-200">
  ...
</div>
```
Stat numbers are dramatically oversized compared to the base prompt's
`text-2xl font-bold tracking-tight` — use `text-5xl font-black
tracking-tighter` for the single most important number on a page, and drop
the icon badge entirely (or reduce to a plain small icon with no
background box at all). Use only ONE accent color total (the category's
`primary`), everywhere else pure black/white/gray — no gradients anywhere,
including the sidebar/brand text (flat `font-black` wordmark, no
bg-clip-text gradient). The page title can go very large:
`text-4xl md:text-5xl font-black tracking-tight` instead of `text-2xl
font-bold`. Section spacing should be generous — prefer `space-y-10`/`gap-10`
over the base prompt's tighter defaults; whitespace is this style's
primary decoration.
""",
    },
}


def select_style(idea: str) -> str:
    """Deterministic style pick from the idea text — the same idea always
    gets the same style (stable across "Check & Fix" re-runs on an existing
    app), while different ideas spread across the catalog."""
    keys = sorted(STYLES.keys())
    digest = hashlib.md5(idea.strip().lower().encode("utf-8")).hexdigest()
    return keys[int(digest, 16) % len(keys)]


def build_style_injection(idea: str, category_ds: dict) -> str:
    """Style override block to append after the category design-system
    injection. Empty for "glass" -- the base frontend prompt already
    describes glass, no override needed."""
    style_key = select_style(idea)
    style = STYLES[style_key]
    if style_key == "glass" or "example" not in style:
        return ""

    return f"""
═══════════════════════════════════════════════════════
STYLE OVERRIDE  ← this app uses a different style than the examples above
═══════════════════════════════════════════════════════

The base prompt's card/button/sidebar examples are written for the Glass
style. This specific app is assigned **{style['label']}** instead
({style['mood']}). Apply the overrides below on top of everything else
(layout shape, page structure, accessibility rules, JSX-safety rules stay
the same — only the surface/shadow/border/motion treatment changes).

MOTION INTENSITY for this style: {style.get('motion_tier', 'Use the base motion tokens as documented, no intensity change for this style.')}

Font: this style's pairing (heading {style['font_heading']}, body
{style['font_body']}) is ALREADY wired into the provided index.html and
src/index.css — the whole app renders in it automatically. Do NOT add any
@import or font-family override; `font-display` (Tailwind) is available if a
heading needs the display font explicitly.

Do NOT use: {style['avoid']}
{style['example']}
"""
