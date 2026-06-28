"""
Differential Repair

Instead of telling the LLM "fix these errors in this file", we tell it:

  OLD (last known good) vs NEW (broken) — here is the diff.
  Diagnostics: [...].
  Fix ONLY the delta that introduced the breakage.

This approach:
  - Reduces unintended rewrites (LLM sees what changed, not the whole file)
  - Reduces regression risk (small patches, not full rewrites)
  - Is faster and cheaper (shorter prompts)
  - Works best when we have a cached known-good version

Falls back to standard fix if no known-good version is available.
"""
from __future__ import annotations

import difflib
import os
from pathlib import Path
from typing import Optional

from app.providers.ai_provider import generate_content
from app.prompts.shared_contract import FASTAPI_CONTRACT
from app.utils.json_cleaner import extract_json


def _unified_diff(old_content: str, new_content: str, filename: str) -> str:
    """Produce a unified diff string between old and new content."""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=3,
    ))
    return "".join(diff)


def _build_diff_repair_prompt(
    filepath: str,
    old_content: str,
    broken_content: str,
    diagnostics: list[str],
) -> str:
    filename = os.path.basename(filepath)
    diff = _unified_diff(old_content, broken_content, filename)

    # Keep the diff short if it's huge (broken files can be very long)
    if len(diff) > 3000:
        diff = diff[:3000] + "\n... (diff truncated)"

    diag_text = "\n".join(f"  - {d}" for d in diagnostics[:10])

    return f"""{FASTAPI_CONTRACT}

You are a surgical code repair agent. A file was working, then a change broke it.

FILE: {filepath}

DIFF (what changed — lines starting with + are new, - are removed):
```diff
{diff}
```

DIAGNOSTICS (what is broken now):
{diag_text}

TASK: Produce a fixed version of the file that:
1. Resolves the diagnostics listed above
2. Reverts ONLY the changes that caused breakage
3. Preserves all other changes that were correct

Return ONLY valid JSON (no markdown fences):
{{"path": "{filepath}", "content": "FULL CORRECTED FILE CONTENT"}}"""


def generate_diff_repair(
    filepath: str,
    broken_content: str,
    diagnostics: list[str],
    project_path: str,
    provider: str = "auto",
    known_good_content: Optional[str] = None,
) -> Optional[dict]:
    """
    Generate a targeted repair by showing the LLM what changed (the diff)
    rather than the entire broken file.

    Args:
        filepath:           Relative path within the project (e.g. "app/routes/user_routes.py")
        broken_content:     Current (broken) file content
        diagnostics:        List of error/warning strings
        project_path:       Absolute path to the project root
        provider:           LLM provider
        known_good_content: Last known good file content. If None, tries fix cache then git.

    Returns:
        {"path": str, "content": str} or None if repair unavailable.
    """
    old_content = known_good_content or _find_last_good_version(filepath, project_path)

    if not old_content:
        # No baseline — fall back to standard fix
        return None

    if old_content == broken_content:
        # No diff — nothing to repair via this path
        return None

    print(f"  [diff_repair] Repairing {filepath} using diff ({len(diagnostics)} diagnostics)")

    prompt = _build_diff_repair_prompt(filepath, old_content, broken_content, diagnostics)

    try:
        raw = generate_content(prompt, provider=provider, max_tokens=6000)
        if not raw:
            return None

        raw = raw.strip()
        if raw.startswith("```"):
            first_nl = raw.find("\n")
            raw = raw[first_nl + 1:] if first_nl != -1 else raw
        if raw.endswith("```"):
            raw = raw[:raw.rfind("```")]
        raw = raw.strip()

        result = extract_json(raw)
        if result and result.get("path") and result.get("content"):
            print(f"  [diff_repair] Repair generated for {filepath}")
            return result

    except Exception as e:
        print(f"  [diff_repair] Repair failed: {e}")

    return None


def _find_last_good_version(filepath: str, project_path: str) -> Optional[str]:
    """
    Try to find the last known-good version of a file.
    Checks:
      1. Fix cache (stored in failure_memory/repair_db.json)
      2. Git history (last committed version)
    """
    # Try fix cache
    try:
        from app.knowledge.failure_db import failure_db
        cached = failure_db.lookup_file(filepath)
        if cached:
            print(f"  [diff_repair] Using fix-cache baseline for {filepath}")
            return cached
    except Exception:
        pass

    # Try git history
    try:
        import subprocess
        result = subprocess.run(
            ["git", "show", f"HEAD:{filepath}"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout:
            print(f"  [diff_repair] Using git HEAD baseline for {filepath}")
            return result.stdout
    except Exception:
        pass

    return None


def should_use_diff_repair(filepath: str, project_path: str) -> bool:
    """
    Return True if a known-good baseline exists for this file, making
    diff repair viable. Avoids expensive LLM calls when no baseline is found.
    """
    return _find_last_good_version(filepath, project_path) is not None
