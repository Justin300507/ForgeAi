"""
Layout Planner (deterministic).

Chooses the app-shell archetype. Data-dense tool categories keep the
battle-tested dark gradient sidebar shell (the base frontend prompt is
written for it and it has survived every canary). Content-forward consumer
categories — where a persistent admin sidebar is the #1 tell that an app
was generated rather than designed — get a top-nav content shell instead:
sticky header, centered max-width column, page-level hero headers.

The choice is a pure function of the category key, so it inherits the same
"stable across Check & Fix re-runs" contract as select_style().
"""
from dataclasses import dataclass

# Categories whose products are content/imagery-forward — a boxed-in admin
# sidebar actively fights their identity.
_TOPNAV_CATEGORIES = frozenset({"restaurant", "travel", "portfolio"})


@dataclass(frozen=True)
class LayoutPlan:
    shell: str       # "sidebar" | "topnav"
    hero: str        # how the primary page opens
    rationale: str


def plan_layout(category_key: str) -> LayoutPlan:
    if category_key in _TOPNAV_CATEGORIES:
        return LayoutPlan(
            shell="topnav",
            hero="Each main page opens with a page-level hero header: an oversized "
                 "title (text-3xl md:text-4xl font-bold tracking-tight), a one-line "
                 "muted subtitle, and the page's primary action aligned right — "
                 "content pages breathe like an editorial site, not an admin panel.",
            rationale="Content-forward consumer product — navigation should recede "
                      "behind the content, not frame it.",
        )
    return LayoutPlan(
        shell="sidebar",
        hero="The dashboard opens with the greeting header and stats grid the base "
             "prompt specifies.",
        rationale="Data-dense tool — persistent sidebar navigation earns its space.",
    )


def build_topnav_override(ds: dict) -> str:
    """The LAYOUT OVERRIDE block appended to the design-system injection for
    top-nav categories. Follows the same override pattern as style_system:
    the base prompt's sidebar instructions are explicitly countermanded and
    replaced with a complete, JSX-safe shell example using this category's
    real tokens."""
    return f"""
═══════════════════════════════════════════════════════
LAYOUT OVERRIDE — TOP-NAV SHELL  ← this app has NO sidebar
═══════════════════════════════════════════════════════

This app uses a top navigation bar with a centered content column, NOT the
sidebar shell. Wherever the base prompt or the design system above says to
build the EXAMPLE SIDEBAR, a fixed <aside>, or `ml-56` main margins —
ignore it for this app and use the shell below instead. Everything else
(auth rules, motion tokens, JSX-safety rules, accessibility, the assigned
style's surface treatment) still applies unchanged.

APP SHELL (all authenticated pages — same navClass discipline as always:
a plain named function returning one of two full class strings, NEVER an
inline template-literal ternary in className):
```jsx
const navClass = ({{ isActive }}) =>
  isActive
    ? 'px-3 py-2 rounded-lg text-sm font-medium transition-all duration-200 bg-{ds['primary_name']}-50 text-{ds['primary_name']}-700 dark:bg-{ds['primary_name']}-900/30 dark:text-{ds['primary_name']}-300'
    : 'px-3 py-2 rounded-lg text-sm font-medium transition-all duration-200 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800';

<div className="min-h-screen bg-slate-50 dark:bg-slate-900">
  {{/* Ambient background blobs from the design system above go here (unless
      the assigned style says to skip decorative background elements) */}}
  <header className="sticky top-0 z-20 bg-white/80 dark:bg-slate-900/80 backdrop-blur-xl border-b border-slate-100 dark:border-slate-800">
    <div className="max-w-6xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-4">
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br {ds['gradient']} flex items-center justify-center shadow-lg shadow-{ds['primary_name']}-500/30">
          <span className="text-white text-sm font-bold">A</span>
        </div>
        <span className="font-bold text-lg bg-gradient-to-r {ds['gradient']} bg-clip-text text-transparent">AppName</span>
      </div>
      <nav className="hidden md:flex items-center gap-1">
        <NavLink to="/dashboard" className={{navClass}}>Overview</NavLink>
        {{/* one NavLink per page */}}
      </nav>
      <div className="flex items-center gap-2">
        {{/* dark-mode toggle + logout button (aria-labels required) */}}
      </div>
    </div>
  </header>
  <main className="max-w-6xl mx-auto px-4 sm:px-6 py-8 animate-fade-in-up">
    ...page content...
  </main>
</div>
```

Rules for this shell:
- Mobile nav: add a `md:hidden` menu button (aria-label="Open menu") that
  toggles a simple dropdown panel below the header containing the same
  NavLinks; Escape closes it. Do not build a slide-out drawer.
- The header's translucent/backdrop-blur surface shown above applies to the
  Glass style. If a STYLE OVERRIDE section appears above, restyle the header
  surface to match it (e.g. Neubrutalist: opaque bg-white dark:bg-slate-900
  with border-b-2 and no blur; Minimal Editorial: plain background with a
  hairline border-b and a flat font-black wordmark).
- Page headers are the hero here: open each main page with an oversized
  title (text-3xl md:text-4xl font-bold tracking-tight), a one-line muted
  subtitle, and the page's primary action button aligned to the right of
  the title row. Content pages should breathe like an editorial site.
- Keep content readable: the max-w-6xl column is mandatory — never
  full-bleed tables or forms edge-to-edge.
"""
