"""
FixOrchestrator — holistic multi-group fix agent.

Key innovation over previous fix loops:
  - Collects ALL failures from ALL sources before making ANY fix
  - Groups related errors (same file / same root cause)
  - Fixes each group with ONE targeted LLM call (not one call per error)
  - Applies regression protection: if a fix breaks passing tests → revert
  - Supports 5-level strategy escalation via RetryManager
"""
from __future__ import annotations

import copy
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from app.core.context import (
    Diagnostic, DiagnosticGroup, ErrorCategory, ErrorSeverity,
    FixAttempt, FixStrategy, GenerationContext, StageStatus,
)
from app.repair.grouper import group_diagnostics
from app.retry.manager import RetryManager, StrategyConfig


def _safe_patch_target(project_path: Path, rel_path: str) -> Optional[Path]:
    """Resolve a patch path and refuse anything outside the project root.
    LLM fixes occasionally emit '../..' paths (one tried to overwrite
    pydantic's site-packages source) — those must never be written.
    Absolute paths are fine IF they point inside the project: fix prompts
    show diagnostics with absolute file paths, and the LLM often echoes
    them back verbatim (e.g. '/app/generated_projects/x/src/App.jsx')."""
    root = project_path.resolve()
    if os.path.isabs(rel_path):
        target = Path(rel_path).resolve()
    else:
        norm = os.path.normpath(rel_path)
        if norm.startswith(".."):
            print(f"    [fix] BLOCKED patch outside project root: {rel_path!r}")
            return None
        target = (project_path / norm).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        print(f"    [fix] BLOCKED patch outside project root: {rel_path!r}")
        return None
    return target


def _maybe_redirect_model_sibling(project_path: Path, target: Path) -> Path:
    """
    If an LLM patch targets a NEW models/schemas file whose singular/plural
    sibling already exists (app/models/user.py vs users.py), redirect the
    patch to the existing file. Writing the new sibling used to trigger the
    duplicate-model dedupe patcher, which deleted whichever file OTHER modules
    still imported — NoReferencedTableError / missing-import chaos.
    """
    if target.suffix != ".py" or target.exists():
        return target
    if target.parent.name not in ("models", "schemas"):
        return target
    stem = target.stem
    sibling_stem = stem[:-1] if stem.endswith("s") else stem + "s"
    sibling = target.with_name(sibling_stem + ".py")
    if sibling.exists():
        print(f"    [fix] Redirecting patch {target.name} -> existing sibling {sibling.name}")
        return sibling
    return target


_REL_IMPORT_RE = re.compile(r"""(?:from|import)\s+['"](\.{1,2}/[^'"]+)['"]""")


def _missing_local_imports(
    project_path: Path, target: Path, content: str, patch_rel_paths: set[str],
) -> list[str]:
    """Relative JS/JSX imports in `content` that resolve neither to a file on
    disk nor to another patch in the same fix response."""
    missing: list[str] = []
    root = project_path.resolve()
    for m in _REL_IMPORT_RE.finditer(content):
        spec = m.group(1)
        base = (target.parent / spec).resolve()
        if base.suffix:
            candidates = [base]
        else:
            candidates = [base.with_suffix(ext) for ext in (".jsx", ".js", ".tsx", ".ts")]
            candidates += [base / "index.jsx", base / "index.js"]
        if any(c.exists() for c in candidates):
            continue
        rel_candidates = set()
        for c in candidates:
            try:
                rel_candidates.add(str(c.relative_to(root)).replace("\\", "/"))
            except ValueError:
                pass
        if rel_candidates & patch_rel_paths:
            continue
        missing.append(spec)
    return missing


_IMPORT_STMT_RE = re.compile(
    r"""import\s+(?P<clause>[^;'"]+?)\s+from\s+['"](?P<spec>\.{1,2}/[^'"]+)['"]""",
    re.MULTILINE,
)


def _scaffold_missing_local_imports(
    project_path: Path, target: Path, missing_specs: list[str], content: str,
) -> list[str]:
    """Create minimal stub files for local imports a fix references but that
    don't exist yet.

    Frontend fixes routinely add an `import Navbar from '../components/Navbar'`
    (or a PrivateRoute/Layout/context) alongside the real syntax fix they were
    asked for. Rejecting the whole patch for that leaves the actual build error
    unfixed forever — the exact reason todo/habit runs stayed stuck with a
    broken vite build and never deployed. Scaffolding a valid stub lets the real
    fix land and the build pass. Stubs pass `children` through, so wrapper
    components don't blank the page; named exports alias the same passthrough so
    the bundle resolves. Only creates files inside the project tree, never
    overwrites an existing file."""
    root = project_path.resolve()
    created: list[str] = []
    for spec in missing_specs:
        base = (target.parent / spec).resolve()
        try:
            base.relative_to(root)
        except ValueError:
            continue  # never write outside the project
        stub_path = base if base.suffix else base.with_suffix(".jsx")
        if stub_path.exists():
            continue

        default_name = None
        named: list[str] = []
        for m in _IMPORT_STMT_RE.finditer(content):
            if m.group("spec") != spec:
                continue
            clause = m.group("clause").strip()
            without_named = re.sub(r"\{[^}]*\}", "", clause)
            dm = re.search(r"(\w+)", without_named)
            if dm:
                default_name = dm.group(1)
            nm = re.search(r"\{([^}]*)\}", clause)
            if nm:
                for n in nm.group(1).split(","):
                    n = n.strip().split(" as ")[-1].strip()
                    if n and n.isidentifier():
                        named.append(n)

        lines = [
            "import React from 'react';",
            "// Auto-scaffolded stub so the build resolves. Passes children",
            "// through so wrapper components don't blank the page.",
            "const Stub = ({ children }) => (children === undefined ? null : children);",
            "export default Stub;",
        ]
        for n in dict.fromkeys(named):
            if n != "default":
                lines.append(f"export const {n} = Stub;")
        try:
            stub_path.parent.mkdir(parents=True, exist_ok=True)
            stub_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            created.append(str(stub_path.relative_to(root)).replace("\\", "/"))
        except Exception as exc:
            print(f"[repair] Failed to write stub for missing import {stub_path}: {exc}")
    return created


