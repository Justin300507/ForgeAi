import os

from app.prompts.shared_contract import FASTAPI_CONTRACT

_REFERENCE_MAX_CHARS = 4000


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
    return f"""
You are ForgeAI Missing File Agent.

A required project file is missing.

========================================
MISSING FILE
========================================

{filepath}

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