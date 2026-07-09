"""
ForgeAI Theme Builder — per-app themed scaffolding.

Renders the three scaffold files that used to be fully static
(src/index.css, tailwind.config.js, index.html) from the SAME category and
style selection the frontend prompt already uses (design_system.detect_category
+ style_system.select_style on the same idea string), so the deterministic
scaffold and the LLM-facing prompt always agree on fonts, brand color, and
motion vocabulary.

This is what makes the design system deterministic instead of "if the LLM
remembers": the motion token library, shimmer skeletons, live-activity dots,
brand CSS variables, and the style pack's font pairing are all baked into the
scaffold by Python — the LLM only composes pages with class names that are
guaranteed to exist.

Master templates use @@MARKER@@ placeholders + .replace() rather than
f-strings: the payloads are CSS/JS/HTML full of literal braces, and marker
substitution can't silently corrupt them.

frontend_templates.py derives its static default constants from these same
templates rendered with DEFAULT_THEME, so the fallback path (theming disabled
or failed) still ships the full motion library — just in the neutral
indigo/Inter look.
"""
import re

# ── Master templates ─────────────────────────────────────────────────────────

INDEX_CSS_TEMPLATE = """@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    --bg: #f8fafc;
    --surface: #ffffff;
    --text: #0f172a;
    --muted: #64748b;
    --border: #e2e8f0;
    --accent: @@PRIMARY_DARK@@;
    --brand: @@PRIMARY@@;
    --brand-2: @@BRAND_2@@;
    --font-heading: @@FONT_HEADING@@;
    --font-body: @@FONT_BODY@@;
    --shimmer: rgba(255, 255, 255, 0.55);
  }

  .dark {
    --bg: #0f172a;
    --surface: #1e293b;
    --text: #f1f5f9;
    --muted: #94a3b8;
    --border: #334155;
    --accent: @@PRIMARY@@;
    --shimmer: rgba(255, 255, 255, 0.09);
  }

  * { box-sizing: border-box; }
  body {
    background-color: var(--bg);
    color: var(--text);
    font-family: var(--font-body);
    margin: 0;
  }
  h1, h2, h3 { font-family: var(--font-heading); }
}

/* ── Motion tokens ─────────────────────────────────────────────────────────
   Named animate-* utilities (animate-fade-in-up, animate-scale-in, ...) are
   defined in tailwind.config.js. The raw keyframes below back the arbitrary
   animate-[fadeIn_0.3s_ease-out] syntax and the .skeleton/.live-dot classes. */

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
}

@keyframes shimmerSweep {
  from { transform: translateX(-100%); }
  to   { transform: translateX(100%); }
}

@keyframes pingSlow {
  75%, 100% { transform: scale(2.4); opacity: 0; }
}

@keyframes gradientPan {
  0%   { background-position: 0% 50%; }
  50%  { background-position: 100% 50%; }
  100% { background-position: 0% 50%; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 0.01ms !important; animation-iteration-count: 1 !important; transition-duration: 0.01ms !important; }
}

@layer components {
  .card {
    @apply bg-white/80 dark:bg-slate-800/70 backdrop-blur-xl rounded-xl shadow-sm ring-1 ring-black/5 dark:ring-white/5 border border-slate-100 dark:border-slate-700/60 transition-shadow duration-200;
  }
  .btn-primary {
    @apply bg-gradient-to-r from-slate-800 to-slate-700 hover:from-slate-700 hover:to-slate-600 dark:from-slate-600 dark:to-slate-500 dark:hover:from-slate-500 dark:hover:to-slate-400 active:scale-[0.97] text-white font-medium px-4 py-2 rounded-lg transition-all duration-150 cursor-pointer;
  }
  .btn-secondary {
    @apply bg-slate-100 hover:bg-slate-200 active:scale-[0.97] dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-200 font-medium px-4 py-2 rounded-lg transition-all duration-150 cursor-pointer;
  }
  .input {
    @apply w-full border border-slate-200 dark:border-slate-600 bg-white/90 dark:bg-slate-800/90 text-slate-900 dark:text-slate-100 rounded-lg px-3 py-2 text-sm transition-shadow duration-150 focus:outline-none focus:ring-2 focus:ring-slate-400 dark:focus:ring-slate-500;
  }
  .badge {
    @apply inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium;
  }
  .stat-card {
    @apply card p-5 hover:shadow-md;
  }
  .nav-link {
    @apply flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition-colors duration-150;
  }
  .nav-link-active {
    @apply bg-indigo-50 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300;
  }
  .nav-link-idle {
    @apply text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700 hover:text-slate-900 dark:hover:text-slate-100;
  }

  /* Shimmer skeleton — shaped placeholder with a moving highlight sweep.
     Usage: <div className="skeleton h-16" /> (any width/height/rounding on top). */
  .skeleton {
    @apply relative overflow-hidden rounded-xl bg-slate-200/80 dark:bg-slate-700/60;
  }
  .skeleton::after {
    content: '';
    position: absolute;
    inset: 0;
    transform: translateX(-100%);
    background: linear-gradient(90deg, transparent, var(--shimmer), transparent);
    animation: shimmerSweep 1.6s ease-in-out infinite;
  }

  /* Live-activity indicator — brand-colored dot with an expanding ping ring.
     Usage: <span className="live-dot" /> next to "Live" / "Recent activity". */
  .live-dot {
    position: relative;
    display: inline-block;
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 9999px;
    background: var(--brand);
    flex: none;
  }
  .live-dot::after {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 9999px;
    background: inherit;
    animation: pingSlow 2s cubic-bezier(0, 0, 0.2, 1) infinite;
  }

  /* Slow-panning gradient — layer on top of bg-gradient-to-r classes for
     hero headings / brand text / primary CTAs that should feel alive. */
  .gradient-animated {
    background-size: 200% 200%;
    animation: gradientPan 8s ease infinite;
  }
}
"""