_JSX_TAG_NAME_RE = re.compile(r"[A-Za-z][\w.]*")


def _find_jsx_element_end(content: str, start: int, mismatches: list[str]) -> int:
    """Consume one JSX element starting at content[start] == '<' (an opening
    or self-closing tag, never a bare closing tag), returning the index just
    past its end. Recurses for nested elements found anywhere while scanning
    -- both ones embedded in an attribute expression
    (`element={<PrivateRoute><X /></PrivateRoute>}`) and ones nested as
    children -- so nesting depth is simply the call stack instead of an
    explicit one, and a capitalized tag's own matching close is always
    searched for by the same call that opened it. Appends to `mismatches`
    (and stops recursing further) the moment a capitalized tag's closing tag
    turns out to be missing or wrong -- this is deliberately not a full
    JSX/JS parser, just enough of one to catch a dangling wrapper tag without
    misflagging valid nesting as broken.
    """
    n = len(content)
    i = start + 1

    if i < n and content[i] == ">":
        # JSX fragment shorthand `<>...</>` -- no name, no attributes.
        name = ""
        i += 1
        self_closing = False
    else:
        m = _JSX_TAG_NAME_RE.match(content, i)
        if not m:
            # Not actually a tag (e.g. a bare '<' from a JS comparison like
            # `a < 5` inside an attribute expression) -- leave it alone
            # rather than misinterpreting it as an element.
            return start + 1
        name = m.group(0)
        i = m.end()

        quote: Optional[str] = None
        self_closing = False
        while i < n:
            c = content[i]
            if quote:
                if c == "\\":
                    i = min(i + 2, n)
                    continue
                if c == quote:
                    quote = None
                i += 1
                continue
            if c in ("'", '"', "`"):
                quote = c
                i += 1
                continue
            if c == "=" and i + 1 < n and content[i + 1] == ">":
                i += 2  # arrow function inside an attribute value, not a tag close
                continue
            if c == "<":
                if i + 1 < n and content[i + 1] != "/":
                    if mismatches:
                        return i
                    i = _find_jsx_element_end(content, i, mismatches)
                    if mismatches:
                        return i
                    continue
                # a bare '</...' while still looking for our OWN '>' means we
                # never found our own opening tag's terminator -- give up on
                # this element rather than guessing where it ends.
                return i
            if c == "/" and i + 1 < n and content[i + 1] == ">":
                self_closing = True
                i += 2
                break
            if c == ">":
                i += 1
                break
            i += 1

    if self_closing or i >= n:
        return i

    is_component = name[:1].isupper()
    while i < n:
        if mismatches:
            return i
        if content[i] != "<":
            i += 1
            continue
        if i + 1 < n and content[i + 1] == "/":
            if i + 2 < n and content[i + 2] == ">":
                # fragment close `</>`
                if name and is_component:
                    mismatches.append(f'closing fragment "</>" does not match opening "{name}"')
                return i + 3
            cm = _JSX_TAG_NAME_RE.match(content, i + 2)
            if not cm:
                i += 1
                continue
            cname = cm.group(0)
            end = content.find(">", cm.end())
            i = end + 1 if end != -1 else n
            if is_component and cname != name:
                mismatches.append(f'closing "{cname}" tag does not match opening "{name}"')
            return i
        i = _find_jsx_element_end(content, i, mismatches)
    if is_component:
        mismatches.append(f'opening "{name}" tag has no matching closing tag')
    return i


def _jsx_tag_mismatches(content: str) -> list[str]:
    """Detect unbalanced capitalized JSX component tags (Route, PrivateRoute,
    Routes, BrowserRouter, ...) in a patch before it's ever written to disk.

    Root cause this guards against: the LLM fix loop occasionally patches
    App.jsx with a wrapper tag it opens but never closes (e.g. `<PrivateRoute>`
    around a new `<Route>` with the matching `</PrivateRoute>` missing) --
    live example: two straight fix attempts on habit_forge each reintroduced
    "Unexpected closing 'Routes' tag does not match opening 'PrivateRoute'"
    at a different line, both silently accepted by this same write path.
    Nothing catches this until the full verify cycle's frontend build step
    (esbuild), by which point a whole attempt (LLM call + install + build +
    runtime + journey tests) has been burned just to discover and revert it.
    Only capitalized (component) tags are tracked -- lowercase html tags are
    left alone to keep false-positive risk near zero.
    """
    mismatches: list[str] = []
    i = 0
    n = len(content)
    while i < n and not mismatches:
        if content[i] != "<":
            i += 1
            continue
        if i + 1 < n and content[i + 1] == "/":
            m = _JSX_TAG_NAME_RE.match(content, i + 2)
            if not m:
                i += 1
                continue
            name = m.group(0)
            end = content.find(">", m.end())
            i = end + 1 if end != -1 else n
            if name[:1].isupper():
                mismatches.append(f'closing "{name}" tag with no matching opening tag')
            continue
        m = _JSX_TAG_NAME_RE.match(content, i + 1)
        if not m:
            i += 1
            continue
        i = _find_jsx_element_end(content, i, mismatches)
    return mismatches


