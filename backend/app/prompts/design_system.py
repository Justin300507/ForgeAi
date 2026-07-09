"""
ForgeAI Design System v1

Detects app category from the idea string and returns category-specific
design tokens injected into the frontend prompt. Every app type gets its
own color palette, icon vocabulary, chart style, and card pattern.
"""

CATEGORIES: dict[str, dict] = {
    "fitness": {
        "label": "Fitness & Health",
        "primary_name": "orange",
        "primary":  "#f97316",
        "primary_dark": "#ea580c",
        "gradient": "from-orange-500 to-amber-500",
        "sidebar": "bg-gradient-to-b from-slate-950 via-slate-950 to-orange-950",
        "sidebar_text": "text-orange-100",
        "sidebar_active": "bg-gradient-to-r from-orange-500 to-amber-500 text-white shadow-lg shadow-orange-500/30",
        "sidebar_idle": "text-orange-200/70 hover:bg-white/5 hover:text-white",
        "stat_icon_bg": "bg-gradient-to-br from-orange-50 to-orange-100 dark:from-orange-900/40 dark:to-orange-900/20",
        "stat_icon_color": "text-orange-600 dark:text-orange-400",
        "accent_badge": "bg-orange-50 text-orange-700",
        "chart_color": "#f97316",
        "chart_gradient_stop": "#fbbf24",
        "icons": ["Dumbbell", "Activity", "Flame", "Trophy", "Timer", "Heart", "TrendingUp", "Zap", "Target", "BarChart2"],
        "keywords": ["gym", "fitness", "workout", "exercise", "training", "muscle", "sport",
                     "health", "weight", "run", "yoga", "progress", "lift", "cardio", "rep", "set"],
    },
    "finance": {
        "label": "Finance & Expense",
        "primary_name": "emerald",
        "primary":  "#10b981",
        "primary_dark": "#059669",
        "gradient": "from-emerald-500 to-teal-500",
        "sidebar": "bg-gradient-to-b from-slate-950 via-slate-950 to-emerald-950",
        "sidebar_text": "text-emerald-100",
        "sidebar_active": "bg-gradient-to-r from-emerald-500 to-teal-500 text-white shadow-lg shadow-emerald-500/30",
        "sidebar_idle": "text-emerald-200/70 hover:bg-white/5 hover:text-white",
        "stat_icon_bg": "bg-gradient-to-br from-emerald-50 to-emerald-100 dark:from-emerald-900/40 dark:to-emerald-900/20",
        "stat_icon_color": "text-emerald-600 dark:text-emerald-400",
        "accent_badge": "bg-emerald-50 text-emerald-700",
        "chart_color": "#10b981",
        "chart_gradient_stop": "#34d399",
        "icons": ["DollarSign", "TrendingUp", "TrendingDown", "CreditCard", "Wallet",
                  "BarChart2", "PieChart", "Receipt", "ArrowUpRight", "ArrowDownRight"],
        "keywords": ["expense", "budget", "finance", "money", "spending", "income",
                     "saving", "payment", "invoice", "accounting", "transaction", "cost", "salary"],
    },
    "productivity": {
        "label": "Productivity & Tasks",
        "primary_name": "indigo",
        "primary":  "#6366f1",
        "primary_dark": "#4f46e5",
        "gradient": "from-indigo-500 to-violet-500",
        "sidebar": "bg-gradient-to-b from-slate-950 via-slate-900 to-indigo-950",
        "sidebar_text": "text-slate-200",
        "sidebar_active": "bg-gradient-to-r from-indigo-500 to-violet-500 text-white shadow-lg shadow-indigo-500/30",
        "sidebar_idle": "text-slate-400 hover:bg-white/5 hover:text-white",
        "stat_icon_bg": "bg-gradient-to-br from-indigo-50 to-indigo-100 dark:from-indigo-900/40 dark:to-indigo-900/20",
        "stat_icon_color": "text-indigo-600 dark:text-indigo-400",
        "accent_badge": "bg-indigo-50 text-indigo-700",
        "chart_color": "#6366f1",
        "chart_gradient_stop": "#a78bfa",
        "icons": ["CheckSquare", "Calendar", "Clock", "Target", "Layers", "Zap",
                  "Star", "ListTodo", "Kanban", "AlarmClock"],
        "keywords": ["todo", "task", "project", "productivity", "habit", "goal",
                     "tracker", "planner", "schedule", "reminder", "deadline", "kanban", "backlog"],
    },
    "social": {
        "label": "Social & Chat",
        "primary_name": "purple",
        "primary":  "#a855f7",
        "primary_dark": "#9333ea",
        "gradient": "from-purple-500 to-pink-500",
        "sidebar": "bg-gradient-to-b from-slate-950 via-slate-950 to-purple-950",
        "sidebar_text": "text-purple-100",
        "sidebar_active": "bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-lg shadow-purple-500/30",
        "sidebar_idle": "text-purple-200/70 hover:bg-white/5 hover:text-white",
        "stat_icon_bg": "bg-gradient-to-br from-purple-50 to-purple-100 dark:from-purple-900/40 dark:to-purple-900/20",
        "stat_icon_color": "text-purple-600 dark:text-purple-400",
        "accent_badge": "bg-purple-50 text-purple-700",
        "chart_color": "#a855f7",
        "chart_gradient_stop": "#e879f9",
        "icons": ["MessageCircle", "Users", "Heart", "Share2", "Bell", "AtSign",
                  "Send", "ThumbsUp", "Image", "Hash"],
        "keywords": ["chat", "social", "message", "community", "forum", "network",
                     "post", "feed", "friend", "follow", "comment", "like", "share"],
    },
    "crm": {
        "label": "CRM & Sales",
        "primary_name": "sky",
        "primary":  "#0ea5e9",
        "primary_dark": "#0284c7",
        "gradient": "from-sky-500 to-cyan-500",
        "sidebar": "bg-gradient-to-b from-slate-950 via-slate-950 to-sky-950",
        "sidebar_text": "text-sky-100",
        "sidebar_active": "bg-gradient-to-r from-sky-500 to-cyan-500 text-white shadow-lg shadow-sky-500/30",
        "sidebar_idle": "text-sky-200/70 hover:bg-white/5 hover:text-white",
        "stat_icon_bg": "bg-gradient-to-br from-sky-50 to-sky-100 dark:from-sky-900/40 dark:to-sky-900/20",
        "stat_icon_color": "text-sky-600 dark:text-sky-400",
        "accent_badge": "bg-sky-50 text-sky-700",
        "chart_color": "#0ea5e9",
        "chart_gradient_stop": "#67e8f9",
        "icons": ["Users", "Phone", "Mail", "Building2", "Handshake",
                  "PieChart", "Target", "UserPlus", "Briefcase", "TrendingUp"],
        "keywords": ["crm", "customer", "lead", "sales", "contact", "client",
                     "deal", "pipeline", "account", "relationship", "prospect", "opportunity"],
    },
    "booking": {
        "label": "Booking & Scheduling",
        "primary_name": "teal",
        "primary":  "#14b8a6",
        "primary_dark": "#0d9488",
        "gradient": "from-teal-500 to-emerald-500",
        "sidebar": "bg-gradient-to-b from-slate-950 via-slate-950 to-teal-950",
        "sidebar_text": "text-teal-100",
        "sidebar_active": "bg-gradient-to-r from-teal-500 to-emerald-500 text-white shadow-lg shadow-teal-500/30",
        "sidebar_idle": "text-teal-200/70 hover:bg-white/5 hover:text-white",
        "stat_icon_bg": "bg-gradient-to-br from-teal-50 to-teal-100 dark:from-teal-900/40 dark:to-teal-900/20",
        "stat_icon_color": "text-teal-600 dark:text-teal-400",
        "accent_badge": "bg-teal-50 text-teal-700",
        "chart_color": "#14b8a6",
        "chart_gradient_stop": "#5eead4",
        "icons": ["Calendar", "Clock", "MapPin", "CheckCircle", "Users",
                  "Star", "Ticket", "CalendarCheck", "Bell", "Navigation"],
        "keywords": ["booking", "appointment", "reservation", "schedule", "calendar",
                     "hotel", "restaurant", "service", "availability", "slot", "event"],
    },
    "ecommerce": {
        "label": "E-commerce & Marketplace",
        "primary_name": "rose",
        "primary":  "#f43f5e",
        "primary_dark": "#e11d48",
        "gradient": "from-rose-500 to-pink-500",
        "sidebar": "bg-gradient-to-b from-slate-950 via-slate-950 to-rose-950",
        "sidebar_text": "text-rose-100",
        "sidebar_active": "bg-gradient-to-r from-rose-500 to-pink-500 text-white shadow-lg shadow-rose-500/30",
        "sidebar_idle": "text-rose-200/70 hover:bg-white/5 hover:text-white",
        "stat_icon_bg": "bg-gradient-to-br from-rose-50 to-rose-100 dark:from-rose-900/40 dark:to-rose-900/20",
        "stat_icon_color": "text-rose-600 dark:text-rose-400",
        "accent_badge": "bg-rose-50 text-rose-700",
        "chart_color": "#f43f5e",
        "chart_gradient_stop": "#fb7185",
        "icons": ["ShoppingCart", "Package", "Tag", "Star", "Truck",
                  "CreditCard", "Store", "Gift", "BarChart2", "ArrowUpRight"],
        "keywords": ["shop", "store", "product", "cart", "order", "inventory",
                     "marketplace", "sell", "buy", "ecommerce", "catalog", "listing"],
    },
}

