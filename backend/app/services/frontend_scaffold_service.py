"""
Guarantees src/App.jsx exists after frontend generation.

main.jsx is a fixed template (see app/templates/frontend_templates.py) that
always does `import App from './App'`. When the frontend LLM's JSON response
gets truncated or otherwise omits src/App.jsx, static validation reports a
single "Missing frontend import target: ./App" error that the per-file fix
loop cannot actually resolve: generate_missing_file() regenerates App.jsx
with zero knowledge of which pages/components were really generated, so it
reliably imports the wrong set of components and makes the error count
*worse*, triggering a revert back to the same unfixable single error forever.

This scaffolds a deterministic, correct-by-construction App.jsx from the
pages that actually exist on disk, following the exact auth/routing contract
in app/prompts/frontend_prompt.py (inline PrivateRoute + localStorage token
check, root/wildcard redirect) — so it never needs an LLM call and never
references a component that doesn't exist.
"""
import os
import re


_DEFAULT_EXPORT_PATTERNS = (
    re.compile(r"export\s+default\s+function\s+(\w+)"),
    re.compile(r"export\s+default\s+class\s+(\w+)"),
    re.compile(r"export\s+default\s+(\w+)\s*;"),
    re.compile(r"export\s+default\s+(\w+)\s*$", re.MULTILINE),
)

_PUBLIC_NAME_MARKERS = ("login", "register", "signup", "landing")


def _find_default_export_name(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return None
    for pattern in _DEFAULT_EXPORT_PATTERNS:
        m = pattern.search(content)
        if m:
            return m.group(1)
    return None


def _to_kebab(name):
    stripped = re.sub(r"Page$", "", name) or name
    words = re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", stripped)
    return "-".join(w.lower() for w in words) if words else stripped.lower()


def _route_path_for(name):
    kebab = _to_kebab(name)
    if kebab in ("dashboard", "home", "index"):
        return "/dashboard"
    return f"/{kebab}"


def ensure_app_jsx(project_path):
    """If src/App.jsx (or .js) is missing, synthesize one from src/pages/*.

    Returns True if a file was written, False if App.jsx already existed or
    there were no pages to scaffold from (nothing useful to synthesize)."""

    src_dir = os.path.join(project_path, "src")
    if os.path.exists(os.path.join(src_dir, "App.jsx")) or os.path.exists(
        os.path.join(src_dir, "App.js")
    ):
        return False

    pages_dir = os.path.join(src_dir, "pages")
    if not os.path.isdir(pages_dir):
        return False

    pages = []
    for fname in sorted(os.listdir(pages_dir)):
        if not fname.endswith((".jsx", ".js")):
            continue
        name = _find_default_export_name(os.path.join(pages_dir, fname))
        if not name:
            continue
        module = os.path.splitext(fname)[0]
        pages.append((name, module))

    if not pages:
        return False

    public_pages = [
        (n, m) for n, m in pages
        if any(marker in n.lower() for marker in _PUBLIC_NAME_MARKERS)
    ]
    private_pages = [p for p in pages if p not in public_pages]

    dashboard_page = next(
        (p for p in private_pages if _to_kebab(p[0]) in ("dashboard", "home", "index")),
        None,
    )
    default_authed_route = (
        _route_path_for(dashboard_page[0]) if dashboard_page
        else _route_path_for(private_pages[0][0]) if private_pages
        else _route_path_for(public_pages[0][0]) if public_pages
        else "/"
    )

    lines = [
        "import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'",
    ]
    for name, module in pages:
        lines.append(f"import {name} from './pages/{module}'")
    lines.append("")
    lines.append("const PrivateRoute = ({ children }) =>")
    lines.append("  localStorage.getItem('token') ? children : <Navigate to=\"/login\" replace />")
    lines.append("")
    lines.append("export default function App() {")
    lines.append("  return (")
    lines.append("    <BrowserRouter>")
    lines.append("      <Routes>")

    seen_paths = set()

    for name, _module in public_pages:
        path = _route_path_for(name)
        if path in seen_paths:
            continue
        seen_paths.add(path)
        lines.append(f'        <Route path="{path}" element={{<{name} />}} />')

    for name, _module in private_pages:
        path = _route_path_for(name)
        if path in seen_paths:
            continue
        seen_paths.add(path)
        lines.append(
            f'        <Route path="{path}" element={{<PrivateRoute><{name} /></PrivateRoute>}} />'
        )

    lines.append(f'        <Route path="/" element={{<Navigate to="{default_authed_route}" replace />}} />')
    lines.append(f'        <Route path="*" element={{<Navigate to="{default_authed_route}" replace />}} />')
    lines.append("      </Routes>")
    lines.append("    </BrowserRouter>")
    lines.append("  )")
    lines.append("}")

    with open(os.path.join(src_dir, "App.jsx"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return True
