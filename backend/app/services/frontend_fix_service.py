"""
Frontend build fix agent — given a file path, its current content, and vite build
errors, asks the LLM to produce a corrected version of the file.
"""
import os
import re
from pathlib import Path

from app.prompts.frontend_fix_prompt import build_frontend_fix_prompt
from app.providers.ai_provider import generate_content


def _strip_fences(text: str) -> str:
    """Remove markdown code fences the LLM might wrap around the fix."""
    text = text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    return text.strip()


def generate_frontend_fix(
    project_path: str,
    file_path: str,
    build_errors: list[str],
    provider: str = "auto",
) -> str | None:
    """
    Returns the fixed file content as a string, or None if generation fails.
    `file_path` is relative to the project root (e.g. 'src/pages/Login.jsx').
    """
    full_path = os.path.join(project_path, file_path)
    if not os.path.exists(full_path):
        print(f"Frontend fix: file not found — {file_path}")
        return None

    with open(full_path, "r", encoding="utf-8") as f:
        current_content = f.read()

    prompt = build_frontend_fix_prompt(file_path, current_content, build_errors)
    print(f"Frontend fix for {file_path} ({len(build_errors)} errors)")

    text = generate_content(prompt, provider, max_tokens=8000, stage="frontend_fix")
    if not text:
        return None

    return _strip_fences(text)


def apply_frontend_fix(project_path: str, file_path: str, fixed_content: str) -> bool:
    """Write the fixed content back to disk."""
    full_path = os.path.join(project_path, file_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(fixed_content)
    print(f"Frontend fix written: {file_path}")
    return True


def _extract_file_from_error(error: str) -> str | None:
    """
    Pull the src-relative file path from a vite/esbuild error line.
    Handles both relative and absolute paths:
      src/pages/Login.jsx:12:5: ERROR: ...
      C:/Users/.../simpletodo/src/App.jsx:3:1: ERROR: ...
    """
    # Absolute path — extract the src/... portion
    m = re.search(r"[A-Za-z]?[:/\\].+?(src[\\/][\w./\\\-]+\.jsx?):\d+", error)
    if m:
        return m.group(1).replace("\\", "/")
    # Already relative
    m = re.search(r"(src/[\w./\-]+\.jsx?):\d+", error)
    if m:
        return m.group(1)
    return None


def group_frontend_errors_by_file(errors: list[str]) -> dict[str, list[str]]:
    """Group build errors by the source file they reference."""
    grouped: dict[str, list[str]] = {}
    unresolved = []

    for error in errors:
        path = _extract_file_from_error(error)
        if path:
            grouped.setdefault(path, []).append(error)
        else:
            unresolved.append(error)

    # Attach unresolved errors to App.jsx as a fallback
    if unresolved:
        grouped.setdefault("src/App.jsx", []).extend(unresolved)

    return grouped


def create_missing_stubs(project_path: str) -> int:
    """
    Walk every JSX/JS file under src/, resolve each relative import, and
    write a minimal stub for any import that doesn't match an existing file.
    Returns the number of stubs created.
    """
    src_dir = Path(project_path) / "src"
    if not src_dir.exists():
        return 0

    _IMPORT_RE = re.compile(r"""from\s+['"](\./[^'"]+|\.\.\/[^'"]+)['"]""")
    stubs = 0

    for jsx_file in src_dir.rglob("*.jsx"):
        try:
            content = jsx_file.read_text(encoding="utf-8")
        except OSError:
            continue

        for m in _IMPORT_RE.finditer(content):
            rel = m.group(1)
            base = (jsx_file.parent / rel).resolve()

            # Check with common extensions/index variants
            found = False
            for ext in (".jsx", ".js", "/index.jsx", "/index.js"):
                candidate = Path(str(base) + ext) if not base.suffix else base
                if ext.startswith("/"):
                    candidate = base / ext[1:]
                elif not base.suffix:
                    candidate = Path(str(base) + ext)
                else:
                    candidate = base
                if candidate.exists():
                    found = True
                    break

            if not found:
                stub_path = Path(str(base) + ".jsx") if not base.suffix else base
                if not stub_path.exists():
                    component_name = stub_path.stem
                    stub_path.parent.mkdir(parents=True, exist_ok=True)
                    # Renders nothing: this stub exists only to satisfy an
                    # unresolved import so the build doesn't crash, not to
                    # look like a real component. Confirmed live
                    # (habit_tracker, 2026-07-30): Navbar/Sidebar stubs are
                    # layout chrome rendered on every page, so visible
                    # placeholder text here shipped straight to production
                    # as raw unstyled "NavbarSidebar" text above every page.
                    stub_path.write_text(
                        f"const {component_name} = () => null;\n"
                        f"export default {component_name};\n",
                        encoding="utf-8",
                    )
                    print(f"  Stub created (renders nothing): {stub_path.relative_to(project_path)}")
                    stubs += 1

    return stubs


def run_frontend_fix_loop(
    project_path: str,
    runner,
    provider: str = "auto",
    max_attempts: int = 6,
) -> dict:
    """
    Attempt up to max_attempts rounds of fixing vite build errors.
    Before the first vite run, create stubs for any missing imported files.
    `runner` is a FrontendRunner instance.
    Returns the final FrontendBuildResult as a dict.
    """
    # Pre-flight: fill in stubs for any missing local imports so vite
    # doesn't cascade errors across unrelated files.
    n_stubs = create_missing_stubs(project_path)
    if n_stubs:
        print(f"Pre-flight: created {n_stubs} stub file(s) for missing imports")

    result = runner.run(project_path)

    for attempt in range(1, max_attempts + 1):
        if result.success or result.node_missing:
            break

        print(f"\n=== FRONTEND FIX ATTEMPT {attempt} ===")
        print(f"  Errors: {len(result.errors)}")
        for e in result.errors[:5]:
            print(f"    {e}")

        grouped = group_frontend_errors_by_file(result.errors)
        if not grouped:
            print("  No file-level errors to fix — stopping.")
            break

        any_written = False
        for file_path, errors in grouped.items():
            fixed = generate_frontend_fix(project_path, file_path, errors, provider)
            if fixed:
                apply_frontend_fix(project_path, file_path, fixed)
                any_written = True

        if not any_written:
            print("  No fixes generated — stopping.")
            break

        result = runner.run(project_path)

    return {
        "success": result.success,
        "exit_code": result.exit_code,
        "errors": result.errors,
        "build_time": result.build_time,
        "node_missing": result.node_missing,
    }