_MISSING_BACKEND_IMPORT_RE = re.compile(r"Missing backend import target:\s*(\S+\.py)")


def _synthesize_missing_backend_files(
    ctx: GenerationContext, diags: list[Diagnostic], provider: str,
) -> tuple[list[str], set[str]]:
    """
    Directly create files a "Missing backend import target" diagnostic points
    at, using the same generate_missing_file() LLM call the V6 generation
    loop already uses for this exact diagnostic category.

    The repair loop's group-based dispatch has no equivalent: _apply_fix_group
    patches the file that HAS the broken import, not the missing target
    itself, and _regenerate_module only ever produces route/model rewrites
    from architecture context, not an arbitrary missing schema file. Either
    way the target import is never actually created, so the diagnostic just
    persists (or a later attempt reintroduces it) until the fix loop gives up
    and reverts -- this is what stalled seed.py-related fixes at 78.8/100.

    Returns (paths written, error_ids of diagnostics this handled) so the
    caller can drop those diagnostics before grouping the rest.
    """
    from app.services.missing_file_service import generate_missing_file

    by_path: dict[str, list[Diagnostic]] = {}
    for d in diags:
        m = _MISSING_BACKEND_IMPORT_RE.search(d.message)
        if not m:
            continue
        by_path.setdefault(m.group(1), []).append(d)

    written: list[str] = []
    handled_ids: set[str] = set()
    for rel_path, path_diags in by_path.items():
        target = _safe_patch_target(ctx.project_path, rel_path)
        if target is None or target.exists():
            continue
        error_text = "\n".join(d.message for d in path_diags)
        try:
            fix = generate_missing_file(rel_path, error_text, provider, project_path=str(ctx.project_path))
        except Exception as exc:
            print(f"    [fix] Missing-file synthesis failed for {rel_path}: {exc}")
            continue
        if not fix or not fix.get("content"):
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(fix["content"], encoding="utf-8")
            written.append(rel_path)
            handled_ids.update(d.error_id for d in path_diags)
            print(f"    [fix] Synthesized missing file: {rel_path}")
        except Exception as exc:
            print(f"    [fix] Write failed for {rel_path}: {exc}")

    return written, handled_ids


# ── Group → LLM fix dispatch ──────────────────────────────────────────────────

def _required_endpoints_for_files(ctx: GenerationContext, files: list[str]) -> str:
    """
    List endpoints (from the architecture plan) that live in the given files,
    so a full-file regeneration has an explicit checklist instead of silently
    dropping endpoints it wasn't focused on fixing.
    """
    arch = ctx.architecture
    if not arch:
        return ""
    endpoints = arch.get("api_endpoints", []) if isinstance(arch, dict) else getattr(arch, "api_endpoints", [])
    if not endpoints:
        return ""
    relevant = [ep for ep in endpoints if ep.get("file") in files]
    if not relevant:
        return ""
    lines = "\n".join(f"  {ep.get('method')} {ep.get('path')}" for ep in relevant)
    return (
        "\nREQUIRED ENDPOINTS (these MUST still exist in the affected file(s) after your "
        "fix -- do not drop any of them while fixing the errors above):\n"
        f"{lines}\n"
    )


