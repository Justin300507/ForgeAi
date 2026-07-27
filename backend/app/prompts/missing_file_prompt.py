import ast
import os
import re

from app.prompts.shared_contract import FASTAPI_CONTRACT
from app.services.model_attribute_validator import _SQLA_SPECIAL_ATTRS

_REFERENCE_MAX_CHARS = 4000


def _find_page_intent(project_path, filepath):
    """
    Figure out what a generically-named scaffolded page (NewPage.jsx,
    DetailPage.jsx, etc.) is actually supposed to do, by tracing:
    filepath -> component name -> the route it's mounted at (App.jsx) ->
    the human-readable label a NavLink/Link uses for that route.

    Without this, the missing-file agent gets nothing but a bare filename
    to work from, and a filename like "NewPage" carries zero semantic
    information -- it generates a maximally-generic filler page ("New
    Page" / "This is a brand new page!" / "You can start adding content
    here."). Reproduced live: habit-forge's "Create Habit" nav link
    pointed at /habits/new -> NewPage.jsx, which rendered exactly that
    placeholder instead of a habit-creation form, even though a real,
    unused HabitForm.jsx component with a working "Create Habit" submit
    button already existed in the same project.
    """
    if not project_path:
        return None
    try:
        component = os.path.splitext(os.path.basename(filepath))[0]
        app_jsx = os.path.join(project_path, "src", "App.jsx")
        if not os.path.isfile(app_jsx):
            return None
        with open(app_jsx, "r", encoding="utf-8", errors="replace") as fh:
            app_content = fh.read()

        route_m = re.search(
            rf'<Route\s+path="([^"]+)"\s+element=\{{[^{{}}]*<{re.escape(component)}\b',
            app_content,
        )
        if not route_m:
            return None
        route_path = route_m.group(1)

        label = None
        src_dir = os.path.join(project_path, "src")
        for root, _dirs, files in os.walk(src_dir):
            for fname in files:
                if not fname.endswith((".jsx", ".js")):
                    continue
                try:
                    with open(os.path.join(root, fname), "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read()
                except Exception:
                    continue
                link_m = re.search(
                    rf'<(?:Link|NavLink)\s+to="{re.escape(route_path)}"[^>]*>(.*?)</(?:Link|NavLink)>',
                    content, re.DOTALL,
                )
                if link_m:
                    # Strip any nested JSX tags (icons etc.) and collapse whitespace
                    text = re.sub(r"<[^>]+>", " ", link_m.group(1))
                    text = re.sub(r"\{[^}]*\}", " ", text)
                    text = " ".join(text.split())
                    if text:
                        label = text
                        break
            if label:
                break

        if not label:
            return None

        # A component whose content already mentions the discovered label
        # (e.g. HabitForm.jsx has a "Create Habit" submit button) is a strong
        # signal it's the real building block for this page and just needs
        # to be wired in, rather than reinvented.
        reusable = None
        components_dir = os.path.join(project_path, "src", "components")
        if os.path.isdir(components_dir):
            for fname in sorted(os.listdir(components_dir)):
                if not fname.endswith((".jsx", ".js")):
                    continue
                try:
                    with open(os.path.join(components_dir, fname), "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read()
                except Exception:
                    continue
                if label.lower() in content.lower():
                    reusable = "src/components/" + fname
                    break

        return route_path, label, reusable
    except Exception:
        return None


def _find_reference_sibling(project_path, filepath):
    """
    Find an already-generated file in the same directory as `filepath` to use
    as a concrete style/convention reference (page layout wrapper, API
    response-shape assumptions, auth-token localStorage key, etc.).

    Without this, a scaffolded page is written in total isolation from the
    rest of the project and reliably drifts from its real conventions --
    reproduced live: a scaffolded BudgetsPage.jsx had no <Sidebar/> wrapper
    at all (every other page does) and read response.data.items off an
    endpoint that actually returns a bare array, both silently invisible to
    every automated check since none of them click through the actual page.
    """
    if not project_path:
        return None
    try:
        target = os.path.normpath(filepath).replace("\\", "/")
        directory = os.path.dirname(target)
        full_dir = os.path.join(project_path, directory)
        if not os.path.isdir(full_dir):
            return None
        target_name = os.path.basename(target)
        candidates = [
            f for f in os.listdir(full_dir)
            if f != target_name and f.endswith((".jsx", ".js", ".py"))
        ]
        # Prefer the most substantial file, not the alphabetically-first one:
        # scaffolded siblings from the same broken missing-file batch are
        # exactly as unreliable as the file we're trying to fix, and picking
        # one as the "reference" just propagates the same missing-layout /
        # wrong-response-shape bug into the next scaffolded page. A larger
        # file is a reasonable proxy for "substantive, originally-generated
        # page" rather than another thin scaffold.
        scored: list[tuple[int, str, str]] = []
        for name in candidates:
            full_path = os.path.join(full_dir, name)
            try:
                with open(full_path, "r", encoding="utf-8") as fh:
                    content = fh.read()
            except Exception:
                continue
            if len(content) < 200:
                continue  # skip near-empty stubs, not a useful reference
            scored.append((len(content), name, content))
        if not scored:
            return None
        scored.sort(key=lambda t: t[0], reverse=True)
        _size, name, content = scored[0]
        return directory + "/" + name, content[:_REFERENCE_MAX_CHARS]
    except Exception:
        pass
    return None


def _find_resource_model_and_schema(project_path, filepath):
    """
    For a missing app/routes/<resource>_routes.py file, find the REAL
    model/schema classes for that resource already on disk (if any) and
    return their actual field names as a grounding block.

    Root cause this closes: MissingEndpoint is the single most common real
    failure category by a wide margin (356 occurrences in failure_memory as
    of 2026-07-24 -- more than the next two categories combined). Unlike
    _find_reference_sibling (a DIFFERENT resource's route file, useful only
    for style/conventions), nothing previously told this prompt what fields
    THIS resource's own model/schema actually declare -- the missing-file
    agent had to guess field names from the resource name and error text
    alone. Same root cause already found and fixed for architecture-repair's
    existing_symbols={} gap: a real anti-hallucination signal exists
    elsewhere in the project and simply wasn't being read here.
    """
    if not project_path:
        return None
    target = os.path.normpath(filepath).replace("\\", "/")
    if not (target.startswith("app/routes/") and target.endswith("_routes.py")):
        return None
    stem = os.path.basename(target)[: -len("_routes.py")]
    resource_name = "".join(w.capitalize() for w in re.split(r"[_-]", stem) if w)
    if not resource_name:
        return None
    candidates = {resource_name}
    if resource_name.endswith("es") and len(resource_name) > 2:
        candidates.add(resource_name[:-2])  # Classes -> Class, Boxes -> Box
    if resource_name.endswith("s") and len(resource_name) > 1:
        candidates.add(resource_name[:-1])  # Habits -> Habit

    blocks = []
    for subdir in ("models", "schemas"):
        dir_path = os.path.join(project_path, "app", subdir)
        if not os.path.isdir(dir_path):
            continue
        for fname in sorted(os.listdir(dir_path)):
            if not fname.endswith(".py"):
                continue
            try:
                with open(os.path.join(dir_path, fname), "r", encoding="utf-8") as fh:
                    tree = ast.parse(fh.read())
            except Exception:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                # Match the resource itself or a *-suffixed variant
                # (Class, ClassCreate, ClassUpdate, ClassResponse, ...) --
                # a bare substring match would also catch an unrelated class
                # that merely contains the resource name.
                if not any(node.name == c or node.name.startswith(c) for c in candidates):
                    continue
                fields = []
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        name = item.target.id
                    elif isinstance(item, ast.Assign) and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
                        name = item.targets[0].id
                    else:
                        continue
                    if name in _SQLA_SPECIAL_ATTRS or name.startswith("__"):
                        continue
                    fields.append(name)
                if fields:
                    blocks.append(f"- {node.name} (app/{subdir}/{fname}): {', '.join(fields)}")

    if not blocks:
        return None
    return "\n".join(blocks)


def _find_app_name(project_path):
    """
    Read the real app name out of index.html's <title> (always set correctly
    by the static template/theme builder, unlike anything the missing-file
    agent would otherwise have to guess).

    Root cause this closes: a scaffolded src/components/Layout.jsx (nothing
    in _find_page_intent/_find_resource_model_and_schema covers plain
    wrapper components) had zero signal for what to call the app, so the
    LLM fell back to the generic placeholder brand name "My Application" in
    the header/footer of every single page in the project -- reproduced
    live on a real habit tracker deploy, 2026-07-27.
    """
    if not project_path:
        return None
    index_html = os.path.join(project_path, "index.html")
    if not os.path.isfile(index_html):
        return None
    try:
        with open(index_html, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except Exception:
        return None
    m = re.search(r"<title>([^<]+)</title>", content)
    if not m:
        return None
    name = m.group(1).strip()
    return name or None


_NON_ENTITY_ROUTE_FILES = {"auth_routes.py", "seed_routes.py", "stats_routes.py"}


def _find_app_root_resources(project_path):
    """
    When App.jsx itself is the missing file (the LLM's frontend response
    never included it, or it got reduced to a trivial stub), the missing-
    file agent has nothing to route to unless it's told what the backend
    actually exposes -- a bare "regenerate App.jsx" prompt produces a
    generic 2-3 route shell (login/register/home) with none of the real
    entity pages, since it has zero project context otherwise. Scans
    app/routes/ for resource route files (skipping auth/seed/stats) and
    returns the inferred Page component names, so the regenerated router
    at least references the real pages -- any that don't exist yet will
    themselves be caught by validate_frontend_imports and regenerated in
    a follow-up round, same as any other missing page.
    """
    if not project_path:
        return []
    routes_dir = os.path.join(project_path, "app", "routes")
    if not os.path.isdir(routes_dir):
        return []
    resources = []
    for fname in sorted(os.listdir(routes_dir)):
        if not fname.endswith("_routes.py") or fname in _NON_ENTITY_ROUTE_FILES:
            continue
        stem = fname[: -len("_routes.py")]
        words = re.split(r"[_-]", stem)
        page_name = "".join(w.capitalize() for w in words if w) + "Page"
        resources.append(page_name)
    return resources


def build_missing_file_prompt(
    filepath,
    error,
    project_path=None
):
    reference = _find_reference_sibling(project_path, filepath)
    reference_block = ""
    if reference:
        ref_path, ref_content = reference
        reference_block = f"""
========================================
REFERENCE FILE — MATCH THIS PROJECT'S ACTUAL CONVENTIONS
========================================

This is a real, already-working file from the SAME directory in THIS
project ({ref_path}). Match its conventions exactly: how it wraps content
(layout/sidebar components), how it reads API responses (does the backend
here return a bare array or {{"items": [...]}}?), its auth-token handling,
its error/loading state pattern, its styling approach. Do not invent a
different convention -- copy this project's real one.

--- {ref_path} ---
{ref_content}
"""

    is_app_root = os.path.normpath(filepath).replace("\\", "/").endswith("src/App.jsx")
    app_root_block = ""
    if is_app_root:
        resources = _find_app_root_resources(project_path)
        resource_lines = "\n".join(f'- ./pages/{r}' for r in resources) if resources else ""
        app_root_block = f"""
========================================
THIS IS THE APP'S ROOT ROUTER -- NOT AN ORDINARY COMPONENT
========================================

App.jsx is the top-level router for the ENTIRE application. It is NOT a
placeholder shell (`const App = () => <div>App</div>;` is never acceptable
here under any circumstance) -- every page in the app is unreachable until
this file wires up real routing.

This backend exposes these resources, each needing its own page and route
(import every one of these from './pages/{{Name}}' and mount it -- if a
page file doesn't exist yet on disk, import it anyway; a separate pass
regenerates any page that's actually missing):
{resource_lines if resource_lines else "(no additional resources detected beyond auth -- still include Login/Register/Dashboard)"}

Required structure:
- BrowserRouter + Routes from react-router-dom
- Public routes: /login -> LoginPage, /register -> RegisterPage
- A PrivateRoute wrapper (check for a token in localStorage under the key
  'token'; redirect to /login if absent) wrapping every authenticated route
- /  and /dashboard -> DashboardPage (protected)
- One protected route per resource above, at a sensible plural/kebab path
  (e.g. HabitsPage -> /habits), rendering the real imported page component
  -- never an inline placeholder div in the route's element prop
- A catch-all 404 route
"""

    intent = _find_page_intent(project_path, filepath)
    intent_block = ""
    if intent:
        route_path, label, reusable = intent
        reuse_line = (
            f"\nAn existing component, {reusable}, already contains text matching "
            f"this label -- it's very likely the real building block for this page "
            f"(a form, list, or detail view meant to be imported and wired in here), "
            f"not something to reinvent from scratch. Check it before writing new UI."
            if reusable else ""
        )
        intent_block = f"""
========================================
WHAT THIS PAGE MUST ACTUALLY DO
========================================

This file is mounted at route "{route_path}", and the app's own navigation
links to that route with the label "{label}". This is NOT a placeholder
page -- it must implement the real "{label}" feature (the appropriate
form, list, or detail view for it), using the shared axios client and
this project's real API endpoints/field names. Do NOT generate generic
filler content ("New Page" / "This is a brand new page!" / "You can
start adding content here.") -- that is a placeholder and is never
acceptable.{reuse_line}
"""

    app_name = _find_app_name(project_path)
    app_name_block = ""
    if app_name:
        app_name_block = f"""
========================================
REAL APP NAME — USE THIS FOR ANY BRANDING TEXT
========================================

This project's actual name is "{app_name}". If this file displays a title,
header, footer, or any other branding text, use "{app_name}" — never a
generic placeholder like "My Application" or "My App".
"""

    resource_grounding = _find_resource_model_and_schema(project_path, filepath)
    resource_block = ""
    if resource_grounding:
        resource_block = f"""
========================================
REAL MODEL/SCHEMA FOR THIS RESOURCE — USE THESE EXACT FIELD NAMES
========================================

These already exist in the project. Every field you reference on them
(request bodies, response fields, filters, .attr access) MUST come from
this list. Do NOT invent a field name that sounds plausible but isn't
here, and do NOT invent an unrelated model/table for this resource.

{resource_grounding}
"""

    return f"""
You are ForgeAI Missing File Agent.

A required project file is missing.

========================================
MISSING FILE
========================================

{filepath}
{intent_block}{app_root_block}{resource_block}{app_name_block}
========================================
VALIDATION ERROR
========================================

{error}
{reference_block}
========================================
YOUR TASK
========================================

Generate the COMPLETE missing file.

The generated file must be runnable.

The generated file must match the file path.

The generated file must follow ForgeAI architecture.

Return a COMPLETE file.

Never return partial code.

Never return placeholders.

{FASTAPI_CONTRACT}

========================================
ROUTE FILE RULES
========================================

If filepath contains:

app/routes/

Generate:

- APIRouter
- Valid route handlers
- Export router object

Example:

user_router = APIRouter()

========================================
MODEL FILE RULES
========================================

If filepath contains:

app/models/

Generate:

- SQLAlchemy models that inherit from Base (NOT Pydantic BaseModel)
- Import Base from app.database: from app.database import Base
- Valid SQLAlchemy column definitions
- Valid Python syntax

NEVER put Pydantic BaseModel subclasses in app/models/ — those belong in app/schemas/.

========================================
SERVICE FILE RULES
========================================

If filepath contains:

app/services/

Generate:

- Minimal service functions
- Valid imports
- Valid Python syntax

========================================
FRONTEND PAGE RULES
========================================

If filepath contains:

src/pages/

Generate:

- React component
- Default export
- Valid JSX
- ALL HTTP calls MUST use the shared axios client: import API from '../api'
  (src/api.js). NEVER use raw fetch() with relative URLs — the frontend is
  deployed on a different domain (Cloudflare Pages) than the backend (Render),
  so fetch('/todos') 404s in production. src/api.js reads VITE_API_URL and
  attaches the Authorization header automatically.
- Auth token in localStorage is ALWAYS stored under the key 'token' (never
  'access_token' — that's the API response field name, not the localStorage
  key: localStorage.setItem('token', response.data.access_token)).
- List endpoint response shape varies by route — some return a bare array,
  some return {{"items": [...], "total": n}}. Never assume; if a REFERENCE
  FILE below shows how a sibling page reads a list response, copy that
  exact pattern. Otherwise check the actual response shape via the
  validation error / architecture context rather than guessing "items".
- If this page is a private/authenticated page (dashboard, list, detail,
  settings, profile, etc.), wrap its content in the same layout/sidebar/nav
  component every other private page in this project uses (see the
  REFERENCE FILE below) — a page missing the shared layout renders with no
  navigation at all, indistinguishable from the app being broken.
- Use the EXACT field names from the backend schemas/routes referenced in the
  validation error — never guess synonyms (e.g. 'done' vs 'completed').

========================================
FRONTEND COMPONENT RULES
========================================

If filepath contains:

src/components/

Generate:

- Reusable React component
- Default export
- Valid JSX
- HTTP calls (if any) use the shared axios client: import API from '../api' —
  never raw fetch() with relative URLs.
- Layout/wrapper components (Navbar, Layout, Sidebar) that pages are nested
  inside as <Navbar><Page/></Navbar> MUST accept and render {{children}} —
  otherwise every page renders blank.
- A pure layout/wrapper component has no reason to fetch anything itself —
  do NOT invent a placeholder API call to a made-up endpoint (e.g. a fake
  "/your-endpoint" GET on mount) just to give the component something to
  do. Only add a data fetch here if the validation error or existing project
  context actually requires one; otherwise render {{children}} directly with
  no loading/error state of its own.

========================================
CONSISTENCY RULES
========================================

- Every import must exist
- Every exported symbol must exist
- No circular imports
- No broken imports
- No undefined variables

========================================
PATH RULES
========================================

The returned path MUST exactly equal:

{filepath}

========================================
OUTPUT FORMAT
========================================

Return ONLY valid JSON.

{{
    "path": "{filepath}",
    "content": "FULL FILE CONTENT"
}}

========================================
OUTPUT RULES
========================================

- JSON only
- No markdown
- No explanations
- No code fences
- No text before JSON
- No text after JSON
- Only escape these characters: \\" \\\\ \\n \\t \\r
- NEVER put a backslash before any other character

========================================
FINAL VALIDATION
========================================

Before returning:

1. Ensure path equals the supplied filepath.
2. Ensure content is a complete file.
3. Ensure syntax is valid.
4. Ensure imports are valid.
5. Ensure JSON is valid.

Return JSON only.
"""