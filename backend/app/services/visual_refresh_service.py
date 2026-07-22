"""
Visual Refresh — retrofits an ALREADY-generated project with the same
landing page + Motion polish new generations get opt-in (see
frontend_prompt.py's LANDING PAGE section and style_system.py's
MOTION_INTENSITIES). Distinct from a full frontend regeneration: this reads
the project's OWN existing frontend files as context (not the original idea
text, which may not even be available for an app generated in a past
session) and makes one targeted LLM call scoped to exactly two changes —
add a landing page, and layer Motion onto the existing dashboard — without
touching backend/API code at all.

Intended for a small, explicitly-approved pilot batch, not an unattended
sweep across every generated project — see docs/plans for the retrofit
rollout decision.
"""
import json
import os
import re
from pathlib import Path

from app.providers.ai_provider import generate_content
from app.utils.json_cleaner import extract_json

_MOTION_DEP_RE = re.compile(r'"motion"\s*:\s*"[^"]*"')


def _read_existing_frontend(project_path: str) -> dict[str, str]:
    """Read every .jsx file under src/ plus App.jsx and package.json —
    the context the refresh call reasons from."""
    root = Path(project_path)
    files: dict[str, str] = {}
    src = root / "src"
    if src.exists():
        for p in src.rglob("*.jsx"):
            try:
                files[str(p.relative_to(root)).replace(os.sep, "/")] = p.read_text(
                    encoding="utf-8", errors="replace"
                )
            except Exception:
                continue
    pkg = root / "package.json"
    if pkg.exists():
        files["package.json"] = pkg.read_text(encoding="utf-8", errors="replace")
    return files


def _ensure_motion_dependency(project_path: str) -> bool:
    """Deterministic — same guaranteed-dependency approach as
    frontend_templates.py's PACKAGE_JSON for new generations. Returns True
    if package.json was modified."""
    pkg_path = Path(project_path) / "package.json"
    if not pkg_path.exists():
        return False
    text = pkg_path.read_text(encoding="utf-8", errors="replace")
    if _MOTION_DEP_RE.search(text) or '"motion"' in text:
        return False
    try:
        data = json.loads(text)
    except Exception:
        return False
    data.setdefault("dependencies", {})["motion"] = "^12.0.0"
    pkg_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def _build_refresh_prompt(files: dict[str, str], motion_intensity: str) -> str:
    from app.prompts.style_system import build_intensity_injection

    app_jsx = files.get("src/App.jsx", "(not found)")
    other_files = "\n\n".join(
        f"=== {path} ===\n{content[:2000]}"
        for path, content in files.items()
        if path not in ("src/App.jsx", "package.json")
    )
    intensity_block = build_intensity_injection(motion_intensity)

    return f"""You are retrofitting an EXISTING, already-shipped React + Tailwind
app with two additions only — do not touch anything else, do not redesign
existing pages, do not change backend/API integration in any file.

Infer this app's actual domain, tone, and existing gradient/color tokens
entirely from the files below (there is no separate idea/spec text — this
IS the source of truth).

CURRENT src/App.jsx:
{app_jsx}

OTHER CURRENT FRONTEND FILES (for domain/tone/token reference):
{other_files}
{intensity_block}

Make exactly these two changes:

1. Generate src/pages/LandingPage.jsx — a NEW public page (no auth, no
   sidebar), matching this app's real domain and existing gradient/color
   tokens (read them from the files above, do not invent new ones):
   hero headline naming what this app specifically does, 3 feature cards
   pulled from its real existing pages/entities, a CTA button linking to
   whatever this app's ACTUAL signup route is (check App.jsx's existing
   routes — do not assume /register if the app uses a different path), and
   a minimal top bar with a Sign In link to whatever this app's actual
   login route is. Use the same ambient background blob technique if the
   existing pages already use one (check the other files above for the
   pattern); otherwise keep it simple. Apply the MOTION INTENSITY guidance
   above to this new page.

2. Return an UPDATED src/App.jsx with EXACTLY ONE routing change: the root
   path "/" now renders the new LandingPage instead of whatever it did
   before (a redirect or otherwise) — every other route, import, and piece
   of logic in App.jsx must be preserved byte-for-byte from the version
   above, character-for-character identical except for this one route's
   element and the new LandingPage import line.

Return ONLY valid JSON, no markdown, no explanation:
{{"files": [
  {{"path": "src/pages/LandingPage.jsx", "content": "..."}},
  {{"path": "src/App.jsx", "content": "..."}}
]}}

JSON string escaping: escape newlines as \\n, double-quotes inside JSX as
\\", backslashes as \\\\. Never put a raw literal newline inside a JSON
string value.
"""


def refresh_project_visuals(
    project_path: str,
    provider: str = "auto",
    motion_intensity: str = "moderate",
) -> dict:
    """
    Retrofit one already-generated project with a landing page + Motion,
    inferring domain/tone from its own existing files.

    Returns {"success": bool, "files_written": [...], "error": str | None}.
    """
    if not Path(project_path).exists():
        return {"success": False, "files_written": [], "error": f"Project path not found: {project_path}"}

    files = _read_existing_frontend(project_path)
    if "src/App.jsx" not in files:
        return {"success": False, "files_written": [], "error": "src/App.jsx not found — not a recognizable generated frontend"}

    motion_dep_added = _ensure_motion_dependency(project_path)

    prompt = _build_refresh_prompt(files, motion_intensity)
    print(f"  [visual-refresh] {project_path}: requesting landing page + App.jsx update...")
    text = generate_content(prompt, provider, max_tokens=8000, stage="visual_refresh")
    if not text:
        return {"success": False, "files_written": [], "error": "LLM call returned no content"}

    data = extract_json(text)
    if not isinstance(data, dict) or "files" not in data:
        return {"success": False, "files_written": [], "error": "LLM response was not valid {files: [...]} JSON"}

    written = []
    root = Path(project_path)
    for f in data["files"]:
        rel_path = f.get("path", "")
        content = f.get("content", "")
        if not rel_path or not content:
            continue
        if not rel_path.startswith("src/") or ".." in rel_path:
            print(f"  [visual-refresh] Skipped unsafe path: {rel_path}")
            continue
        out_path = root / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        written.append(rel_path)
        print(f"  [visual-refresh] Wrote {rel_path}")

    if "src/pages/LandingPage.jsx" not in written:
        return {"success": False, "files_written": written, "error": "LandingPage.jsx was not in the LLM's response"}

    return {
        "success": True,
        "files_written": written,
        "motion_dependency_added": motion_dep_added,
        "error": None,
    }