TAILWIND_CONFIG_TEMPLATE = """/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        primary: {
          50:  '#eef2ff',
          100: '#e0e7ff',
          500: '#6366f1',
          600: '#4f46e5',
          700: '#4338ca',
          900: '#312e81',
        },
        brand: {
          DEFAULT: '@@PRIMARY@@',
          dark: '@@PRIMARY_DARK@@',
          light: '@@BRAND_2@@',
        },
      },
      fontFamily: {
        sans: [@@BODY_FONT_STACK@@],
        display: [@@HEADING_FONT_STACK@@],
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'fade-in-up': {
          from: { opacity: '0', transform: 'translateY(12px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'scale-in': {
          from: { opacity: '0', transform: 'scale(0.94)' },
          to: { opacity: '1', transform: 'scale(1)' },
        },
        'slide-in-right': {
          from: { opacity: '0', transform: 'translateX(24px)' },
          to: { opacity: '1', transform: 'translateX(0)' },
        },
        'pop': {
          '0%': { opacity: '0', transform: 'scale(0.9)' },
          '60%': { opacity: '1', transform: 'scale(1.03)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        'float-slow': {
          '0%, 100%': { transform: 'translate3d(0, 0, 0)' },
          '50%': { transform: 'translate3d(20px, -30px, 0) scale(1.05)' },
        },
        'float-slower': {
          '0%, 100%': { transform: 'translate3d(0, 0, 0)' },
          '50%': { transform: 'translate3d(-24px, 20px, 0) scale(1.08)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 0.4s ease-out both',
        'fade-in-up': 'fade-in-up 0.45s cubic-bezier(0.16, 1, 0.3, 1) both',
        'scale-in': 'scale-in 0.25s cubic-bezier(0.34, 1.56, 0.64, 1) both',
        'slide-in-right': 'slide-in-right 0.35s cubic-bezier(0.16, 1, 0.3, 1) both',
        'pop': 'pop 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) both',
        'float-slow': 'float-slow 18s ease-in-out infinite',
        'float-slower': 'float-slower 26s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
"""

INDEX_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="theme-color" content="@@THEME_COLOR@@" />
    <title>@@TITLE@@</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="@@FONT_URL@@" rel="stylesheet" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
"""

PWA_INDEX_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="theme-color" content="@@THEME_COLOR@@" />
    <meta name="mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
    <meta name="apple-mobile-web-app-title" content="@@TITLE@@" />
    <link rel="manifest" href="/manifest.json" />
    <link rel="apple-touch-icon" href="/icon-192.svg" />
    <link rel="icon" type="image/svg+xml" href="/icon-192.svg" />
    <title>@@TITLE@@</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="@@FONT_URL@@" rel="stylesheet" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
    <script>
      if ('serviceWorker' in navigator) {
        window.addEventListener('load', () => {
          navigator.serviceWorker.register('/sw.js').catch(() => {});
        });
      }
    </script>
  </body>
</html>
"""

PWA_MANIFEST_TEMPLATE = """{
  "name": "@@TITLE@@",
  "short_name": "@@SHORT_TITLE@@",
  "description": "Generated by ForgeAI",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#0f172a",
  "theme_color": "@@THEME_COLOR@@",
  "orientation": "portrait-primary",
  "icons": [
    { "src": "/icon-192.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable" }
  ],
  "categories": ["productivity", "utilities"]
}
"""

