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
            except Exception:
                pass

    required_endpoints = _required_endpoints_for_files(ctx, group.affected_files[:3])

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

    # ── Fix cache lookup ──────────────────────────────────────────────────────
    try:
        from app.knowledge.failure_db import fix_cache
        cache_hit = fix_cache.lookup(group.diagnostics)
        if cache_hit:
            print(f"    [fix] Cache HIT for group {group.group_id} "
                  f"(seen {cache_hit.success_count}x before) — skipping LLM")
            modified: list[str] = []
            for rel_path, content in cache_hit.fix_content.items():
                target = ctx.project_path / rel_path
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
        raw = generate_content(prompt, provider=provider, max_tokens=8000)
    except Exception as exc:
        # A specifically-routed provider (e.g. model_router picked "groq") has
        # no fallback of its own -- only provider="auto" does. Retry through
        # the auto chain once rather than losing this entire fix attempt to
        # one exhausted provider's rate limit / credit error.
        if provider != "auto":
            print(f"    [fix] {provider} failed ({exc}) — retrying via auto-fallback chain")
            try:
                raw = generate_content(prompt, provider="auto", max_tokens=8000)
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

    modified: list[str] = []
    fix_content_map: dict[str, str] = {}
    for patch in patches:
        rel_path = patch.get("path", "")
        content  = patch.get("content", "")
        if not rel_path or not content:
            continue
        target = ctx.project_path / rel_path
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
            from app.services.fix_writer_service import write_fix
            fix_data = generate_architecture_fix(
                str(ctx.project_path),
                [d.message for d in group.diagnostics],
                ctx.architecture,
            )
            if fix_data:
                rel = fix_data.get("path", "")
                content = fix_data.get("content", "")
                if rel and content:
                    target = ctx.project_path / rel
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
    Snapshot of all project files before a fix.
    Can revert to this state if a regression is detected.
    """

    def __init__(self, project_path: Path):
        self.project_path = project_path
        self._snap: dict[str, bytes] = {}
        self._take()

    def _take(self):
        for f in self.project_path.rglob("*.py"):
            try:
                self._snap[str(f.relative_to(self.project_path))] = f.read_bytes()
            except Exception:
                pass
        for f in self.project_path.rglob("*.jsx"):
            try:
                self._snap[str(f.relative_to(self.project_path))] = f.read_bytes()
            except Exception:
                pass

    def revert(self):
        for rel, data in self._snap.items():
            target = self.project_path / rel
            try:
                target.write_bytes(data)
            except Exception:
                pass
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

        groups = group_diagnostics(all_diags, max_groups=6)
        print(f"  [fix] {len(all_diags)} diagnostics → {len(groups)} groups")

        # 2. Take pre-fix snapshot for regression protection
        ctx.snapshot_passing()
        snapshot = _ProjectSnapshot(ctx.project_path)

        all_modified: list[str] = []
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

        # 7. Check for regressions -- diagnostic-based AND score-based.
        # Diagnostic comparison now covers static/runtime/browser/http/perf/
        # accessibility/workflow, but LLM Judge is deliberately excluded (its
        # free-text wording varies between calls) and there may be other gaps.
        # A fix that measurably drops the score should never be kept just
        # because nothing tripped the diagnostic comparison.
        regressions = ctx.detect_regression()
        regression_detected = False
        if regressions:
            print(f"  [fix] REGRESSION: {len(regressions)} new error(s) introduced")
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
        if success and group_fix_contents:
            try:
                from app.knowledge.failure_db import fix_cache
                for g, fix_content in group_fix_contents:
                    fix_cache.store(g.diagnostics, fix_content, idea=getattr(ctx, "idea", ""))
            except Exception:
                pass

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