def _build_fix_prompt(
    group: DiagnosticGroup,
    ctx: GenerationContext,
    improve: bool = False,
) -> str:
    """Build a targeted fix prompt for a single DiagnosticGroup."""
    from app.prompts.shared_contract import FIXER_CONTRACT as SHARED_CONTRACT

    errors_txt = "\n".join(
        f"  [{d.severity.value.upper()}] {d.category.value}: {d.message}"
        + (f"\n    file: {d.file_path}" if d.file_path else "")
        + (f"\n    hint: {d.fix_hint}" if d.fix_hint else "")
        for d in group.diagnostics
    )

    file_contents = ""
    for fpath in group.affected_files[:3]:  # cap at 3 files per group
        full = ctx.project_path / fpath
        if full.exists():
            try:
                content = full.read_text(encoding="utf-8", errors="replace")
                file_contents += f"\n--- {fpath} ---\n{content[:3000]}\n"
            except Exception as exc:
                print(f"[repair] Could not read {fpath} for fix context (LLM will fix it blind): {exc}")

    required_endpoints = _required_endpoints_for_files(ctx, group.affected_files[:3])

    # When fixing frontend files, tell the model exactly which local modules
    # exist. Fix rewrites repeatedly imported ../components/TaskCard etc. that
    # were never generated — each such patch broke the build and got reverted,
    # stalling the whole loop.
    frontend_files_ctx = ""
    if any(f.endswith((".jsx", ".js", ".tsx", ".ts")) for f in group.affected_files):
        src_dir = ctx.project_path / "src"
        if src_dir.exists():
            existing = sorted(
                str(p.relative_to(ctx.project_path)).replace("\\", "/")
                for p in src_dir.rglob("*")
                if p.suffix in (".jsx", ".js", ".tsx", ".ts", ".css")
            )
            if existing:
                frontend_files_ctx = (
                    "\nEXISTING FRONTEND FILES (the ONLY local modules you may import):\n"
                    + "\n".join(f"  {f}" for f in existing[:60])
                    + "\nDo NOT import any local file that is not in this list. If a new "
                    "component is genuinely needed, include it as an additional patch in "
                    "your JSON array — otherwise inline the JSX directly in the page.\n"
                )

    # When a diagnostic group has no grounded affected_files at all (e.g. an
    # integration/workflow-test failure carries no file_path — see
    # engine.py's _run_workflow_tests / _diag), the model gets zero context
    # about this project's actual layout and invents a plausible-looking path
    # from training data (e.g. "app/routers/seed.py" for a project that
    # actually uses "app/routes/seed_routes.py"). That orphan file then trips
    # a "Missing backend import target" regression on the next verify pass,
    # burning a fix attempt without ever touching the real bug -- this is
    # what stalled habit_forge's seed-endpoint fix loop at 78.7/100.
    backend_files_ctx = ""
    if not group.affected_files:
        app_dir = ctx.project_path / "app"
        if app_dir.exists():
            existing = sorted(
                str(p.relative_to(ctx.project_path)).replace("\\", "/")
                for p in app_dir.rglob("*.py")
                if "__pycache__" not in p.parts
            )
            if existing:
                backend_files_ctx = (
                    "\nEXISTING BACKEND FILES (this project's real layout -- fix code "
                    "that already lives in one of these; do NOT invent a new path or "
                    "naming convention like app/routers/x.py unless none of these are "
                    "relevant to the errors above):\n"
                    + "\n".join(f"  {f}" for f in existing[:80])
                    + "\n"
                )

    extra_context = ""
    if improve:
        extra_context = (
            "\n\nIMPORTANT: Previous fix attempts failed. Be more thorough this time.\n"
            "- Rewrite the ENTIRE affected function/class if needed\n"
            "- Don't just patch the error line; check the full surrounding logic\n"
            "- Ensure all imports are present\n"
        )

    return f"""You are fixing bugs in a generated FastAPI + React project.

{SHARED_CONTRACT}

ROOT CAUSE: {group.root_cause}

ERRORS TO FIX:
{errors_txt}

AFFECTED FILES:
{file_contents}
{required_endpoints}
{frontend_files_ctx}
{backend_files_ctx}
{extra_context}

Return a JSON array of file patches. Each patch must be:
{{
  "path": "relative/path/from/project/root.py",
  "content": "COMPLETE new file content (not a diff)"
}}

Rules:
- Return ONLY valid JSON array, no markdown fences
- Write COMPLETE file content, not snippets
- Fix ALL errors listed above
- Do not break any existing working functionality
- If a REQUIRED ENDPOINTS list is present above, every one of those routes must
  still be defined in your rewritten file -- do not drop, rename, or merge them
- Do NOT write out your reasoning, alternatives you considered, or a running
  commentary as comments inside the file content. Decide the fix silently and
  write only the final, correct code (plus comments that would normally
  belong in the file, if any). Verbose reasoning-as-comments wastes tokens on
  every fix call and this system runs on a tight budget.
"""


_SAFE_SEED_ROUTES_STUB = (
    "from fastapi import APIRouter, Depends\n"
    "from sqlalchemy.orm import Session\n"
    "from app.database import get_db\n\n"
    "seed_router = APIRouter()\n\n"
    "@seed_router.post('/seed')\n"
    "def seed_data(db: Session = Depends(get_db)):\n"
    "    return {'seeded': True, 'message': 'Demo data ready'}\n"
)


def _is_seed_related_group(group: DiagnosticGroup) -> bool:
    """
    True only if EVERY diagnostic in the group is about /seed or
    seed_routes.py -- conservative on purpose so a mixed group (seed plus
    something unrelated and important) never gets short-circuited.
    """
    if not group.diagnostics:
        return False
    for d in group.diagnostics:
        haystack = (d.message or "").lower() + (d.file_path or "").lower()
        if "seed" not in haystack:
            return False
    return True


