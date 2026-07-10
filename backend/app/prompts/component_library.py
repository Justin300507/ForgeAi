"""
ForgeAI Component Library — curated premium component patterns.

No live external-source fetch (21st.dev, npm registries) is wired into the
Python generation pipeline, and adding one would be a new, unvalidated
network dependency in a stage that already has to be fast and reliable. This
module is the deterministic, $0-cost substitute: a small hand-authored
catalog of high-quality React + Tailwind + lucide-react component patterns,
written in the exact same idiom as design_system.py's existing examples
(JSX-safety rules honored: no nested ternary-in-template-literal-in-attr,
placeholder tokens like {gradient}/{primary_name} substituted the same way).

build_component_reference(category_design_system) picks the 1-2 snippets
most relevant to that category's `components` hints (keyword match against
COMPONENTS) and returns them as reference code the frontend generator can
adapt -- "adapt, not copy": these are patterns to follow, not files to
paste verbatim (paths/data/props still need to fit the actual app).
"""

COMPONENTS: dict[str, dict] = {
    "kanban_board": {
        "keywords": ["kanban", "pipeline board", "board (to do"],
        "label": "Kanban board",
        "code": """```jsx
const COLUMNS = [
  {{ id: 'todo', label: 'To Do' }},
  {{ id: 'in_progress', label: 'In Progress' }},
  {{ id: 'done', label: 'Done' }},
];
<div className="grid grid-cols-1 md:grid-cols-3 gap-4">
  {{COLUMNS.map(col => (
    <div key={{col.id}} className="bg-slate-50 dark:bg-slate-900/50 rounded-xl p-3 space-y-2 min-h-[200px]">
      <div className="flex items-center justify-between px-1 mb-1">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{{col.label}}</p>
        <span className="text-xs text-slate-400">{{items.filter(i => i.status === col.id).length}}</span>
      </div>
      {{items.filter(i => i.status === col.id).map((item, index) => (
        <div key={{item.id}} style={{{{ animationDelay: `${{index * 40}}ms` }}}}
          className="animate-fade-in-up bg-white/80 dark:bg-slate-800/70 backdrop-blur-xl rounded-lg border border-slate-100 dark:border-slate-700/60 ring-1 ring-black/5 dark:ring-white/5 p-3 shadow-sm hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 cursor-pointer">
          <p className="text-sm font-medium text-slate-900 dark:text-white">{{item.title}}</p>
        </div>
      ))}}
    </div>
  ))}}
</div>
```""",
    },
    "streak_heatmap": {
        "keywords": ["streak calendar", "heatmap"],
        "label": "Streak calendar heatmap",
        "code": """```jsx
const last30 = Array.from({{ length: 30 }}, (_, i) => ({{
  date: i,
  active: completedDates.includes(i),
}}));
<div className="grid grid-cols-10 gap-1.5">
  {{last30.map((day, index) => (
    <div key={{day.date}} style={{{{ animationDelay: `${{index * 15}}ms` }}}}
      className={{`animate-scale-in w-full aspect-square rounded-md ${{day.active ? 'bg-gradient-to-br {gradient}' : 'bg-slate-100 dark:bg-slate-800'}}`}} />
  ))}}
</div>
```""",
    },
    "progress_ring": {
        "keywords": ["progress ring"],
        "label": "Radial progress ring",
        "code": """```jsx
import {{ RadialBarChart, RadialBar, ResponsiveContainer }} from 'recharts';
<div className="relative w-28 h-28 mx-auto">
  <ResponsiveContainer width="100%" height="100%">
    <RadialBarChart innerRadius="75%" outerRadius="100%" data={{[{{ value: progressPct, fill: '{primary}' }}]}} startAngle={{90}} endAngle={{-270}}>
      <RadialBar dataKey="value" cornerRadius={{8}} background={{{{ fill: '#e2e8f0' }}}} />
    </RadialBarChart>
  </ResponsiveContainer>
  <div className="absolute inset-0 flex items-center justify-center">
    <span className="text-xl font-bold tracking-tight text-slate-900 dark:text-white">{{progressPct}}%</span>
  </div>
</div>
```""",
    },
    "command_palette": {
        "keywords": ["command palette"],
        "label": "Command palette (Cmd+K)",
        "code": """```jsx
const [paletteOpen, setPaletteOpen] = React.useState(false);
React.useEffect(() => {{
  const onKey = (e) => {{
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {{ e.preventDefault(); setPaletteOpen(o => !o); }}
    if (e.key === 'Escape') setPaletteOpen(false);
  }};
  window.addEventListener('keydown', onKey);
  return () => window.removeEventListener('keydown', onKey);
}}, []);
{{paletteOpen && (
  <div className="fixed inset-0 z-50 flex items-start justify-center pt-24 bg-black/40 backdrop-blur-sm" onClick={{() => setPaletteOpen(false)}}>
    <div onClick={{e => e.stopPropagation()}} className="animate-scale-in w-full max-w-lg bg-white dark:bg-slate-800 rounded-2xl shadow-2xl ring-1 ring-black/5 overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-100 dark:border-slate-700">
        <Search size={{16}} className="text-slate-400" />
        <input autoFocus placeholder="Type a command or search..." className="flex-1 bg-transparent text-sm focus:outline-none text-slate-900 dark:text-white" />
      </div>
    </div>
  </div>
)}}
```""",
    },
    "sortable_table": {
        "keywords": ["data table", "sortable"],
        "label": "Sortable data table",
        "code": """```jsx
const [sortKey, setSortKey] = React.useState('name');
const [sortDir, setSortDir] = React.useState(1);
const sorted = [...rows].sort((a, b) => (a[sortKey] > b[sortKey] ? 1 : -1) * sortDir);
const toggleSort = (key) => {{ setSortDir(sortKey === key ? sortDir * -1 : 1); setSortKey(key); }};
<div className="overflow-x-auto rounded-xl border border-slate-100 dark:border-slate-700/60">
  <table className="w-full text-sm">
    <thead className="bg-slate-50 dark:bg-slate-800/50">
      <tr>
        {{COLUMNS.map(col => (
          <th key={{col.key}} onClick={{() => toggleSort(col.key)}} className="text-left px-4 py-2.5 text-xs font-medium uppercase tracking-wide text-slate-500 cursor-pointer select-none">
            {{col.label}} {{sortKey === col.key ? (sortDir === 1 ? '↑' : '↓') : ''}}
          </th>
        ))}}
      </tr>
    </thead>
    <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
      {{sorted.map((row, index) => (
        <tr key={{row.id}} style={{{{ animationDelay: `${{index * 30}}ms` }}}} className="animate-fade-in-up hover:bg-slate-50 dark:hover:bg-slate-800/40">
          <td className="px-4 py-2.5 text-slate-700 dark:text-slate-300">{{row.name}}</td>
        </tr>
      ))}}
    </tbody>
  </table>
</div>
```""",
    },
    "timeline": {
        "keywords": ["timeline"],
        "label": "Vertical activity timeline",
        "code": """```jsx
<div className="relative pl-6 space-y-6">
  <div className="absolute left-[7px] top-1 bottom-1 w-px bg-slate-200 dark:bg-slate-700" />
  {{events.map((event, index) => (
    <div key={{event.id}} style={{{{ animationDelay: `${{index * 50}}ms` }}}} className="animate-fade-in-up relative">
      <span className="absolute -left-6 top-1 w-3.5 h-3.5 rounded-full border-2 border-white dark:border-slate-900 bg-gradient-to-br {gradient}" />
      <p className="text-sm font-medium text-slate-900 dark:text-white">{{event.title}}</p>
      <p className="text-xs text-slate-500 dark:text-slate-400">{{event.timestamp}}</p>
    </div>
  ))}}
</div>
```""",
    },
    "calendar_grid": {
        "keywords": ["calendar", "time-slot grid", "floor plan"],
        "label": "Time-slot calendar grid",
        "code": """```jsx
const SLOTS = ['9:00', '10:00', '11:00', '12:00', '1:00', '2:00', '3:00', '4:00'];
<div className="grid grid-cols-4 sm:grid-cols-8 gap-2">
  {{SLOTS.map((slot, index) => {{
    const booked = bookedSlots.includes(slot);
    const slotClass = booked
      ? 'bg-slate-100 dark:bg-slate-800 text-slate-400 cursor-not-allowed'
      : 'bg-white/80 dark:bg-slate-800/70 backdrop-blur-xl border border-slate-100 dark:border-slate-700/60 hover:shadow-md hover:-translate-y-0.5 cursor-pointer text-slate-700 dark:text-slate-200';
    return (
      <button key={{slot}} disabled={{booked}} style={{{{ animationDelay: `${{index * 30}}ms` }}}}
        className={{`animate-scale-in rounded-lg py-2.5 text-xs font-medium transition-all duration-200 ${{slotClass}}`}}>
        {{slot}}
      </button>
    );
  }})}}
</div>
```""",
    },
    "stat_donut": {
        "keywords": ["donut", "funnel"],
        "label": "Category breakdown donut",
        "code": """```jsx
import {{ PieChart, Pie, Cell, ResponsiveContainer, Tooltip }} from 'recharts';
const COLORS = ['{primary}', '{primary_dark}', '#cbd5e1', '#e2e8f0'];
<ResponsiveContainer width="100%" height={{200}}>
  <PieChart>
    <Pie data={{breakdown}} dataKey="value" nameKey="label" innerRadius={{50}} outerRadius={{80}} paddingAngle={{2}}>
      {{breakdown.map((entry, index) => <Cell key={{index}} fill={{COLORS[index % COLORS.length]}} />)}}
    </Pie>
    <Tooltip contentStyle={{{{ background: '#1e293b', border: 'none', borderRadius: '8px', color: '#f1f5f9' }}}} />
  </PieChart>
</ResponsiveContainer>
```""",
    },
    "activity_feed": {
        "keywords": ["activity feed", "avatar stack"],
        "label": "Activity feed with avatar stack",
        "code": """```jsx
<div className="space-y-3">
  {{feed.map((post, index) => (
    <div key={{post.id}} style={{{{ animationDelay: `${{index * 40}}ms` }}}}
      className="animate-fade-in-up bg-white/80 dark:bg-slate-800/70 backdrop-blur-xl rounded-xl border border-slate-100 dark:border-slate-700/60 ring-1 ring-black/5 dark:ring-white/5 p-4">
      <div className="flex items-center gap-2.5 mb-2">
        <div className="w-8 h-8 rounded-full bg-gradient-to-br {gradient} flex items-center justify-center text-white text-xs font-bold">
          {{post.author[0]}}
        </div>
        <div>
          <p className="text-sm font-medium text-slate-900 dark:text-white">{{post.author}}</p>
          <p className="text-xs text-slate-400">{{post.timestamp}}</p>
        </div>
      </div>
      <p className="text-sm text-slate-600 dark:text-slate-300">{{post.content}}</p>
    </div>
  ))}}
</div>
```""",
    },
    "masonry_gallery": {
        "keywords": ["masonry", "gallery", "gallery grid"],
        "label": "Masonry image gallery",
        "code": """```jsx
<div className="columns-2 sm:columns-3 gap-4 space-y-4">
  {{items.map((item, index) => (
    <div key={{item.id}} style={{{{ animationDelay: `${{index * 50}}ms` }}}}
      className="animate-scale-in break-inside-avoid rounded-xl overflow-hidden bg-slate-100 dark:bg-slate-800 hover:shadow-lg hover:-translate-y-0.5 transition-all duration-200 cursor-pointer">
      <img src={{item.imageUrl}} alt={{item.title}} className="w-full h-auto object-cover" />
      <div className="p-3">
        <p className="text-sm font-medium text-slate-900 dark:text-white">{{item.title}}</p>
      </div>
    </div>
  ))}}
</div>
```""",
    },
    "achievement_grid": {
        "keywords": ["achievement badge", "badge grid", "leaderboard"],
        "label": "Achievement badge grid",
        "code": """```jsx
<div className="grid grid-cols-3 sm:grid-cols-4 gap-3">
  {{achievements.map((a, index) => {{
    const badgeClass = a.unlocked ? 'bg-gradient-to-br {gradient} text-white' : 'bg-slate-100 dark:bg-slate-800 text-slate-300 dark:text-slate-600';
    return (
      <div key={{a.id}} style={{{{ animationDelay: `${{index * 40}}ms` }}}} className="animate-pop flex flex-col items-center gap-1.5 text-center">
        <div className={{`w-14 h-14 rounded-2xl flex items-center justify-center shadow-sm ${{badgeClass}}`}}>
          <Trophy size={{22}} />
        </div>
        <p className="text-[11px] font-medium text-slate-600 dark:text-slate-400 leading-tight">{{a.label}}</p>
      </div>
    );
  }})}}
</div>
```""",
    },
    "streaming_chat": {
        "keywords": ["streaming chat", "response panel"],
        "label": "Streaming chat / response panel",
        "code": """```jsx
const [streaming, setStreaming] = React.useState(false);
const [displayed, setDisplayed] = React.useState('');
const streamIn = (fullText) => {{
  setStreaming(true);
  setDisplayed('');
  let i = 0;
  const timer = setInterval(() => {{
    i += 3;
    setDisplayed(fullText.slice(0, i));
    if (i >= fullText.length) {{ clearInterval(timer); setStreaming(false); }}
  }}, 20);
}};
<div className="space-y-3">
  {{messages.map((m, index) => {{
    const bubbleClass = m.role === 'user'
      ? 'ml-auto bg-gradient-to-r {gradient} text-white'
      : 'mr-auto bg-white/80 dark:bg-slate-800/70 backdrop-blur-xl border border-slate-100 dark:border-slate-700/60 text-slate-700 dark:text-slate-200';
    return (
      <div key={{m.id}} style={{{{ animationDelay: `${{index * 40}}ms` }}}}
        className={{`animate-fade-in-up max-w-[80%] rounded-2xl px-4 py-2.5 text-sm ${{bubbleClass}}`}}>
        {{m.content}}
      </div>
    );
  }})}}
  {{streaming && (
    <div className="mr-auto max-w-[80%] rounded-2xl px-4 py-2.5 text-sm bg-white/80 dark:bg-slate-800/70 border border-slate-100 dark:border-slate-700/60 text-slate-700 dark:text-slate-200">
      {{displayed}}<span className="live-dot ml-1" aria-hidden="true" />
    </div>
  )}}
</div>
```""",
    },
    "photo_card_grid": {
        "keywords": ["photography-forward", "menu grid", "destination cards", "hero imagery"],
        "label": "Photography-forward card grid",
        "code": """```jsx
<div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
  {{items.map((item, index) => (
    <div key={{item.id}} style={{{{ animationDelay: `${{index * 50}}ms` }}}}
      className="animate-fade-in-up group rounded-2xl overflow-hidden bg-white dark:bg-slate-800 border border-slate-100 dark:border-slate-700/60 shadow-sm hover:shadow-lg hover:-translate-y-1 transition-all duration-300 cursor-pointer">
      <div className="relative h-44 overflow-hidden">
        <img src={{item.imageUrl}} alt={{item.name}} className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500" />
        <div className="absolute inset-0 bg-gradient-to-t from-black/50 via-transparent to-transparent" />
        <p className="absolute bottom-2.5 left-3 text-white font-semibold drop-shadow">{{item.name}}</p>
      </div>
      <div className="p-4 flex items-center justify-between">
        <p className="text-sm text-slate-500 dark:text-slate-400">{{item.subtitle}}</p>
        <span className="text-sm font-bold tracking-tight text-slate-900 dark:text-white">{{item.price}}</span>
      </div>
    </div>
  ))}}
</div>
```""",
    },
}

