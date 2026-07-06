"""
Multi-agent critic — reviews generated project files against the contract and
returns a structured critique with a confidence score (0-100).

Runs after the static validator passes, before runtime validation.
If confidence is below RETRY_THRESHOLD the pipeline can decide to regenerate.
"""
import os
from pathlib import Path

from app.providers.ai_provider import generate_content
from app.utils.json_cleaner import extract_json

RETRY_THRESHOLD = 40  # below this score, caller should consider full retry


_CRITIC_PROMPT = """\
You are a senior FastAPI code reviewer. Review the generated project files below
against these rules and return a JSON critique.

CONTRACT RULES (non-exhaustive):
1. No Flask / Django — FastAPI only
2. Router vars named exactly {{resource}}_router (e.g. user_router, task_router)
3. Pydantic schemas use `from_attributes = True`, NOT `orm_mode = True`
4. No bare built-in type in Depends() — e.g. Depends(str) is wrong
5. All imports absolute starting with `app.`
6. No module-level `global` statements
7. Every route handler returns a real value, not just `pass`
8. SQLAlchemy models in app/models/, Pydantic schemas in app/schemas/
9. get_db imported from app.database only
10. No async def mixing with sync db.query / db.commit

FILES:
{files_block}

Return ONLY valid JSON in this exact shape — no markdown, no explanation:
{{
  "score": <integer 0-100>,
  "critical_issues": ["<issue>", ...],
  "warnings": ["<warning>", ...],
  "summary": "<one sentence>"
}}

score 90-100 = ship it, 70-89 = minor issues, 40-69 = notable problems, 0-39 = major rework needed.
"""


def _collect_key_files(project_path: str, max_files: int = 12) -> list[dict]:
    """Return the most important Python files for review (routes + main + models)."""
    base = Path(project_path)
    priority = []

    for pattern in [
        "app/main.py",
        "app/database.py",
        "app/models/*.py",
        "app/schemas/*.py",
        "app/routes/*.py",
    ]:
        for p in sorted(base.glob(pattern)):
            if p.name != "__init__.py":
                priority.append(p)

    seen = set()
    files = []
    for p in priority:
        if p not in seen and len(files) < max_files:
            seen.add(p)
            try:
                content = p.read_text(encoding="utf-8")
                files.append({
                    "path": str(p.relative_to(base)).replace("\\", "/"),
                    "content": content[:3000],  # cap per-file to stay in token budget
                })
            except OSError:
                pass

    return files


def run_critic(project_path: str, provider: str = "auto") -> dict:
    """
    Run the critic against the generated project.
    Returns dict with keys: score, critical_issues, warnings, summary.
    Returns a safe default on any failure so the pipeline never crashes.
    """
    files = _collect_key_files(project_path)
    if not files:
        return {"score": 50, "critical_issues": [], "warnings": ["No files found to review"], "summary": "Could not read project files."}

    files_block = "\n\n".join(
        f"### {f['path']}\n```python\n{f['content']}\n```" for f in files
    )

    # Use replace() instead of .format() to avoid KeyError when generated
    # code contains curly-brace format specifiers like {resource}, {user_id}.
    prompt = _CRITIC_PROMPT.replace("{files_block}", files_block)

    try:
        raw = generate_content(prompt, provider, max_tokens=1500, stage="critic")
        if not raw:
            raise ValueError("Empty response")

        result = extract_json(raw)
        score = int(result.get("score", 50))
        return {
            "score": max(0, min(100, score)),
            "critical_issues": result.get("critical_issues", []),
            "warnings": result.get("warnings", []),
            "summary": result.get("summary", ""),
        }

    except Exception as e:
        print(f"Critic failed ({e}) — skipping")
        return {"score": 75, "critical_issues": [], "warnings": [str(e)], "summary": "Critic unavailable."}