def _apply_fix_group(
    group: DiagnosticGroup,
    ctx: GenerationContext,
    cfg: StrategyConfig,
) -> tuple[list[str], dict[str, str]]:
    """
    Fix a DiagnosticGroup: check fix cache first, then LLM if no cache hit.
    Returns (file paths modified, {path: content} of what was written this
    call) -- the caller decides whether to actually commit this to the fix
    cache, only after verification confirms the fix helped.
    """
    from app.providers.ai_provider import generate_content
    from app.utils.json_cleaner import extract_json

    # seed_routes.py is a self-contained demo-data stub with no project
    # context -- the LLM has no way to know what "seed" means for this
    # specific app, and reliably invents nonexistent paths trying anyway
    # (app/models/seed.py, app/schemas/seed.py, app/utils/exceptions.py all
    # seen live across two separate generations). The inner per-file fix
    # loop already never calls the LLM for this file for the same reason;
    # this group-based outer loop had no equivalent guard and kept burning
    # whole fix attempts re-discovering the same dead end. Always write the
    # known-good minimal stub instead.
    if _is_seed_related_group(group):
        target = _safe_patch_target(ctx.project_path, "app/routes/seed_routes.py")
        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_SAFE_SEED_ROUTES_STUB, encoding="utf-8")
            print(f"    [fix] Seed-related group {group.group_id}: "
                  f"writing known-good seed_routes.py stub instead of calling the LLM")
            return (["app/routes/seed_routes.py"],
                    {"app/routes/seed_routes.py": _SAFE_SEED_ROUTES_STUB})

    # ── Fix cache lookup ──────────────────────────────────────────────────────
    try:
        from app.knowledge.failure_db import fix_cache
        cache_hit = fix_cache.lookup(group.diagnostics)
        if cache_hit:
            print(f"    [fix] Cache HIT for group {group.group_id} "
                  f"(seen {cache_hit.success_count}x before) — skipping LLM")
            modified: list[str] = []
            for rel_path, content in cache_hit.fix_content.items():
                target = _safe_patch_target(ctx.project_path, rel_path)
                if target is None:
                    continue
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                    modified.append(rel_path)
                    print(f"    [fix] (cache) Patched: {rel_path}")
                except Exception as exc:
                    print(f"    [fix] (cache) Write failed for {rel_path}: {exc}")
            return modified, dict(cache_hit.fix_content)
    except Exception:
        pass  # cache unavailable — fall through to LLM

    prompt   = _build_fix_prompt(group, ctx, improve=cfg.improve_prompt)

    # Smart model routing: use cheaper models for simple fix types
    if cfg.provider in ("auto", ""):
        try:
            from app.providers.model_router import route_for_fix
            routing = route_for_fix(
                idea=getattr(ctx, "idea", ""),
                fix_strategy=cfg.strategy.value,
                provider="auto",
            )
            provider = routing.provider
            print(f"    [fix] Router: {routing.reasoning}")
        except Exception:
            provider = "auto"
    else:
        provider = cfg.provider

    try:
        # thinking_budget gives Gemini a dedicated reasoning channel instead of
        # zero -- without it, any "thinking" it wants to do has nowhere to go
        # but directly into the visible response, which showed up as pages of
        # narrated reasoning written as comments inside the generated file
        # content (thousands of wasted tokens per fix call). architecture_fix_
        # service.py already does this (thinking_budget=512); this call site
        # never did.
        raw = generate_content(prompt, provider=provider, max_tokens=8000, thinking_budget=512, stage="fix")
    except Exception as exc:
        # A specifically-routed provider (e.g. model_router picked "groq") has
        # no fallback of its own -- only provider="auto" does. Retry through
        # the auto chain once rather than losing this entire fix attempt to
        # one exhausted provider's rate limit / credit error.
        if provider != "auto":
            print(f"    [fix] {provider} failed ({exc}) — retrying via auto-fallback chain")
            try:
                raw = generate_content(prompt, provider="auto", max_tokens=8000, thinking_budget=512, stage="fix")
            except Exception as exc2:
                print(f"    [fix] LLM call failed for group {group.group_id}: {exc2}")
                return [], {}
        else:
            print(f"    [fix] LLM call failed for group {group.group_id}: {exc}")
            return [], {}

    try:
        patches = extract_json(raw)
        if isinstance(patches, dict):
            patches = [patches]
    except Exception as exc:
        print(f"    [fix] JSON parse failed: {exc}")
        return [], {}

    # Resolve every patch target first so imports of files provided IN THIS
    # SAME response count as satisfied when validating each patch below.
    root = ctx.project_path.resolve()
    resolved: list[tuple[str, Path, str]] = []
    patch_rel_paths: set[str] = set()
    for patch in patches:
        rel_path = patch.get("path", "")
        content  = patch.get("content", "")
        if not rel_path or not content:
            continue
        target = _safe_patch_target(ctx.project_path, rel_path)
        if target is None:
            continue
        target = _maybe_redirect_model_sibling(ctx.project_path, target)
        resolved.append((rel_path, target, content))
        try:
            patch_rel_paths.add(str(target.relative_to(root)).replace("\\", "/"))
        except ValueError:
            pass

    modified: list[str] = []
    fix_content_map: dict[str, str] = {}
    for rel_path, target, content in resolved:
        # A frontend patch that imports local modules that don't exist (and
        # aren't part of this patch set) is guaranteed to break the vite
        # build — reject it up front instead of burning a full verify cycle
        # discovering the regression and reverting.
        if target.suffix in (".jsx", ".js", ".tsx", ".ts"):
            missing = _missing_local_imports(ctx.project_path, target, content, patch_rel_paths)
            if missing:
                # Don't throw away an otherwise-good fix just because it also
                # references a component that doesn't exist yet — scaffold a
                # stub so the real fix (usually the actual build-error fix) can
                # land and the vite build can pass.
                created = _scaffold_missing_local_imports(
                    ctx.project_path, target, missing, content
                )
                if created:
                    print(f"    [fix] Scaffolded {len(created)} missing import stub(s) "
                          f"for {rel_path}: {', '.join(created[:5])}")
                    modified.extend(created)
                    for c in created:
                        try:
                            fix_content_map[c] = (root / c).read_text(encoding="utf-8")
                        except Exception as exc:
                            print(f"[repair] Could not re-read scaffolded stub {c}: {exc}")
                # Re-check (stubs now exist on disk); only reject if something
                # genuinely couldn't be resolved.
                still_missing = _missing_local_imports(
                    ctx.project_path, target, content, patch_rel_paths
                )
                if still_missing:
                    print(f"    [fix] REJECTED patch for {rel_path}: unresolvable "
                          f"local import(s): {', '.join(still_missing[:5])}")
                    continue

            tag_mismatches = _jsx_tag_mismatches(content)
            if tag_mismatches:
                print(f"    [fix] REJECTED patch for {rel_path}: unbalanced JSX tag(s) "
                      f"-- {tag_mismatches[0]}")
                continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            modified.append(rel_path)
            fix_content_map[rel_path] = content
            print(f"    [fix] Patched: {rel_path}")
        except Exception as exc:
            print(f"    [fix] Write failed for {rel_path}: {exc}")

    # NOTE: fix cache commit happens in FixOrchestrator.run_attempt(), only
    # after verification confirms this attempt actually succeeded -- see the
    # comment there for why storing eagerly here was a real bug.
    return modified, fix_content_map