# Component metadata — one entry per COMPONENTS key. `styles: "any"` means
# the pattern adapts to every visual style via the STYLE OVERRIDE rules;
# an explicit list restricts it to styles where the pattern reads natively.
# Used to annotate injected snippets (adapt/a11y guidance) and available to
# future selection agents (A/B design generation, vision critic).
COMPONENT_META: dict[str, dict] = {
    "kanban_board": {"categories": ["crm", "productivity"], "styles": "any", "complexity": "medium",
                     "motion": "staggered card entrance per column",
                     "a11y": "column headers are real text; keep cards keyboard-focusable"},
    "streak_heatmap": {"categories": ["fitness", "productivity", "education"], "styles": "any",
                       "complexity": "low", "motion": "cell-by-cell scale-in (15ms stagger)",
                       "a11y": "pair cell color with an accessible title/aria-label per day"},
    "progress_ring": {"categories": ["fitness", "productivity", "education", "finance"], "styles": "any",
                      "complexity": "low", "motion": "value animates on mount via recharts",
                      "a11y": "center label is real text, not color-only signal"},
    "command_palette": {"categories": ["ai_saas", "crm", "productivity"], "styles": "any",
                        "complexity": "medium", "motion": "scale-in entrance, Escape closes",
                        "a11y": "role=dialog, autoFocus input, Escape-to-close wired"},
    "sortable_table": {"categories": ["crm", "finance", "ecommerce", "healthcare"], "styles": "any",
                       "complexity": "medium", "motion": "row entrance stagger (30ms)",
                       "a11y": "sort state shown with a visible arrow, not color alone"},
    "timeline": {"categories": ["crm", "healthcare", "travel", "ecommerce"], "styles": "any",
                 "complexity": "low", "motion": "event-by-event fade-in-up (50ms stagger)",
                 "a11y": "timestamps are text; dots are decorative only"},
    "calendar_grid": {"categories": ["booking", "healthcare"], "styles": "any", "complexity": "medium",
                      "motion": "slot scale-in (30ms stagger)",
                      "a11y": "booked slots use disabled + muted text, never color alone"},
    "stat_donut": {"categories": ["finance", "crm", "ecommerce"], "styles": "any", "complexity": "low",
                   "motion": "recharts draw-in on mount",
                   "a11y": "pair with a text legend list; tooltip is supplementary"},
    "activity_feed": {"categories": ["social", "crm"], "styles": "any", "complexity": "low",
                      "motion": "post entrance stagger (40ms)",
                      "a11y": "avatar initials are aria-hidden; author name is the text label"},
    "masonry_gallery": {"categories": ["portfolio", "travel", "restaurant"], "styles": "any",
                        "complexity": "low", "motion": "scale-in stagger (50ms)",
                        "a11y": "every img gets a meaningful alt from the item title"},
    "achievement_grid": {"categories": ["fitness", "education"], "styles": "any", "complexity": "low",
                         "motion": "animate-pop per badge (40ms stagger)",
                         "a11y": "locked state shown via muted badge + label, not color alone"},
    "streaming_chat": {"categories": ["ai_saas", "social"], "styles": "any", "complexity": "medium",
                       "motion": "progressive text streaming + live-dot cursor",
                       "a11y": "streaming region should be aria-live=polite"},
    "photo_card_grid": {"categories": ["restaurant", "travel", "ecommerce", "portfolio"], "styles": "any",
                        "complexity": "low", "motion": "image scales inside its frame on hover (500ms)",
                        "a11y": "text over imagery always sits on the gradient contrast overlay"},
}