_DEFAULT_FONT_URL = "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
_DEFAULT_FONT_STACK = "'Inter', system-ui, sans-serif"

DEFAULT_THEME = {
    "title": "ForgeAI App",
    "short_title": "ForgeApp",
    "theme_color": "#4f46e5",
    "primary": "#6366f1",
    "primary_dark": "#4f46e5",
    "brand_2": "#a78bfa",
    "font_url": _DEFAULT_FONT_URL,
    "font_heading": _DEFAULT_FONT_STACK,
    "font_body": _DEFAULT_FONT_STACK,
}


def _render(template: str, theme: dict) -> str:
    out = template
    for key, marker in [
        ("title", "@@TITLE@@"),
        ("short_title", "@@SHORT_TITLE@@"),
        ("theme_color", "@@THEME_COLOR@@"),
        ("primary", "@@PRIMARY@@"),
        ("primary_dark", "@@PRIMARY_DARK@@"),
        ("brand_2", "@@BRAND_2@@"),
        ("font_url", "@@FONT_URL@@"),
        ("font_heading", "@@FONT_HEADING@@"),
        ("font_body", "@@FONT_BODY@@"),
    ]:
        out = out.replace(marker, theme[key])
    out = out.replace("@@BODY_FONT_STACK@@", _js_font_stack(theme["font_body"]))
    out = out.replace("@@HEADING_FONT_STACK@@", _js_font_stack(theme["font_heading"]))
    return out


def _js_font_stack(css_stack: str) -> str:
    """'Sora', sans-serif  ->  'Sora', 'system-ui', 'sans-serif' as JS array items."""
    names = [n.strip().strip("'\"") for n in css_stack.split(",") if n.strip()]
    if "system-ui" not in names:
        names.insert(len(names) - 1 if names and names[-1].endswith("-serif") else len(names), "system-ui")
    return ", ".join(f"'{n}'" for n in names)


def _prettify_name(project_name: str) -> str:
    words = re.split(r"[_\-\s]+", (project_name or "").strip())
    pretty = " ".join(w.capitalize() for w in words if w)
    return pretty or "ForgeAI App"


def _font_url_from_import(font_import: str) -> str:
    m = re.search(r"url\(['\"]?([^'\")]+)['\"]?\)", font_import or "")
    return m.group(1) if m else _DEFAULT_FONT_URL


def build_theme(idea: str, project_name: str = "") -> dict:
    """Resolve the theme dict from the same category/style selection the
    frontend prompt uses. Empty idea falls back to the default (indigo/Inter)
    theme with just the title personalized."""
    theme = dict(DEFAULT_THEME)
    title = _prettify_name(project_name)
    theme["title"] = title
    theme["short_title"] = (title[:12].strip() or "ForgeApp")

    if not (idea or "").strip():
        return theme

    from app.prompts.design_system import detect_category
    from app.prompts.style_system import STYLES, select_style

    cat = detect_category(idea)
    theme["primary"] = cat["primary"]
    theme["primary_dark"] = cat["primary_dark"]
    theme["brand_2"] = cat.get("chart_gradient_stop", cat["primary"])
    theme["theme_color"] = cat["primary"]

    style = STYLES.get(select_style(idea), {})
    if style.get("font_import"):
        theme["font_url"] = _font_url_from_import(style["font_import"])
        theme["font_heading"] = style.get("font_heading", _DEFAULT_FONT_STACK)
        theme["font_body"] = style.get("font_body", _DEFAULT_FONT_STACK)
    return theme


def build_themed_templates(idea: str, project_name: str = "",
                           frontend_target: str = "web") -> dict:
    """Scaffold file overrides for this specific app. Returned dict is merged
    over FRONTEND_TEMPLATE_FILES (and PWA_EXTRA_FILES) by the file writer."""
    theme = build_theme(idea, project_name)
    files = {
        "src/index.css": _render(INDEX_CSS_TEMPLATE, theme),
        "tailwind.config.js": _render(TAILWIND_CONFIG_TEMPLATE, theme),
        "index.html": _render(INDEX_HTML_TEMPLATE, theme),
    }
    if frontend_target == "pwa":
        files["index.html"] = _render(PWA_INDEX_HTML_TEMPLATE, theme)
        files["public/manifest.json"] = _render(PWA_MANIFEST_TEMPLATE, theme)
    return files