def _regenerate_module(
    group: DiagnosticGroup,
    ctx: GenerationContext,
    cfg: StrategyConfig,
) -> tuple[list[str], dict[str, str]]:
    """
    Regenerate all files in the affected module group using the existing
    backend/frontend generation services. Returns (modified paths, {path:
    content}) -- same contract as _apply_fix_group, see there for why the
    fix cache is committed by the caller, not here.
    """
    affected = group.affected_files
    if not affected:
        return _apply_fix_group(group, ctx, cfg)

    # Determine if affected files are backend or frontend
    is_backend  = any("app/" in f for f in affected)
    is_frontend = any(f.endswith((".jsx", ".tsx", ".js", ".ts")) for f in affected)

    modified: list[str] = []
    fix_content_map: dict[str, str] = {}

    if is_backend:
        # Use the architecture fix service to regenerate the affected route files
        try:
            from app.services.architecture_fix_service import generate_architecture_fix
            # Signature is (architecture, validation_errors, provider, ...) --
            # passing project_path/architecture in the wrong slots used to
            # hand a dict to generate_content()'s `provider` param, and the
            # response is always {"files": [...]} (see the prompt in
            # architecture_fix_service.py), never a bare {"path", "content"}
            # object. Together those meant this strategy either crashed
            # straight into the except-fallback below or silently wrote
            # nothing, on every call.
            fix_data = generate_architecture_fix(
                ctx.architecture,
                [d.message for d in group.diagnostics],
                cfg.provider,
            )
            for f in (fix_data or {}).get("files", []):
                rel = f.get("path", "")
                content = f.get("content", "")
                if not rel or not content:
                    continue
                target = _safe_patch_target(ctx.project_path, rel)
                if target is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                modified.append(rel)
                fix_content_map[rel] = content
        except Exception as exc:
            print(f"    [fix] Module regen failed: {exc}")
            # Fall back to patch strategy
            modified, fix_content_map = _apply_fix_group(group, ctx, cfg)

    if is_frontend and not modified:
        modified, fix_content_map = _apply_fix_group(group, ctx, cfg)

    return modified, fix_content_map


def _regenerate_architecture(ctx: GenerationContext, cfg: StrategyConfig) -> list[str]:
    """
    Nuclear option: redesign the architecture and regenerate from scratch.
    Used only on attempt 5 when all other strategies failed.
    """
    print("  [fix] REGENERATE ARCHITECTURE — redesigning from idea...")
    try:
        # Re-run the V6 generation pipeline from architecture stage onward
        from app.services.v6_orchestrator import generate_project_v6
        result = generate_project_v6(
            ctx.idea,
            provider=cfg.provider,
        )
        # If successful, update context with new generation outputs
        if result.get("project_path"):
            new_path = Path(result["project_path"])
            if new_path.exists() and new_path != ctx.project_path:
                # Copy new files over existing project
                for src in new_path.rglob("*"):
                    if src.is_file():
                        rel = src.relative_to(new_path)
                        dst = ctx.project_path / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(src), str(dst))
        return ["<full-regen>"]
    except Exception as exc:
        print(f"    [fix] Architecture regen failed: {exc}")
        return []


# ── Regression protection ─────────────────────────────────────────────────────