_DEFAULT_CATEGORY = "productivity"


def detect_category(idea: str) -> dict:
    """
    Detect the most likely app category from the idea string.
    Returns the full category design token dict.
    """
    idea_lower = idea.lower()
    best_match = None
    best_count = 0

    for cat_key, cat in CATEGORIES.items():
        count = sum(1 for kw in cat["keywords"] if kw in idea_lower)
        if count > best_count:
            best_count = count
            best_match = cat_key

    return CATEGORIES[best_match or _DEFAULT_CATEGORY]


def build_design_system_injection(idea: str) -> str:
    """
    Return a design-system block to inject into the frontend prompt.
    Tells the LLM exactly which colors, icons, and patterns to use for this specific app type.
    """
    ds = detect_category(idea)
    icons_str = ", ".join(ds["icons"][:8])

    from app.prompts.style_system import build_style_injection
    style_block = build_style_injection(idea, ds)

    return f"""
═══════════════════════════════════════════════════════
APP-SPECIFIC DESIGN SYSTEM  ← apply these EXACTLY
═══════════════════════════════════════════════════════

Category detected: {ds['label']}

This is the ONLY color system for this app. Every hardcoded example elsewhere
in this prompt that mentions "indigo" is a NEUTRAL PLACEHOLDER — substitute
the tokens below wherever you see it. Do not leave any indigo-* class in the
generated code unless {ds['primary_name']} literally IS indigo.

  Sidebar background:    {ds['sidebar']} {ds['sidebar_text']}
  Active nav link:       {ds['sidebar_active']}
  Idle nav link:         {ds['sidebar_idle']}
  Stat icon background:  {ds['stat_icon_bg']}
  Stat icon color:       {ds['stat_icon_color']}
  Badge:                 {ds['accent_badge']}
  Button primary:        bg-gradient-to-r {ds['gradient']} hover:opacity-90 text-white shadow-lg shadow-{ds['primary_name']}-500/25
  Chart stroke:          {ds['chart_color']}
  Chart gradient top:    {ds['primary']}  opacity 0.15
  Chart gradient bottom: {ds['chart_gradient_stop']}  opacity 0

ICON VOCABULARY  (import from lucide-react, use these icons throughout):
  {icons_str}
  — Use thematically appropriate icons from this list on every card, nav item, and button.

TAILWIND GRADIENT (use on the sidebar logo/brand area, primary buttons, hero header, and ambient background blobs):
  className="bg-gradient-to-br {ds['gradient']} ..."

GRADIENT BRAND TEXT (use for the app name in the sidebar/logo — a flat solid
color there is the #1 thing that makes a generated app look like a generic
template):
```jsx
<span className="font-bold text-lg bg-gradient-to-r {ds['gradient']} bg-clip-text text-transparent">
  AppName
</span>
```

AMBIENT BACKGROUND (mandatory on the authenticated app shell — this is what
makes the app feel alive instead of a flat wireframe. Two blurred, low-opacity
gradient blobs, fixed so they don't scroll, placed BEHIND the content with
negative z-index. Purely decorative — do not let them intercept clicks):
```jsx
<div className="fixed inset-0 overflow-hidden pointer-events-none -z-10">
  <div className="animate-float-slow absolute -top-24 -right-24 w-96 h-96 rounded-full bg-gradient-to-br {ds['gradient']} opacity-20 dark:opacity-10 blur-3xl" />
  <div className="animate-float-slower absolute bottom-0 left-1/4 w-72 h-72 rounded-full bg-gradient-to-br {ds['gradient']} opacity-10 dark:opacity-[0.07] blur-3xl" />
</div>
```
The float classes are pre-defined motion tokens (slow transform-only drift) —
they make the background feel alive instead of a static wallpaper.
Place this as the FIRST child inside the outermost app-shell div (the one with
`min-h-screen`), a sibling of the sidebar/main, not nested inside `<main>`.

EXAMPLE SIDEBAR (complete — adapt nav links to your pages):
IMPORTANT: define navClass as a plain named function that returns ONE of two
full class strings via a simple ternary. Do NOT inline a template literal with a
nested `${{isActive ? ...}}` inside the JSX className attribute — that extra
brace nesting is the #1 cause of "Expected '}}' but found isActive" build
failures. `className={{navClass}}` must contain nothing but the identifier.
```jsx
// Define ONCE inside the component, before the return statement:
const navClass = ({{ isActive }}) =>
  isActive
    ? 'flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition-all duration-200 {ds['sidebar_active']}'
    : 'flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition-all duration-200 {ds['sidebar_idle']}';

<aside className="w-56 {ds['sidebar']} {ds['sidebar_text']} flex flex-col px-3 py-4 fixed h-full z-10">
  <div className="flex items-center gap-2 px-2 mb-6">
    <div className="w-7 h-7 rounded-lg bg-gradient-to-br {ds['gradient']} flex items-center justify-center shadow-lg shadow-{ds['primary_name']}-500/30">
      <span className="text-white text-sm font-bold">A</span>
    </div>
    <span className="font-bold text-lg bg-gradient-to-r {ds['gradient']} bg-clip-text text-transparent">AppName</span>
  </div>
  <nav className="flex-1 space-y-0.5">
    <NavLink to="/dashboard" className={{navClass}}>
      <LayoutDashboard size={{16}} /> Dashboard
    </NavLink>
  </nav>
</aside>
```

EXAMPLE STAT CARD for this category (glass-lite surface + ring + hover lift + gradient badge + tight-tracking number):
```jsx
<div className="bg-white/80 dark:bg-slate-800/70 backdrop-blur-xl rounded-xl p-5 border border-slate-100 dark:border-slate-700/60 ring-1 ring-black/5 dark:ring-white/5 shadow-sm hover:shadow-md hover:-translate-y-0.5 transition-all duration-200">
  <div className="flex items-center justify-between mb-3">
    <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">Label</p>
    <div className="{ds['stat_icon_bg']} p-2 rounded-lg">
      <{ds['icons'][0]} size={{18}} className="{ds['stat_icon_color']}" />
    </div>
  </div>
  <p className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">42</p>
  <p className="text-xs text-{ds['primary_name']}-600 mt-1">+12% this week</p>
</div>
```
Note the `/80` and `/70` opacity + `backdrop-blur-xl` on the card background —
combined with the ambient blobs behind it, this is what gives the whole app
its depth. A fully opaque `bg-white` card here looks flat again by comparison.
{style_block}"""