def select_components(ds: dict, style_key: str | None = None) -> list[str]:
    """The component keys build_component_reference will inject for this
    category+style — exposed separately so design_memory can record the
    actual component mix each generation lands on."""
    hints = " | ".join(ds.get("components", [])).lower()
    picked: list[str] = []
    for key, comp in COMPONENTS.items():
        meta = COMPONENT_META.get(key, {})
        style_fit = meta.get("styles", "any")
        if style_key and style_fit != "any" and style_key not in style_fit:
            continue
        if any(kw in hints for kw in comp["keywords"]):
            picked.append(key)
        if len(picked) == 2:
            break
    return picked


def build_component_reference(ds: dict, style_key: str | None = None) -> str:
    """Pick up to 2 curated component snippets matching this category's
    `components` hints and render them, tokens substituted with this
    category's actual design-system values. Components whose metadata
    restricts them to specific styles are skipped when they don't fit the
    assigned style. Returns "" if nothing matches (composition menu items
    covered elsewhere still apply)."""
    picked = [(key, COMPONENTS[key]) for key in select_components(ds, style_key)]

    if not picked:
        return ""

    blocks = []
    for key, comp in picked:
        code = comp["code"].format(
            gradient=ds["gradient"],
            primary=ds["primary"],
            primary_dark=ds["primary_dark"],
            primary_name=ds["primary_name"],
        )
        meta = COMPONENT_META.get(key, {})
        notes = ""
        if meta:
            notes = (f"\n  motion: {meta.get('motion', '—')}"
                     f"\n  accessibility: {meta.get('a11y', '—')}")
        blocks.append(
            f"\n{comp['label']} — reference pattern (adapt data/props to this app, "
            f"don't paste verbatim; restyle surfaces per any STYLE OVERRIDE below):{notes}\n{code}"
        )

    return "\nCOMPONENT REFERENCE (adapt, don't copy — these establish the shape/motion/token usage, not literal final content):" + "".join(blocks)
