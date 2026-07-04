import json

from app.prompts.design_system import build_design_system_injection


def build_frontend_prompt(architecture, target: str = "web", idea: str = ""):
    # Extract idea from architecture if not passed directly
    if not idea:
        idea = (
            architecture.get("project_name", "")
            + " "
            + " ".join(architecture.get("features", []))
        )
    design_system = build_design_system_injection(idea)

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
- src/index.css and src/main.jsx are already provided — DO NOT regenerate them

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
- Page entrance: wrap the main content return in a fade/slide-in —
  `<div className="animate-[fadeIn_0.3s_ease-out]">` is enough (the
  keyframe is already defined in src/index.css, do not redefine it).
- List/grid entrances stagger: when mapping over an array to render cards,
  add `style={{{{ animationDelay: `${{index * 40}}ms` }}}}` alongside the same
  fadeIn animation class so items cascade in rather than all popping at once.
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

SIDEBAR LAYOUT + NAV LINK PATTERN (use for ALL authenticated pages):
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
    className="animate-[fadeIn_0.3s_ease-out] bg-white/80 dark:bg-slate-800/70 backdrop-blur-xl rounded-xl border border-slate-100 dark:border-slate-700/60 ring-1 ring-black/5 dark:ring-white/5 p-4 flex items-center justify-between hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 cursor-pointer"
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
    <div className="absolute -top-24 -right-24 w-96 h-96 rounded-full bg-gradient-to-br {{gradient}} opacity-20 dark:opacity-10 blur-3xl" />
    <div className="absolute bottom-0 left-1/4 w-72 h-72 rounded-full bg-gradient-to-br {{gradient}} opacity-10 dark:opacity-[0.07] blur-3xl" />
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

DASHBOARD PAGE — MUST include ALL of:
  1. Page header with greeting + current date
  2. Stats grid (4 cards) — grid-cols-1 sm:grid-cols-2 lg:grid-cols-4
  3. Recharts chart (BarChart or AreaChart) in a card below stats
  4. Recent items list (last 5 entries)

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
      <Area type="monotone" dataKey="total" stroke="#6366f1" strokeWidth={{2}} fill="url(#colorTotal)" />
    </AreaChart>
  </ResponsiveContainer>
</div>
```

LIST / INDEX PAGES — MUST contain:
  - Page header with title + "Add X" button (flex justify-between items-center)
  - Search bar with value/onChange wired to a search state variable
  - Status/category filter dropdown wired to a filter state variable
  - At LEAST 5 pre-populated example items in useState initial array (use realistic domain data)
  - Empty state component if filtered list is empty (icon + "No results found" message)
  - Loading skeleton (show when fetching) using animate-pulse divs

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
  <div className={{`fixed bottom-4 right-4 px-4 py-3 rounded-xl shadow-lg text-sm font-medium text-white z-50 ${{toastColorClass}}`}}>
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

LOADING STATES — every useEffect data fetch MUST show a skeleton:
```jsx
const [loading, setLoading] = React.useState(true);
// In JSX:
{{loading ? (
  <div className="animate-pulse space-y-3">
    {{[...Array(5)].map((_, i) => (
      <div key={{i}} className="h-16 bg-slate-200 dark:bg-slate-700 rounded-xl" />
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
DARK MODE
═══════════════════════════════════════════════════════

Add a dark mode toggle button somewhere (header or settings page):
```jsx
const [dark, setDark] = React.useState(false);
React.useEffect(() => {{
  document.documentElement.classList.toggle('dark', dark);
}}, [dark]);

<button onClick={{() => setDark(d => !d)}} className="p-2 rounded-lg text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors">
  {{dark ? <Sun size={{18}} /> : <Moon size={{18}} />}}
</button>
```

═══════════════════════════════════════════════════════
API INTEGRATION
═══════════════════════════════════════════════════════

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
attempt/sleep(15000) retry loop shown above (3 attempts), showing a friendly
"Waking up the server…" status instead of a raw error while retrying.

AUTH RULES — mandatory for every app with authentication:

1. PRIVATE ROUTE — add to App.jsx to protect all authenticated pages:
```jsx
const PrivateRoute = ({{ children }}) =>
  localStorage.getItem('token') ? children : <Navigate to="/login" replace />;
```
Wrap every authenticated route: `<Route path="/dashboard" element={{<PrivateRoute><Layout><Dashboard /></Layout></PrivateRoute>}} />`

ALWAYS add a root redirect as the LAST route so `/` never shows a 404:
```jsx
<Route path="/" element={{<Navigate to="/dashboard" replace />}} />
<Route path="*" element={{<Navigate to="/dashboard" replace />}} />
```
(PrivateRoute will redirect unauthenticated users from /dashboard → /login automatically)

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
11. Verify src/api.js uses baseURL: import.meta.env.VITE_API_URL || '' — this reads the Render backend URL when deployed to Cloudflare, and falls back to relative URLs for local dev with the Vite proxy
12. Verify every auth error uses parseError() helper — never display raw detail or use plain || fallback
13. Verify signup page validates password.length >= 8 before submitting, with visible hint text
14. Verify App.jsx has PrivateRoute wrapping all authenticated routes
15. Verify all list data fetching unwraps .items: setItems(res.data.items || []) not setItems(res.data)
16. Verify signup does auto-login after account creation — NEVER redirect to /login after signup
17. Verify login stores display_name, user_id, user_email in localStorage from response
18. Verify logout clears ALL keys: token, display_name, user_id, user_email
19. Verify App.jsx has a root redirect (path="/") and wildcard (path="*") pointing to /dashboard so the root URL never shows a 404
"""
