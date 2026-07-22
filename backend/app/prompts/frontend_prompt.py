import json

from app.prompts.design_system import build_design_system_injection


def build_frontend_prompt(
    architecture,
    target: str = "web",
    idea: str = "",
    style_override: str | None = None,
    motion_intensity: str | None = None,
    include_landing_page: bool = False,
):
    # Extract idea from architecture if not passed directly
    if not idea:
        idea = (
            architecture.get("project_name", "")
            + " "
            + " ".join(architecture.get("features", []))
        )
    design_system = build_design_system_injection(idea, style_override, motion_intensity)

    landing_page_note = (
        """

═══════════════════════════════════════════════════════
LANDING PAGE  (user opted in — generate this in addition to everything else)
═══════════════════════════════════════════════════════

Generate an extra file, src/pages/LandingPage.jsx, a PUBLIC page (no auth
required, no sidebar/app-shell) shown at "/" instead of the usual immediate
redirect to the dashboard. This is the very first thing a visitor sees,
before signing up — it must sell the app's actual purpose, not restate
"Welcome back" like the login page does.

Structure (single scrolling page, use this app's gradient/primary_name
tokens from the design system above throughout — never a neutral/indigo
default here of all places):
  1. Hero: large headline stating the app's core value proposition in this
     app's actual domain (not generic "Manage everything in one place" —
     name the real thing this app does, e.g. for a task tracker: "Ship your
     to-do list, not your excuses"), a supporting one-line subhead, and a
     primary CTA button ("Get Started" / "Sign Up Free") linking to
     /register, using the same gradient primary-button treatment as
     everywhere else in this app.
  2. Feature highlights: 3 short cards, each with a lucide-react icon from
     this app's ICON VOCABULARY above, a bolded feature name, and one
     sentence — pull these from the app's actual entities/features in the
     architecture above, never generic filler ("Fast", "Secure", "Easy").
  3. Closing CTA band: a second, simpler call-to-action ("Ready to start?"
     + the same Sign Up button) so a visitor who scrolled past the hero
     still has an obvious next step.
  4. A minimal top bar with the app name (gradient brand text, same pattern
     as the sidebar) and a "Sign In" link to /login on the right.

Use the same ambient background blobs technique from the design system
above. Apply this style's MOTION INTENSITY guidance to the landing page
too — the hero and feature cards are exactly the kind of content that
benefits from scroll-triggered entrances when intensity is "heavy".

Routing change (App.jsx): the root path "/" now renders LandingPage
instead of an immediate redirect —
```jsx
<Route path="/" element={<LandingPage />} />
<Route path="*" element={<Navigate to="/dashboard" replace />} />
```
(the wildcard "*" fallback for unmatched paths still points at /dashboard,
unchanged — only the exact "/" root path changes). LandingPage is NOT
wrapped in PrivateRoute — it must render for logged-out visitors.
"""
        if include_landing_page
        else ""
    )

    # Pre-computed (not inline f-string substitutions): Python <3.12 forbids
    # a backslash anywhere inside an f-string {...} expression part, and
    # these conditional strings need escaped quotes / \n — a real Railway
    # (python:3.11-slim) crash confirmed this live: "SyntaxError: f-string
    # expression part cannot include a backslash". Computing them as plain
    # variables first and referencing a bare {name} in the f-string avoids
    # the restriction entirely, and works identically on every Python version.
    checklist_19_root_note = (
        ' -- and if a LANDING PAGE section appears above, verify the exact '
        'root path "/" renders LandingPage, NOT a redirect to /dashboard; '
        'otherwise (no landing page requested) verify "/" also redirects to '
        '/dashboard, same as the wildcard'
        if include_landing_page else
        ' (the exact root path "/" gets this same /dashboard redirect too, '
        'since no landing page was requested)'
    )
    checklist_28_landing_page = (
        "28. Verify src/pages/LandingPage.jsx is a REAL marketing page for THIS "
        "app's actual domain (hero headline naming what the app specifically "
        "does, 3 feature cards pulled from its real entities/features, a CTA "
        "linking to /register) using Tailwind classes and this app's gradient "
        "tokens — NOT a generic placeholder, NOT inline style={} objects, and "
        "NOT content/stats copied from a different app category than this one.\n"
        if include_landing_page else ""
    )
    checklist_29_motion_library = (
        "29. Verify `motion` (import from 'motion/react') is ACTUALLY imported "
        "and used somewhere (AnimatePresence route transitions, whileInView "
        "reveals, or whileHover/whileTap) — this app's MOTION INTENSITY is "
        "\"heavy\", which specifically requires it; the base Tailwind "
        "animate-* tokens alone do NOT satisfy this tier.\n"
        if motion_intensity == "heavy" else ""
    )
    root_redirect_note = (
        'The exact root path "/" is handled by the LANDING PAGE section above '
        "instead — do NOT also add a plain redirect-to-/dashboard route for "
        '"/", that would override the landing page route.'
        if include_landing_page else
        'The root path "/" gets the SAME redirect as the wildcard above:\n'
        "```jsx\n"
        '<Route path="/" element={<Navigate to="/dashboard" replace />} />\n'
        "```"
    )

    pwa_note = (
        "\n\nPWA TARGET — apply ALL of these:"
        "\n• Import and render <InstallPrompt /> in App.jsx (provided at src/components/InstallPrompt.jsx — do NOT re-generate it)"
        "\n• Import notification utilities where relevant:"
        "\n    import { requestNotificationPermission, scheduleDaily, alertBudgetExceeded, alertWorkoutReminder } from '../utils/notifications';"
        "\n• On the Settings or Dashboard page, add a 'Enable Notifications' button:"
        "\n    <button onClick={async () => { const r = await requestNotificationPermission(); setNotifStatus(r); }} ...>"
        "\n• Wire budget/workout/task alerts to the appropriate actions (e.g. when adding an expense that exceeds budget, call alertBudgetExceeded)"
        "\n• The notifications.js utility is already provided — do NOT re-generate it"
        if target == "pwa" else ""
    )

    return f"""You are ForgeAI Frontend Generator v2. Generate a modern, polished React + Tailwind CSS frontend.

Architecture:
{json.dumps(architecture, indent=2)}
{pwa_note}
{design_system}

Return ONLY valid JSON in this exact format:
{{"files": [{{"path": "src/App.jsx", "content": "..."}}, ...]}}

═══════════════════════════════════════════════════════
STACK  (mandatory — no exceptions)
═══════════════════════════════════════════════════════

✅ Tailwind CSS utility classes for ALL styling
✅ lucide-react for ALL icons
✅ recharts for ALL charts/graphs (dashboard only)
✅ react-router-dom for routing
✅ React.useState / React.useEffect for state
✅ axios for API calls (import axios from 'axios')
✅ motion (import from 'motion/react') — ONLY for the specific effects the
   MOTION INTENSITY section below calls for at this app's selected tier;
   never as a general replacement for the Tailwind animate-* tokens
❌ NO inline style={{}} objects (only Tailwind classes)
❌ NO @mui, @chakra-ui, antd, or other component libraries
❌ NO external CSS files except src/index.css (already provided)
❌ NO TypeScript

═══════════════════════════════════════════════════════
FILE RULES
═══════════════════════════════════════════════════════

- Generate: src/App.jsx + src/pages/*.jsx + src/components/*.jsx
- Maximum 20 files total
- Every path: starts with src/, forward slashes, ASCII only, ends with .jsx
- Never import a file you did not also generate in this same response
- Every component: export default ComponentName (no named exports)
- src/index.css, src/main.jsx, and src/components/ErrorBoundary.jsx are already
  provided — DO NOT regenerate them (main.jsx already wraps <App /> in the
  ErrorBoundary; never import or re-create an error boundary yourself)

═══════════════════════════════════════════════════════
DESIGN SYSTEM  (use these patterns exactly)
═══════════════════════════════════════════════════════

COLOR PALETTE (Tailwind classes — these are the NEUTRAL/structural colors only:
backgrounds, surfaces, borders, body text. They are the same in every app
regardless of category. For BRAND/accent colors — sidebar, primary buttons,
active nav, icon badges, gradients — use ONLY the tokens in the
APP-SPECIFIC DESIGN SYSTEM section above, never indigo-600 as a default):
  Backgrounds:  bg-slate-50  dark:bg-slate-900
  Surfaces:     bg-white  dark:bg-slate-800
  Borders:      border-slate-100  dark:border-slate-700
  Text primary: text-slate-900  dark:text-slate-100
  Text muted:   text-slate-500  dark:text-slate-400

═══════════════════════════════════════════════════════
MOTION TOKENS  (already defined in the scaffold — just use the class names)
═══════════════════════════════════════════════════════

These animation utilities are pre-wired in tailwind.config.js / index.css.
Do NOT redefine any keyframes — apply the classes:

  animate-fade-in        gentle opacity fade (0.4s)
  animate-fade-in-up     fade + 12px rise — THE standard entrance
  animate-scale-in       springy scale entrance — dialogs, toasts, popovers
  animate-slide-in-right slide entrance for panels/drawers
  animate-pop            success pop with overshoot — confirmation moments
  animate-float-slow     slow ambient drift (18s) — background blob #1
  animate-float-slower   slower counter-drift (26s) — background blob #2
  .skeleton              shimmer loading block (moving highlight sweep)
  .live-dot              pulsing brand-colored live-activity dot
  .gradient-animated     slow gradient pan — add to bg-gradient-to-r hero/brand elements

Usage rules (mandatory, not decoration):
- Every page's main content wrapper gets `animate-fade-in-up`.
- Every `.map()` rendering cards/rows adds BOTH `animate-fade-in-up` AND
  `style={{{{ animationDelay: `${{index * 40}}ms` }}}}` so lists cascade in.
- Toasts and modals enter with `animate-scale-in` (never appear instantly).
- The two ambient background blobs get `animate-float-slow` and
  `animate-float-slower` respectively so the background feels alive.
- Dashboard "Recent activity"/live sections show `<span className="live-dot" />`
  before the section title.
- All of these are transform/opacity only and automatically disabled for
  prefers-reduced-motion — use them freely, never invent new keyframes.

═══════════════════════════════════════════════════════
VISUAL POLISH — mandatory, this is what separates "plain" from premium
═══════════════════════════════════════════════════════

A flat white card with a thin border and nothing else reads as a wireframe,
not a shipped product. Apply ALL of these across every page:

- Card depth: every surface card gets `shadow-sm hover:shadow-md
  transition-shadow duration-200` at minimum — cards that are clickable
  (list rows, nav items, stat cards that link somewhere) ALSO get
  `hover:-translate-y-0.5 transition-all duration-200` for a subtle lift.
- Ring for definition in dark mode: add `ring-1 ring-black/5
  dark:ring-white/5` alongside the border on elevated surfaces (cards,
  modals, dropdowns) — a hairline border alone looks flat on dark
  backgrounds.
- Icon badges are never a flat solid box: use a soft gradient tint —
  `bg-gradient-to-br from-{{color}}-50 to-{{color}}-100 dark:from-{{color}}-900/40
  dark:to-{{color}}-900/20` instead of a single flat `bg-{{color}}-50`.
- Buttons get press feedback: `active:scale-[0.97] transition-transform
  duration-100` in addition to the hover color change — a button that only
  changes color on hover feels unresponsive on click.
- Page entrance: wrap the main content return in
  `<div className="animate-fade-in-up">` (token already defined — do not
  redefine any keyframe).
- List/grid entrances stagger: when mapping over an array to render cards,
  add `style={{{{ animationDelay: `${{index * 40}}ms` }}}}` alongside the same
  `animate-fade-in-up` class so items cascade in rather than all popping at once.
- Numbers and stats: use `font-bold tracking-tight` on large stat values
  (not just `font-bold`) — tight tracking on big numbers reads as more
  deliberate/designed.
- Section headings use a consistent weight scale: page title
  `text-2xl font-bold`, card/section title `text-base font-semibold`,
  label/eyebrow text `text-xs font-medium uppercase tracking-wide
  text-slate-500`. Never mix arbitrary weights across the same hierarchy
  level between pages.
- Dividers between grouped content use `divide-y divide-slate-100
  dark:divide-slate-700` on the parent instead of manual borders on every
  child — keeps spacing consistent.
- Every icon inside a colored badge/circle should be sized 1–2px smaller
  than the badge would suggest (e.g. 18px icon in a 36px badge, not a 24px
  icon cramped into the same box) — breathing room reads as more refined.

═══════════════════════════════════════════════════════
ACCESSIBILITY — mandatory, not optional polish
═══════════════════════════════════════════════════════

- Every icon-only button (no visible text label — nav collapse toggle, row
  "⋮" menu, close/X buttons, dark-mode toggle) gets `aria-label="..."`
  describing the action, e.g. `<button aria-label="Delete item" onClick={{...}}>`.
- Every form input already has a visible `<label>` per the FORM INPUT pattern
  above — never rely on `placeholder` alone as the only label.
- Every interactive element (button, link, input) needs a visible focus
  state, not just hover: add `focus:outline-none focus-visible:ring-2
  focus-visible:ring-{{primary_name}}-500 focus-visible:ring-offset-2
  dark:focus-visible:ring-offset-slate-900` alongside its existing hover/
  active classes (substitute this app's actual `primary_name` token from the
  design system above, same as every other `{{primary_name}}` placeholder in
  this prompt) — a sighted keyboard user tabbing through the page must be
  able to see where focus is.
- Modals/dialogs get `role="dialog" aria-modal="true"` and close on Escape
  (`useEffect` with a `keydown` listener checking `e.key === 'Escape'`).
- Never use color as the ONLY signal for status (a red dot with no text is
  invisible to colorblind users) — pair every status color with an icon or
  text label (the BADGE pattern below already does this correctly: colored
  background + a text word, follow that model everywhere else too).
- `prefers-reduced-motion` is already handled globally in the provided
  src/index.css (a `* {{ animation-duration: 0.01ms !important; ... }}` rule
  under that media query) — do NOT add your own
  `window.matchMedia('(prefers-reduced-motion)')` checks in JSX, that would
  be redundant and risks fighting the global rule.

SIDEBAR LAYOUT + NAV LINK PATTERN (use for ALL authenticated pages — UNLESS a
"LAYOUT OVERRIDE — TOP-NAV SHELL" section appears in the design system above,
in which case that override's top-nav shell replaces this entire sidebar
pattern and the `ml-56` main margin):
Use the EXAMPLE SIDEBAR code block from the APP-SPECIFIC DESIGN SYSTEM section
above verbatim (gradient sidebar, gradient active nav pill, gradient brand
text) — do not fall back to a flat white/indigo sidebar, that is the generic
look this app must NOT have. The overall shell shape is:
```jsx
<div className="flex min-h-screen bg-slate-50 dark:bg-slate-900">
  {{/* Ambient background blobs from the design system above go here, as the
     first child of this div */}}
  {{/* Sidebar — use the EXAMPLE SIDEBAR block from the design system above */}}
  <aside>...</aside>
  {{/* Main */}}
  <main className="ml-56 flex-1 p-6 overflow-auto relative z-0">
    ...page content...
  </main>
</div>
```
The nav-link ternary shown in the design system's EXAMPLE SIDEBAR (a plain
named `navClass` function, never an inline template literal in `className`) is
mandatory — inlining `${{isActive ? ...}}` directly in the className attribute
is the #1 cause of "Expected '}}' but found isActive" build failures.

STAT CARD — use the EXAMPLE STAT CARD block from the APP-SPECIFIC DESIGN
SYSTEM section above (glass-lite surface, category gradient icon badge,
hover lift, tight-tracking number). Do not substitute a flat indigo badge.

LIST ITEM CARD (clickable → lift on hover, gradient badge, staggered entrance):
Use the SAME `stat_icon_bg` / `stat_icon_color` tokens from the design system
above for the icon badge — never a hardcoded indigo badge here either.
```jsx
{{items.map((item, index) => (
  <div
    key={{item.id}}
    style={{{{ animationDelay: `${{index * 40}}ms` }}}}
    className="animate-fade-in-up bg-white/80 dark:bg-slate-800/70 backdrop-blur-xl rounded-xl border border-slate-100 dark:border-slate-700/60 ring-1 ring-black/5 dark:ring-white/5 p-4 flex items-center justify-between hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 cursor-pointer"
  >
    <div className="flex items-center gap-3">
      {{/* icon badge: use this app's stat_icon_bg / stat_icon_color tokens from the design system above */}}
      <div className="w-9 h-9 rounded-lg flex items-center justify-center">
        <ShoppingCart size={{18}} />
      </div>
      <div>
        <p className="text-sm font-semibold text-slate-900 dark:text-white">{{item.title}}</p>
        <p className="text-xs text-slate-500 dark:text-slate-400">Food · Jun 22</p>
      </div>
    </div>
    <span className="text-sm font-bold tracking-tight text-red-500">-$47.80</span>
  </div>
))}}
```

PRIMARY BUTTON — the app's main brand CTA (Add/Create/Save) uses the
category gradient from the design system above, NOT the generic `.btn-primary`
utility class (that class is a neutral fallback for secondary/incidental
buttons only — a flat single-color primary button is exactly the "plain"
look this app must avoid):
```jsx
<button className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-white font-medium bg-gradient-to-r {{gradient}} hover:opacity-90 active:scale-[0.97] shadow-lg shadow-{{primary_name}}-500/25 transition-all duration-150">
  <Plus size={{16}} /> Add Item
</button>
```
Replace `{{gradient}}` / `{{primary_name}}` with this app's actual tokens from
the design system above (e.g. `from-emerald-500 to-teal-500` / `emerald`) —
these are placeholders here, not literal Tailwind classes to copy verbatim.
Secondary/neutral actions (Cancel, Back, icon-only toolbar buttons) may still
use the `.btn-secondary` / `.btn-primary` utility classes from index.css.

FORM INPUT:
```jsx
<div className="space-y-1">
  <label className="text-xs font-medium text-slate-700 dark:text-slate-300">Email</label>
  <input
    type="email"
    value={{email}}
    onChange={{e => setEmail(e.target.value)}}
    placeholder="you@example.com"
    className="input"
  />
</div>
```

BADGE:
```jsx
<span className="badge bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">Active</span>
<span className="badge bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">Pending</span>
<span className="badge bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-400">Overdue</span>
```

═══════════════════════════════════════════════════════
PAGE REQUIREMENTS
═══════════════════════════════════════════════════════

LOGIN PAGE (no sidebar — centered card, first screen the user ever sees, so
it must NOT be a flat white card with a flat indigo logo. Use this app's
gradient tokens from the design system above, plus the same ambient
background blobs, for the logo badge / link color / submit button):
```jsx
<div className="min-h-screen bg-slate-50 dark:bg-slate-900 flex items-center justify-center p-4 relative overflow-hidden">
  {{/* Ambient background blobs — same technique as the design system above */}}
  <div className="fixed inset-0 overflow-hidden pointer-events-none -z-10">
    <div className="animate-float-slow absolute -top-24 -right-24 w-96 h-96 rounded-full bg-gradient-to-br {{gradient}} opacity-20 dark:opacity-10 blur-3xl" />
    <div className="animate-float-slower absolute bottom-0 left-1/4 w-72 h-72 rounded-full bg-gradient-to-br {{gradient}} opacity-10 dark:opacity-[0.07] blur-3xl" />
  </div>
  <div className="w-full max-w-sm">
    <div className="text-center mb-8">
      <div className="w-12 h-12 rounded-2xl bg-gradient-to-br {{gradient}} mx-auto mb-3 flex items-center justify-center shadow-lg shadow-{{primary_name}}-500/30">
        <span className="text-white font-bold text-xl">A</span>
      </div>
      <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Welcome back</h1>
      <p className="text-slate-500 dark:text-slate-400 text-sm mt-1">Sign in to your account</p>
    </div>
    <div className="bg-white/80 dark:bg-slate-800/70 backdrop-blur-xl rounded-2xl shadow-sm ring-1 ring-black/5 dark:ring-white/5 border border-slate-100 dark:border-slate-700/60 p-6 space-y-4">
      {{/* email input, password input, submit button */}}
      <button type="submit" className="w-full justify-center inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-white font-medium bg-gradient-to-r {{gradient}} hover:opacity-90 active:scale-[0.97] shadow-lg shadow-{{primary_name}}-500/25 transition-all duration-150">Sign In</button>
    </div>
    <p className="text-center text-sm text-slate-500 mt-4">
      Don't have an account? <Link to="/register" className="font-medium hover:underline bg-gradient-to-r {{gradient}} bg-clip-text text-transparent">Sign up</Link>
    </p>
  </div>
</div>
```
`{{gradient}}` / `{{primary_name}}` are placeholders — substitute this app's
actual tokens from the design system above, same as the primary button rule.
The Register page follows the identical pattern.

{landing_page_note}
DASHBOARD PAGE — MUST include ALL of:
  1. Page header with greeting + current date
  2. Stats grid (4 cards) — grid-cols-1 sm:grid-cols-2 lg:grid-cols-4
  3. Recharts chart (BarChart or AreaChart) in a card below stats
  4. Recent items list (last 5 entries)

These four are the FLOOR, not the whole page — a dashboard that stops here
looks identical across every app category regardless of what the app
actually does, which reads as generic no matter how well each individual
card is styled. Add 1-2 MORE widgets chosen from this app's actual entities/
features (pick whichever genuinely fits, don't force one that doesn't):
  - Goal/progress card: a labeled progress bar or radial percentage toward
    a target (budget remaining, workout streak, tasks completed this week)
  - Quick actions row: 2-4 small buttons for the app's most common action
    besides "Add X" (e.g. "Mark all read", "Export CSV", "Start timer")
  - Secondary breakdown: a compact donut/pie or horizontal bar list showing
    a category split (spending by category, tasks by status, members by tier)
  - Attention list: items needing action, styled distinctly from the
    "recent" list (overdue, low-stock, unread) — red/amber accents, not the
    neutral list styling used for "recent activity"
Compose these into the SAME stats-grid + chart + list shell above, don't
replace it — the floor stays mandatory, this is what fills it out.

CHART TYPE — match the chart to what the data actually represents, don't
default to AreaChart for everything:
  Trend over time (revenue, weight, completions per day)   -> AreaChart or LineChart
  Comparing discrete categories (sales by region, votes)    -> BarChart
  Part-of-a-whole composition (budget by category, status)  -> PieChart / donut (innerRadius set)
  Single value vs. a goal (storage used, workout streak %)  -> RadialBarChart or a plain progress bar

RECHARTS EXAMPLE (area chart):
```jsx
import {{ AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer }} from 'recharts';

const data = [
  {{ month: 'Jan', total: 840 }},
  {{ month: 'Feb', total: 720 }},
  {{ month: 'Mar', total: 1100 }},
  {{ month: 'Apr', total: 890 }},
  {{ month: 'May', total: 1240 }},
  {{ month: 'Jun', total: 980 }},
];

<div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-100 dark:border-slate-700 p-5">
  <h3 className="font-semibold text-slate-900 dark:text-white mb-4">Monthly Overview</h3>
  {{data.length === 0 ? (
    <div className="h-[240px] flex flex-col items-center justify-center text-center gap-2">
      <TrendingUp size={{28}} className="text-slate-300 dark:text-slate-600" />
      <p className="text-sm text-slate-400 dark:text-slate-500">Not enough data yet — check back after a few entries</p>
    </div>
  ) : (
    <ResponsiveContainer width="100%" height={{240}}>
      <AreaChart data={{data}}>
        <defs>
          <linearGradient id="colorTotal" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#6366f1" stopOpacity={{0.15}} />
            <stop offset="95%" stopColor="#6366f1" stopOpacity={{0}} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis dataKey="month" tick={{{{ fontSize: 12, fill: '#94a3b8' }}}} axisLine={{false}} tickLine={{false}} />
        <YAxis tick={{{{ fontSize: 12, fill: '#94a3b8' }}}} axisLine={{false}} tickLine={{false}} />
        <Tooltip contentStyle={{{{ background: '#1e293b', border: 'none', borderRadius: '8px', color: '#f1f5f9' }}}} />
        <Area type="monotone" dataKey="total" stroke="#6366f1" strokeWidth={{2}} fill="url(#colorTotal)" activeDot={{{{ r: 5, strokeWidth: 2 }}}} />
      </AreaChart>
    </ResponsiveContainer>
  )}}
</div>
```
The empty-state branch above is mandatory for every chart, not optional
polish — a chart fed an empty array silently renders a blank card with axes
and no data, which is indistinguishable from the app being broken (this is
the single most common false "blank page" report from screenshot review).
Replace the icon/copy to fit the app's domain; keep the structure.

LIST / INDEX PAGES — MUST contain:
  - Page header with title + "Add X" button (flex justify-between items-center)
  - Search bar with value/onChange wired to a search state variable
  - Status/category filter dropdown wired to a filter state variable
  - At LEAST 5 pre-populated example items in useState initial array (use realistic domain data)
  - Empty state component if filtered list is empty (icon + "No results found" message)
  - Loading skeleton (show when fetching) using animate-pulse divs

COMPOSITION MENU — pick 1-2 of these per list/detail page where the domain
genuinely calls for it (don't force all of them onto every page, and don't
skip all of them either — a page using only the bare list above every time
is what makes generated apps feel interchangeable regardless of what they
actually do):
  - Sort control next to the search bar (dropdown or clickable column header)
  - Bulk select: checkbox per row + a contextual action bar that appears
    once >=1 row is selected ("3 selected — Delete / Archive")
  - Row action menu: a single "⋮" icon button per row opening a small
    absolute-positioned dropdown (Edit/Duplicate/Delete) instead of three
    separate always-visible icon buttons crowding the row
  - Tabs above the list to split by a natural status dimension (All /
    Active / Archived) — real `<button>` tabs with an active-underline
    style, wired to a state variable that filters the array, not separate
    routes
  - Pagination or a "Load more" button once the (demo) dataset exceeds ~8 items
  - Detail pages (viewing one item, not editing) benefit from a 2-column
    layout on lg+ screens: primary info left, a compact metadata/activity
    sidebar right — a form-shaped single column for a read-only detail view
    is a common tell that the page was templated rather than composed

FORM PAGES — MUST contain:
  - Page header with back button (← Back)
  - All fields with labels above inputs
  - Placeholder text matching the domain (e.g. "e.g. John Smith" not "Enter name")
  - Client-side validation: disable submit button if required fields are empty
  - Submit button shows loading state: disabled + spinner while submitting
  - Submit + Cancel buttons at bottom
  - Success/error toast feedback after submit

TOAST NOTIFICATIONS — add to every page that mutates data:
- Keep this EXACTLY as shown — do not nest a ternary inside a template literal inside a JSX
  attribute expression, that is the #1 cause of "Expected '}}' but found ..." build failures.
  Compute the color class as its own plain variable BEFORE the return statement instead.
```jsx
const [toast, setToast] = React.useState(null);
const showToast = (msg, type = 'success') => {{
  setToast({{ msg, type }});
  setTimeout(() => setToast(null), 3000);
}};
// Inside the component, before the return statement:
const toastColorClass = toast && toast.type === 'success' ? 'bg-emerald-600' : 'bg-red-600';
// In JSX:
{{toast && (
  <div className={{`animate-scale-in fixed bottom-4 right-4 px-4 py-3 rounded-xl shadow-lg text-sm font-medium text-white z-50 ${{toastColorClass}}`}}>
    {{toast.msg}}
  </div>
)}}
```

GENERAL RULE — this is not just the NavLink/toast cases above. The SAME bug
appears anywhere: a streak/badge color class, a progress bar width, a status
label, a conditional icon, etc. NEVER write a ternary or another `${{...}}`
directly inside a template literal that is itself inside a JSX `{{...}}`
expression (className, style, or anywhere else) — that nested-brace pattern
is what breaks the build, regardless of which value it's computing. The fix
is always the same: compute the ENTIRE interpolated/conditional string as its
own plain `const` on the line before `return`, then reference only that
identifier inside the JSX expression. If you find yourself writing a `${{`
inside a `` {{`...`}} `` that is itself inside a JSX attribute, stop and pull
it out into a variable first.

LOADING STATES — every useEffect data fetch MUST show a shimmer skeleton
(use the provided `.skeleton` class — a moving highlight sweep, NOT plain
animate-pulse gray boxes — and shape the blocks like the content they
replace: stat-card-sized blocks for a stats grid, row-height blocks for a list):
```jsx
const [loading, setLoading] = React.useState(true);
// In JSX:
{{loading ? (
  <div className="space-y-3">
    {{[...Array(5)].map((_, i) => (
      <div key={{i}} className="skeleton h-16" />
    ))}}
  </div>
) : (
  /* real content */
)}}
```

PRE-POPULATED DEMO DATA — use realistic domain-specific values:
  Gym app:    members named "Alex Chen", "Maria Garcia", tier "Premium", class "7:00 AM Yoga"
  Finance:    expenses like "Whole Foods $89.40", "Netflix $15.99", category "Food"/"Entertainment"
  Tasks:      "Launch Q3 campaign", "Fix login bug", status "In Progress", priority "High"
  Shop:       "Wireless Headphones $79.99", "USB-C Cable $12.99", stock 24, category "Electronics"
  Never use:  "User 1", "Item A", "test@test.com", "Lorem ipsum", "Sample data"

═══════════════════════════════════════════════════════
LUCIDE-REACT ICON GUIDE
═══════════════════════════════════════════════════════

Import only what you use:
  import {{ LayoutDashboard, Plus, Search, Trash2, Pencil, ChevronRight,
           TrendingUp, TrendingDown, DollarSign, Users, Package,
           ShoppingCart, Calendar, Bell, Settings, LogOut, X,
           CheckCircle, AlertCircle, Clock, BarChart2 }} from 'lucide-react';

Use as JSX: <LayoutDashboard size={{18}} className="text-slate-500 dark:text-slate-400" />
(inside a category-colored icon badge, use this app's `stat_icon_color` token
from the design system above instead of a neutral color)
Default size: 16 for nav, 18 for list icons, 20 for page headings

═══════════════════════════════════════════════════════
DARK MODE + LOGOUT — OWNED BY App.jsx, PASSED DOWN AS PROPS
═══════════════════════════════════════════════════════

`dark`/`setDark`/`onLogout` must live in ONE place (App.jsx) and be passed as
props to every routed page — NOT declared as local state inside whichever
page happens to render the toggle button or a Header. If each page owns its
own `dark` state independently, toggling it on one page has no effect on any
other page (looks broken), and a page's Header/logout button silently does
nothing if that page never wrote its own click handler. This is the single
most common way a generated app's dark-mode toggle and logout button end up
non-functional — the pattern below is mandatory, not optional styling.

App.jsx:
```jsx
import React from 'react';
import {{ BrowserRouter, Routes, Route, Navigate, useNavigate }} from 'react-router-dom';

function AppRoutes() {{
  const navigate = useNavigate();
  const [dark, setDark] = React.useState(() => localStorage.getItem('theme') === 'dark');

  React.useEffect(() => {{
    document.documentElement.classList.toggle('dark', dark);
    localStorage.setItem('theme', dark ? 'dark' : 'light');
  }}, [dark]);

  const onLogout = () => {{
    localStorage.removeItem('token');
    navigate('/login');
  }};

  const pageProps = {{ dark, setDark, onLogout }};

  return (
    <Routes>
      <Route path="/dashboard" element={{<PrivateRoute><Dashboard {{...pageProps}} /></PrivateRoute>}} />
      {{/* pass {{...pageProps}} to EVERY routed page, public and private alike */}}
    </Routes>
  );
}}

export default function App() {{
  return (<BrowserRouter><AppRoutes /></BrowserRouter>);
}}
```

Every page/component that shows a dark-mode toggle or a logout button
receives `dark`, `setDark`, `onLogout` as **props** (destructure them from the
function's params — do NOT call `React.useState` for `dark` anywhere outside
App.jsx):
```jsx
const Header = ({{ dark, setDark, onLogout }}) => (
  <header>
    <button onClick={{() => setDark(d => !d)}} className="p-2 rounded-lg text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors">
      {{dark ? <Sun size={{18}} /> : <Moon size={{18}} />}}
    </button>
    <button onClick={{onLogout}}>Log out</button>
  </header>
);
```

═══════════════════════════════════════════════════════
API INTEGRATION
═══════════════════════════════════════════════════════

NEVER call an endpoint path that isn't listed in the Architecture's
api_endpoints above. Every `API.get(...)`/`API.post(...)`/etc. call MUST use
one of those exact paths (with real path-param values substituted). If a
page needs data that ISN'T covered by any listed endpoint (e.g. "show the
current user's own posts" or "show this author's profile"), reuse the
closest EXISTING endpoint instead of guessing a new resource name --
prefer a query parameter or an existing user-scoped endpoint over
inventing `/authors/{{id}}` or `/articles?author_id={{id}}` out of thin air
when the architecture already declares `/users/{{user_id}}` and a `/posts`
(or similar) endpoint that takes the same filter. A comment like
"assuming an endpoint like /authors/{{id}}" is an explicit admission the
call is fabricated, not grounded in the architecture -- if you catch
yourself writing a comment like that, stop and use the real listed
endpoint instead, even if it means composing the page's data from two
existing calls rather than one convenient invented one.

ALWAYS create src/api.js as a shared axios instance (every page imports from here):
```jsx
import axios from 'axios';
const API = axios.create({{ baseURL: import.meta.env.VITE_API_URL || '' }});
API.interceptors.request.use(cfg => {{
  const token = localStorage.getItem('token');
  if (token) cfg.headers.Authorization = `Bearer ${{token}}`;
  return cfg;
}});
// A 401 from any endpoint OTHER than login/register means the stored token
// is stale or invalid (expired, or the backend's JWT secret rotated on a
// redeploy) -- not a validation error the current page should render. Clear
// it and hard-redirect to /login instead of letting every page's error
// parser show a raw "Could not validate credentials" string. Returning a
// never-resolving promise (not Promise.reject) stops the calling page from
// flashing a broken error state while the redirect is already in flight.
API.interceptors.response.use(
  res => res,
  err => {{
    const url = err.config?.url || '';
    const isAuthAttempt = url.includes('/auth/login') || url.includes('/auth/register');
    if (err.response?.status === 401 && !isAuthAttempt) {{
      localStorage.removeItem('token');
      if (!window.location.pathname.startsWith('/login')) {{
        window.location.href = '/login';
      }}
      return new Promise(() => {{}});
    }}
    return Promise.reject(err);
  }}
);
export default API;
```
Every page still needs its own `parseError`/try-catch for its OWN endpoint's real
validation errors (422s, 404s, etc.) — this interceptor only intercepts the
"stale session" 401 case so it never reaches page-level error rendering.

ERROR HANDLING — always use these helpers in auth pages:
```jsx
const parseError = (err) => {{
  if (!err.response) return null; // network error — handle separately with retry
  const detail = err.response?.data?.detail;
  if (!detail) return 'Something went wrong. Please try again.';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(d => d.msg).join(', ');
  return 'Something went wrong. Please try again.';
}};
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
```
FastAPI validation errors (422) return detail as an ARRAY — never display it raw.

RETRY PATTERN — wrap all auth API calls in a retry loop (backend may be sleeping on free hosting):
```jsx
for (let attempt = 1; attempt <= 3; attempt++) {{
  try {{
    setStatus(attempt === 1 ? 'Signing in…' : `Starting up… retrying (${{attempt}}/3)`);
    const res = await API.post('/auth/login', {{ email, password }});
    localStorage.setItem('token', res.data.access_token);
    navigate('/dashboard');
    return;
  }} catch (err) {{
    const msg = parseError(err);
    if (msg) {{ setError(msg); setLoading(false); return; }} // real API error, don't retry
    if (attempt < 3) {{ setStatus(`Backend starting up… retrying in 15s (${{attempt}}/3)`); await sleep(15000); }}
  }}
}}
setError('Backend took too long. Wait 30 seconds then try again.');
setLoading(false);
```
Show `status` text in a neutral muted color (`text-slate-500 dark:text-slate-400`)
below the form while retrying.

DASHBOARD/PROTECTED-PAGE DATA FETCHES ALSO NEED THIS RETRY — not just login.
Render's free tier puts the backend to sleep after ~15 minutes idle; EVERY
deploy also cold-starts the same way on its first request. A user who logs
in successfully (auth can differ in timing from the first data fetch) and
then lands on Dashboard/Habits/etc. can still hit a cold backend on that
page's useEffect — and unlike login, these pages have no retry, so the raw
error (e.g. a literal "Not Found" from the platform's own routing while the
container is still starting, not from your API) gets shown directly to the
user, which looks like the app is broken when it just needs ~30-50s. Wrap
the initial data-fetching effect on every protected page in the SAME
attempt/sleep(15000) retry loop shown above (3 attempts).

The loading skeleton MUST visibly change once a retry happens — a skeleton
that looks identical for the full 30-50s cold-start window is indistinguishable
from a permanently broken page from the user's point of view, even though the
retry loop is working correctly underneath. Track whether a retry has
happened and show a message once it has:
```jsx
const [slowLoad, setSlowLoad] = React.useState(false);
// ...inside the catch block, before the sleep:
setSlowLoad(true);
// ...in the loading branch of the JSX:
{{loading && (
  <>
    {{slowLoad && (
      <div className="text-sm font-medium text-amber-600 dark:text-amber-400 mb-4">
        Waking up the server — it's been idle and can take up to a minute on the first load. Hang tight...
      </div>
    )}}
    <SkeletonOrSpinner />
  </>
)}}
```

AUTH RULES — mandatory for every app with authentication:

1. PRIVATE ROUTE — add to App.jsx to protect all authenticated pages:
```jsx
const PrivateRoute = ({{ children }}) =>
  localStorage.getItem('token') ? children : <Navigate to="/login" replace />;
```
Wrap every authenticated route: `<Route path="/dashboard" element={{<PrivateRoute><Layout><Dashboard /></Layout></PrivateRoute>}} />`

ALWAYS add a wildcard fallback as the LAST route so an unmatched path never
shows a 404 — it redirects to /dashboard (PrivateRoute redirects
unauthenticated users from there to /login automatically):
```jsx
<Route path="*" element={{<Navigate to="/dashboard" replace />}} />
```
{root_redirect_note}

2. LOGIN PAGE — POST JSON to /auth/login, store token + user info, redirect:
```jsx
const res = await API.post('/auth/login', {{ email, password }});
localStorage.setItem('token', res.data.access_token);
if (res.data.display_name) localStorage.setItem('display_name', res.data.display_name);
if (res.data.user_id) localStorage.setItem('user_id', String(res.data.user_id));
if (res.data.email) localStorage.setItem('user_email', res.data.email);
navigate('/dashboard');
```

3. SIGNUP PAGE — validate password, submit, then AUTO-LOGIN (never redirect to /login):
```jsx
if (password.length < 8) {{ setError('Password must be at least 8 characters.'); return; }}
// Step 1: create account
await API.post('/auth/signup', {{ email, password, display_name: displayName }});
// Step 2: immediately log in — user should never have to log in twice
const loginRes = await API.post('/auth/login', {{ email, password }});
localStorage.setItem('token', loginRes.data.access_token);
if (loginRes.data.display_name) localStorage.setItem('display_name', loginRes.data.display_name);
if (loginRes.data.user_id) localStorage.setItem('user_id', String(loginRes.data.user_id));
navigate('/dashboard');
```
Show a hint below the password field: `<p className="text-xs text-slate-400">Must be at least 8 characters</p>`

4. LOGOUT — always clear ALL stored keys and redirect:
```jsx
['token','display_name','user_id','user_email'].forEach(k => localStorage.removeItem(k));
navigate('/login');
```

5. API LIST RESPONSES — response shape VARIES by endpoint in this project, it is
NOT always `{{ items: [...], total: N }}`. A paginated endpoint (accepts
`limit`/`offset` query params) returns `{{ items, total }}` — unwrap with
`res.data.items || []`. A simple, non-paginated list endpoint returns a BARE
ARRAY — use `res.data || []` directly, do NOT read `.items` off it (that
silently becomes `undefined`, and rendering `undefined.length` blanks the
whole page). Check the actual endpoint definition in the architecture above
(does it declare `limit`/`offset` params and a `{{items,total}}` response
model, or a plain `List[...]` response model?) before assuming either shape.

6. CURRENT USER — the login response includes display_name, user_id, email. Read from localStorage:
```jsx
const displayName = localStorage.getItem('display_name') || 'User';
const userId = Number(localStorage.getItem('user_id'));
```
Use these instead of calling /auth/me — already available after login.

═══════════════════════════════════════════════════════
CODE RULES
═══════════════════════════════════════════════════════

- Arrow function components only: const Page = () => {{ ... }}; export default Page;
- useState for all form state, list data, loading, error, toast
- useEffect with empty deps [] to fetch data on mount
- Try/catch around all axios calls; show toast on error
- No comments in code
- 80-250 lines per file (feature-rich pages need more lines — do not truncate)
- No TypeScript, no PropTypes
- Forms: all required fields have Field(min_length=1) equivalent; disable submit when empty
- Search/filter: apply client-side filter on initial data AND wire to API query params

═══════════════════════════════════════════════════════
JSON OUTPUT RULES  (CRITICAL)
═══════════════════════════════════════════════════════

- Escape newlines as \\n inside JSON string values
- Escape double-quotes inside JSX as \\" (use single quotes where possible)
- Escape backslashes as \\\\
- NEVER put a raw literal newline inside a JSON string value
- Return ONLY the JSON object — no markdown, no backticks, no explanation

Before returning:
1. Verify every import references a file you generated (or react/axios/lucide-react/recharts/react-router-dom)
2. Verify every file ends with: export default ComponentName
3. Verify JSON is syntactically valid
4. Verify Login/Register pages have real wired inputs with domain-appropriate placeholders
5. Verify Dashboard has 4 stat cards + a recharts chart with real data shape
6. Verify list pages have at LEAST 5 pre-populated items with realistic domain values (NOT "User 1", NOT "test@test.com")
7. Verify every list page has a working search bar
8. Verify every form has placeholder text, a loading state on submit, and toast feedback
9. Verify every data-fetching page has a loading skeleton
10. Verify dashboard stats come from the /stats/summary API endpoint
11. Verify src/api.js uses baseURL: import.meta.env.VITE_API_URL || 'http://localhost:8000' — this reads the deployed backend URL in production and contacts FastAPI directly in local development. Do NOT proxy `/` through Vite; it prevents the React SPA entrypoint from loading.
12. Verify every auth error uses parseError() helper — never display raw detail or use plain || fallback
13. Verify signup page validates password.length >= 8 before submitting, with visible hint text
14. Verify App.jsx has PrivateRoute wrapping all authenticated routes
15. Verify all list data fetching unwraps .items: setItems(res.data.items || []) not setItems(res.data)
16. Verify signup does auto-login after account creation — NEVER redirect to /login after signup
17. Verify login stores display_name, user_id, user_email in localStorage from response
18. Verify logout clears ALL keys: token, display_name, user_id, user_email
19. Verify App.jsx's wildcard route (path="*") points to /dashboard so an unmatched URL never shows a 404{checklist_19_root_note}
20. Verify EVERY page's main content wrapper has animate-fade-in-up, and every mapped card/row list has the staggered animationDelay — no page or list may appear instantly
21. Verify loading states use the .skeleton class (shimmer), not plain animate-pulse boxes
22. Verify toasts/modals enter with animate-scale-in, ambient blobs have animate-float-slow / animate-float-slower, and the dashboard's recent-activity section has a live-dot
23. Verify every icon-only button has aria-label, and every interactive element has a focus-visible ring (not just hover)
24. Verify at least 1-2 of the SIGNATURE COMPONENTS from the design system above are actually built somewhere, not just the generic list+form shell
25. Verify this style's MOTION INTENSITY guidance (stagger timing, which entrance animation to prefer) was actually followed, not just the base motion tokens by default
26. If a LAYOUT OVERRIDE — TOP-NAV SHELL section appears in the design system above, verify NO page renders a sidebar or ml-56 margin — every authenticated page uses the sticky top-nav shell with the centered max-w-6xl content column
27. Verify the EXPERIENCE BLUEPRINT's delight moment and empty-state treatment are actually implemented (using the existing motion tokens), not just the generic empty-state fallback
28. Verify every `<Link>`, `<NavLink>`, and `navigate('/...')` target is declared as an exact `<Route path="...">` in App.jsx. Never rely on the wildcard redirect for a navigation control.
29. Verify every navigation component you generate is rendered by the authenticated Layout/App shell, and every authenticated route wraps that same shell. Do not generate an unused Sidebar/Header.
{checklist_28_landing_page}{checklist_29_motion_library}"""
