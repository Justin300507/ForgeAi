"""
ErrorGrouper — clusters related diagnostics into DiagnosticGroups.

Groups are formed by:
  1. Same affected file (co-located errors)
  2. Same error category across files (systemic patterns)
  3. Cascading import errors (one missing file causes many)

Each group gets a root cause summary and a suggested fix strategy,
so the FixOrchestrator can target one LLM call per group instead of
one call per error.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Optional

from app.core.context import (
    Diagnostic, DiagnosticGroup, ErrorCategory, ErrorSeverity, FixStrategy,
)


def _severity_rank(sev: ErrorSeverity) -> int:
    return {
        ErrorSeverity.CRITICAL: 4,
        ErrorSeverity.HIGH:     3,
        ErrorSeverity.MEDIUM:   2,
        ErrorSeverity.LOW:      1,
        ErrorSeverity.INFO:     0,
    }.get(sev, 0)


def _strategy_for_group(diags: list[Diagnostic]) -> FixStrategy:
    """Choose fix strategy based on the severity and count of diagnostics in a group."""
    max_sev   = max((_severity_rank(d.severity) for d in diags), default=0)
    count     = len(diags)
    cats      = {d.category for d in diags}

    if ErrorCategory.ARCHITECTURE in cats:
        return FixStrategy.REGENERATE_MODULE

    if max_sev >= 4 or count >= 5:          # CRITICAL or many errors
        return FixStrategy.REGENERATE_FILE

    if max_sev >= 3:                         # HIGH severity
        return FixStrategy.REGENERATE_FILE

    return FixStrategy.PATCH_FILE


def _root_cause_summary(diags: list[Diagnostic]) -> str:
    """Produce a one-sentence root-cause description for the group."""
    if not diags:
        return "Unknown error"

    # Most severe error drives the summary
    diags_sorted = sorted(diags, key=lambda d: _severity_rank(d.severity), reverse=True)
    lead = diags_sorted[0]

    prefix = {
        ErrorCategory.SYNTAX:       "Syntax error",
        ErrorCategory.IMPORT:       "Import/module error",
        ErrorCategory.RUNTIME:      "Runtime crash",
        ErrorCategory.BROWSER:      "Frontend/browser failure",
        ErrorCategory.API:          "API endpoint failure",
        ErrorCategory.DEPENDENCY:   "Missing dependency",
        ErrorCategory.ARCHITECTURE: "Architecture violation",
        ErrorCategory.CONTRACT:     "Contract violation",
        ErrorCategory.INTEGRATION:  "Frontend–backend integration failure",
        ErrorCategory.SECURITY:     "Security issue",
        ErrorCategory.ENV_VAR:      "Missing environment variable",
        ErrorCategory.REGRESSION:   "Regression introduced by previous fix",
    }.get(lead.category, "Error")

    extras = f" ({len(diags)-1} related)" if len(diags) > 1 else ""
    return f"{prefix}: {lead.message[:120]}{extras}"


def group_diagnostics(
    diagnostics: list[Diagnostic],
    max_groups: int = 8,
) -> list[DiagnosticGroup]:
    """
    Cluster diagnostics into actionable groups.

    Priority order:
      1. Groups with CRITICAL severity diagnostics
      2. Groups with HIGH severity diagnostics
      3. All others ordered by group size desc

    Returns at most max_groups groups (to limit LLM calls per fix round).
    """
    if not diagnostics:
        return []

    # ── Phase 1: group by file ────────────────────────────────────────────
    by_file: dict[str, list[Diagnostic]] = defaultdict(list)
    no_file: list[Diagnostic] = []

    for d in diagnostics:
        if d.file_path:
            by_file[d.file_path].append(d)
        else:
            no_file.append(d)

    # ── Phase 2: group no-file diagnostics by category ────────────────────
    by_category: dict[str, list[Diagnostic]] = defaultdict(list)
    for d in no_file:
        by_category[d.category.value].append(d)

    # ── Phase 3: detect cascade patterns ─────────────────────────────────
    # If there's a "ModuleNotFoundError" for the same missing module in many files,
    # group ALL those diagnostics together (fixing the one missing file fixes them all)
    cascade_key: dict[str, list[Diagnostic]] = defaultdict(list)
    ungrouped: list[Diagnostic] = []
    for d in diagnostics:
        if d.category == ErrorCategory.IMPORT and "No module named" in d.message:
            # extract module name
            import re
            m = re.search(r"No module named ['\"](.+?)['\"]", d.message)
            if m:
                cascade_key[f"missing_module:{m.group(1)}"].append(d)
                continue
        ungrouped.append(d)

    # Build groups -----------------------------------------------------------
    raw_groups: list[tuple[str, list[Diagnostic]]] = []

    # Cascade groups first (highest priority)
    for key, diags in cascade_key.items():
        if diags:
            raw_groups.append((key, diags))

    # Per-file groups
    for fpath, diags in by_file.items():
        # Remove diags already in cascade groups
        cascade_ids = {d.error_id for g in cascade_key.values() for d in g}
        diags = [d for d in diags if d.error_id not in cascade_ids]
        if diags:
            raw_groups.append((f"file:{fpath}", diags))

    # Category groups for no-file diags
    for cat, diags in by_category.items():
        cascade_ids = {d.error_id for g in cascade_key.values() for d in g}
        diags = [d for d in diags if d.error_id not in cascade_ids]
        if diags:
            raw_groups.append((f"category:{cat}", diags))

    # ── Phase 4: rank groups ──────────────────────────────────────────────
    def group_priority(item: tuple) -> tuple:
        _, diags = item
        max_sev  = max((_severity_rank(d.severity) for d in diags), default=0)
        return (-max_sev, -len(diags))  # highest severity first, then by size

    raw_groups.sort(key=group_priority)

    if len(raw_groups) > max_groups:
        dropped = len(raw_groups) - max_groups
        dropped_diag_count = sum(len(diags) for _, diags in raw_groups[max_groups:])
        print(f"  [fix] {dropped} lower-priority group(s) ({dropped_diag_count} diagnostic(s)) "
              f"dropped this round due to max_groups={max_groups} cap")

    # ── Phase 5: build DiagnosticGroup objects ─────────────────────────────
    groups: list[DiagnosticGroup] = []
    seen_ids: set[str] = set()

    for priority, (key, diags) in enumerate(raw_groups[:max_groups], start=1):
        # Deduplicate across groups
        diags = [d for d in diags if d.error_id not in seen_ids]
        if not diags:
            continue
        seen_ids.update(d.error_id for d in diags)

        # Some diagnostics (e.g. "Duplicate class definition ... in multiple
        # files (a.py, b.py)") name a SECOND file the fix must also see —
        # stashed in metadata since Diagnostic.file_path only holds one path.
        # Without folding these in, the LLM fix prompt only ever includes
        # the first-named file, patches it every round, and the diagnostic
        # recurs unchanged since the actual duplicate/other half of the
        # problem in the second file is never in its context.
        affected_set = {d.file_path for d in diags if d.file_path}
        for d in diags:
            affected_set.update(d.metadata.get("extra_file_paths", []))
        affected = list(affected_set)
        strategy = _strategy_for_group(diags)
        root     = _root_cause_summary(diags)

        groups.append(DiagnosticGroup(
            group_id=uuid.uuid4().hex[:8],
            root_cause=root,
            diagnostics=diags,
            affected_files=affected,
            suggested_strategy=strategy,
            priority=priority,
        ))

    return groups