class _ProjectSnapshot:
    """
    Snapshot of all project source files before a fix.
    Can revert to this state if a regression is detected.
    """

    # Everything an LLM fix or patcher can plausibly write. The old snapshot
    # only covered .py/.jsx, so a reverted attempt silently KEPT its changes
    # to package.json, .js/.ts modules, css, configs -- the "reverted" state
    # was actually a half-applied mix of before and after.
    _EXTS = {".py", ".jsx", ".js", ".ts", ".tsx", ".json", ".css", ".html",
             ".toml", ".yaml", ".yml", ".txt", ".env", ".md"}
    _SKIP_DIRS = {"node_modules", "dist", "build", "__pycache__", ".git",
                  "venv", ".venv"}

    def __init__(self, project_path: Path):
        self.project_path = project_path
        self._snap: dict[str, bytes] = {}
        self._take()

    def _iter_files(self):
        for root, dirs, files in os.walk(self.project_path):
            dirs[:] = [d for d in dirs if d not in self._SKIP_DIRS]
            for name in files:
                f = Path(root) / name
                if f.suffix.lower() in self._EXTS:
                    yield f

    def _take(self):
        for f in self._iter_files():
            try:
                self._snap[str(f.relative_to(self.project_path))] = f.read_bytes()
            except Exception as exc:
                print(f"[fix] Snapshot failed to capture {f}: {exc} — a revert "
                      f"will NOT be able to restore this file")

    def revert(self):
        # Restore every snapshotted file...
        failures: list[str] = []
        for rel, data in self._snap.items():
            target = self.project_path / rel
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            except Exception as exc:
                failures.append(f"{rel} (restore failed: {exc})")
        # ...and remove source files the failed fix newly created, which the
        # old revert left behind (e.g. a broken new route module that main.py
        # doesn't import but py_compile still trips over).
        for f in list(self._iter_files()):
            rel = str(f.relative_to(self.project_path))
            if rel not in self._snap:
                try:
                    f.unlink()
                except Exception as exc:
                    failures.append(f"{rel} (delete failed: {exc})")
        if failures:
            # A revert that doesn't fully succeed leaves the project in a
            # half-reverted mixed state — the exact bug the _EXTS widening
            # above was meant to fix. Must NOT print an unconditional
            # success message in that case, or a corrupted state gets
            # reported (and trusted) as a clean recovery.
            print(f"    [fix] Revert INCOMPLETE — {len(failures)} file(s) could "
                  f"not be restored/removed: {'; '.join(failures[:5])}")
        else:
            print("    [fix] Reverted to pre-fix snapshot")


# ── FixOrchestrator ───────────────────────────────────────────────────────────

class FixOrchestrator:
    """
    Holistic fix agent that processes ALL failures before making ANY change.

    Algorithm per attempt:
      1. Collect all diagnostics from ctx (static + runtime + browser)
      2. Group them into DiagnosticGroups (by file / root cause)
      3. For each group (priority order):
         a. Take a project snapshot (for regression rollback)
         b. Build a targeted LLM fix prompt
         c. Apply patches
         d. Re-run verification
         e. If regression detected → revert snapshot
      4. Score the result
      5. Record attempt in ctx
    """

    def __init__(self, verification_engine=None, scoring_engine=None):
        # These are injected to avoid circular imports; default to factory pattern
        self._ve = verification_engine
        self._se = scoring_engine

    def _get_ve(self):
        if self._ve:
            return self._ve
        from app.verification.engine import VerificationEngine
        return VerificationEngine()

    def _get_se(self):
        if self._se:
            return self._se
        from app.scoring.engine import ScoringEngine
        return ScoringEngine()

    def run_attempt(
        self,
        ctx: GenerationContext,
        cfg: StrategyConfig,
    ) -> FixAttempt:
        """
        Execute one fix attempt using the given StrategyConfig.
        Returns a FixAttempt record with outcome details.
        """
        t0 = time.time()
        score_before = ctx.latest_score

        # 1. Collect + group all current diagnostics
        all_diags = ctx.all_diagnostics()
        if not all_diags:
            print("  [fix] No diagnostics to fix — skipping")
            return FixAttempt(
                attempt_number=cfg.attempt,
                strategy=cfg.strategy,
                groups=[], files_modified=[],
                provider=cfg.provider, model=cfg.model_hint,
                duration_ms=0, score_before=score_before,
                score_after=score_before, success=True,
            )

        # Use Failure Graph to suppress downstream symptoms — fix roots only
        failure_graph = getattr(ctx, "failure_graph", None)
        if failure_graph and failure_graph.suppressed_count > 0:
            root_ids = {n.diagnostic.error_id for n in failure_graph.roots}
            root_diags = [d for d in all_diags if d.error_id in root_ids]
            if root_diags:
                print(f"  [fix] Failure graph: fixing {len(root_diags)} root(s), "
                      f"skipping {failure_graph.suppressed_count} downstream symptom(s)")
                all_diags = root_diags

        # Missing-file diagnostics need a file created from scratch, not a
        # patch to some other file that imports it -- handle those directly
        # before grouping so neither dispatch path below silently no-ops on
        # them (see _synthesize_missing_backend_files docstring).
        synthesized, handled_ids = _synthesize_missing_backend_files(ctx, all_diags, cfg.provider)
        if handled_ids:
            all_diags = [d for d in all_diags if d.error_id not in handled_ids]

        groups = group_diagnostics(all_diags, max_groups=6) if all_diags else []
        print(f"  [fix] {len(all_diags)} diagnostics → {len(groups)} groups")

        # 2. Take pre-fix snapshot for regression protection
        ctx.snapshot_passing()
        snapshot = _ProjectSnapshot(ctx.project_path)

        all_modified: list[str] = list(synthesized)
        # (group, fix_content) pairs generated this attempt -- only committed
        # to the fix cache at the end, if this attempt actually succeeds.
        group_fix_contents: list[tuple[DiagnosticGroup, dict[str, str]]] = []

        # 3. Fix each group
        for g in groups:
            print(f"  [fix] Group [{g.priority}] {g.root_cause[:80]}")
            if cfg.strategy == FixStrategy.REGENERATE_ARCH:
                modified = _regenerate_architecture(ctx, cfg)
                all_modified.extend(modified)
                break  # full regen covers all groups
            elif cfg.strategy == FixStrategy.REGENERATE_MODULE:
                modified, fix_content = _regenerate_module(g, ctx, cfg)
            else:
                modified, fix_content = _apply_fix_group(g, ctx, cfg)
            all_modified.extend(modified)
            if fix_content:
                group_fix_contents.append((g, fix_content))

        # 4. Apply deterministic + preflight patches on top of LLM fixes
        if all_modified:
            try:
                from app.services.deterministic_patcher import run_deterministic_patches
                from app.services.database_patcher import patch_database_py
                from app.repair.preflight import preflight
                run_deterministic_patches(str(ctx.project_path))
                patch_database_py(str(ctx.project_path))
                preflight.run(ctx.project_path, all_diags)
            except Exception as exc:
                print(f"    [fix] Deterministic patch warning: {exc}")

        # 5. Re-verify
        ve = self._get_ve()
        ve.run(ctx)

        # 6. Score the (unreverted) post-fix state
        se = self._get_se()
        score_after_obj = se.score(ctx, attempt_number=cfg.attempt)
        score_after = score_after_obj.overall

        # 7. Check for regressions -- the SCORE is the arbiter, the diagnostic
        # diff is only a tiebreaker. When the pre-fix backend never started,
        # the http/browser/endpoint/journey stages were SKIPPED and produced
        # zero diagnostics; a fix that gets the server up makes those checks
        # run for the first time, so their findings are newly-VISIBLE, not
        # newly-INTRODUCED. Treating them as regressions reverted a
        # 42.5 -> 63 improvement and pinned runs at F: any fix good enough to
        # start the backend was guaranteed to be thrown away.
        regressions = ctx.detect_regression()
        regression_detected = False
        if score_after > score_before:
            if regressions:
                print(f"  [fix] {len(regressions)} newly-visible diagnostic(s), but score "
                      f"improved {score_before:.1f} -> {score_after:.1f} — keeping fix")
        elif regressions:
            print(f"  [fix] REGRESSION: {len(regressions)} new error(s) with no score "
                  f"improvement ({score_before:.1f} -> {score_after:.1f})")
            for r in regressions[:3]:
                print(f"       ↳ {r.message[:80]}")
            regression_detected = True
        elif score_after < score_before:
            print(f"  [fix] REGRESSION: score dropped {score_before:.1f} -> {score_after:.1f} "
                  f"with no new diagnostics detected -- reverting anyway")
            regression_detected = True

        if regression_detected:
            snapshot.revert()
            ve.run(ctx)
            score_after_obj = se.score(ctx, attempt_number=cfg.attempt)
            score_after = score_after_obj.overall

        se.print_report(score_after_obj)

        success = score_after > score_before and not regression_detected

        # 8. Only now commit fixes to the cache -- after we know they actually
        # helped. Storing eagerly (the old behavior) meant a fix that failed
        # or got reverted was replayed unchanged on every subsequent attempt
        # ("Cache HIT ... skipping LLM"), burning the remaining escalation
        # strategies on a patch already confirmed not to work.
        #
        # Even on a successful attempt, only SOME groups are usually fixed. The
        # overall score is the success gate, but it can't tell which group's
        # patch earned the improvement. Caching every group's patch on any
        # score gain poisons the cache: a group's no-op patch gets recorded as
        # "the fix", and every later attempt replays it via a cache HIT instead
        # of re-diagnosing with the LLM. Seen on habit_forge — attempt 1 rose
        # 49->65 purely from the frontend-build fix, but the CRUD-500 group's
        # useless app/database.py patch got cached too, so attempts 2-3 replayed
        # it and the loop stalled at 65. Gate per-group on the group's own
        # errors actually clearing: error_id is content-derived (stable across
        # runs), so a group whose error_ids are gone from the post-fix
        # diagnostics is genuinely resolved; one whose errors persist is not.
        if success and group_fix_contents:
            try:
                from app.knowledge.failure_db import fix_cache
                post_fix_ids = {d.error_id for d in ctx.all_diagnostics()}
                for g, fix_content in group_fix_contents:
                    group_ids = {d.error_id for d in g.diagnostics}
                    if group_ids & post_fix_ids:
                        # At least one of this group's errors is still present —
                        # this patch did NOT resolve the group; don't cache it.
                        continue
                    fix_cache.store(g.diagnostics, fix_content, idea=getattr(ctx, "idea", ""))
            except Exception as exc:
                print(f"[fix] fix_cache.store failed (fix worked but won't be reused): {exc}")

        elapsed = (time.time() - t0) * 1000

        attempt = FixAttempt(
            attempt_number=cfg.attempt,
            strategy=cfg.strategy,
            groups=groups,
            files_modified=list(set(all_modified)),
            provider=cfg.provider,
            model=cfg.model_hint,
            duration_ms=elapsed,
            score_before=score_before,
            score_after=score_after,
            regression_detected=regression_detected,
            success=success,
        )
        ctx.fix_attempts.append(attempt)
        return attempt
