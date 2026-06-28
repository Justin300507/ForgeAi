"""
FailureGraph — causal dependency analysis for diagnostics.

Core insight: most errors in a generated project are DOWNSTREAM symptoms
of 1-2 root causes. If the auth module fails to import, every route that
uses it also fails — 15 "errors" that have exactly one fix.

This graph finds the true roots. The FixOrchestrator only needs to fix roots;
downstream effects resolve automatically when roots are fixed.

Algorithm:
  1. Parse all diagnostics for causal patterns (import chain, startup cascade)
  2. Build a directed graph: root → [children]
  3. Topological traversal: surface only root nodes for fixing
  4. Mark the rest as "suppressed" (symptom, not cause)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from app.core.context import Diagnostic, ErrorCategory, ErrorSeverity


# ── Causal rules (pattern → match function) ───────────────────────────────────

def _extract_missing_module(msg: str) -> Optional[str]:
    m = re.search(r"No module named ['\"]([^'\"]+)['\"]", msg)
    return m.group(1) if m else None


def _extract_import_target(msg: str) -> Optional[str]:
    m = re.search(r"cannot import name .+? from ['\"]([^'\"]+)['\"]", msg)
    if m:
        return m.group(1)
    m = re.search(r"ImportError.*?['\"]([^'\"]+)['\"]", msg)
    return m.group(1) if m else None


# ── Graph data structures ─────────────────────────────────────────────────────

@dataclass
class GraphNode:
    diagnostic:   Diagnostic
    children:     list["GraphNode"] = field(default_factory=list)
    parents:      list["GraphNode"] = field(default_factory=list)
    is_root:      bool = True          # True until a parent edge is added
    depth:        int  = 0            # 0 = root, 1 = first downstream, etc.
    cascade_type: str  = ""           # what caused this to become a child


@dataclass
class FailureGraph:
    nodes:           dict[str, GraphNode]   # error_id → node
    roots:           list[GraphNode]         # nodes with no parents
    suppressed:      list[GraphNode]         # downstream symptoms
    suppressed_count: int
    cascade_chains:  list[list[str]]         # root_id → path to leaf_ids

    def root_diagnostics(self) -> list[Diagnostic]:
        """Only the diagnostics that need to actually be fixed."""
        return [n.diagnostic for n in self.roots]

    def suppressed_diagnostics(self) -> list[Diagnostic]:
        return [n.diagnostic for n in self.suppressed]

    def explain(self) -> str:
        lines = [f"FailureGraph: {len(self.roots)} root(s), "
                 f"{self.suppressed_count} suppressed downstream effect(s)"]
        for root in self.roots:
            lines.append(f"  ROOT [{root.diagnostic.severity.value.upper()}] "
                         f"{root.diagnostic.message[:80]}")
            for child in root.children:
                lines.append(f"    └─ DOWNSTREAM: {child.diagnostic.message[:70]}")
                for grandchild in child.children:
                    lines.append(f"       └─ DOWNSTREAM: {grandchild.diagnostic.message[:60]}")
        return "\n".join(lines)


# ── Graph builder ──────────────────────────────────────────────────────────────

def build_failure_graph(diagnostics: list[Diagnostic]) -> FailureGraph:
    """
    Build a causal failure graph from a flat list of diagnostics.

    Causal rules applied (in priority order):
      R1. ModuleNotFoundError(X) → causes any error mentioning X
      R2. Runtime CRITICAL failure → causes all Browser diagnostics
      R3. Runtime CRITICAL failure → causes all API diagnostics
      R4. Auth module error → causes all auth-dependent endpoint errors
      R5. Database/session error → causes all DB-related errors
      R6. Frontend build failure → causes all blank-page / console-error browser diags
    """
    if not diagnostics:
        return FailureGraph(nodes={}, roots=[], suppressed=[], suppressed_count=0, cascade_chains=[])

    # Create nodes
    nodes: dict[str, GraphNode] = {d.error_id: GraphNode(diagnostic=d) for d in diagnostics}

    def add_edge(parent_id: str, child_id: str, cascade_type: str):
        if parent_id == child_id:
            return
        p = nodes.get(parent_id)
        c = nodes.get(child_id)
        if p and c and c not in p.children:
            p.children.append(c)
            c.parents.append(p)
            c.is_root = False
            c.cascade_type = cascade_type

    # ── R1: missing module cascades ───────────────────────────────────────────
    module_error_map: dict[str, str] = {}  # module_name → error_id
    for d in diagnostics:
        mod = _extract_missing_module(d.message)
        if mod:
            module_error_map[mod] = d.error_id

    for d in diagnostics:
        # Any error that mentions a known missing module but is NOT the ModuleNotFoundError itself
        for mod_name, root_id in module_error_map.items():
            if d.error_id != root_id and mod_name in d.message:
                add_edge(root_id, d.error_id, f"missing_module:{mod_name}")
            # Also: ImportError for a file that imports the missing module
            target = _extract_import_target(d.message)
            if target and mod_name.startswith(target):
                add_edge(root_id, d.error_id, f"import_cascade:{mod_name}")

    # ── R2 & R3: runtime CRITICAL → all browser + API downstream ─────────────
    runtime_critical_ids = [
        d.error_id for d in diagnostics
        if d.source == "runtime" and d.severity in (ErrorSeverity.CRITICAL, ErrorSeverity.HIGH)
    ]
    for rc_id in runtime_critical_ids:
        for d in diagnostics:
            if d.source == "browser":
                add_edge(rc_id, d.error_id, "runtime_startup_cascade")
            if d.source == "api" and d.error_id != rc_id:
                add_edge(rc_id, d.error_id, "runtime_startup_cascade")

    # ── R4: auth module errors → auth-endpoint downstream ────────────────────
    auth_root_ids = [
        d.error_id for d in diagnostics
        if d.file_path and "auth" in d.file_path.lower()
        and d.severity in (ErrorSeverity.CRITICAL, ErrorSeverity.HIGH)
    ]
    for auth_id in auth_root_ids:
        for d in diagnostics:
            if d.error_id == auth_id:
                continue
            if (d.category == ErrorCategory.API and
                    any(kw in d.message.lower() for kw in ("401", "403", "login", "register", "auth"))):
                add_edge(auth_id, d.error_id, "auth_cascade")
            if d.source == "browser" and any(kw in d.message.lower() for kw in ("401", "login", "register")):
                add_edge(auth_id, d.error_id, "auth_cascade")

    # ── R5: database / session errors → related DB errors downstream ──────────
    db_root_ids = [
        d.error_id for d in diagnostics
        if d.category in (ErrorCategory.CONTRACT,) and
        any(kw in d.message.lower() for kw in ("database", "session", "sqlalchemy", "get_db"))
        and d.severity in (ErrorSeverity.CRITICAL, ErrorSeverity.HIGH)
    ]
    for db_id in db_root_ids:
        for d in diagnostics:
            if d.error_id == db_id:
                continue
            if any(kw in d.message.lower() for kw in ("db", "database", "session", "commit", "query")):
                add_edge(db_id, d.error_id, "db_cascade")

    # ── R6: static CRITICAL → suppress browser results ────────────────────────
    static_critical_ids = [
        d.error_id for d in diagnostics
        if d.source == "static" and d.severity == ErrorSeverity.CRITICAL
    ]
    for sc_id in static_critical_ids:
        for d in diagnostics:
            if d.source in ("browser", "api") and d.error_id not in static_critical_ids:
                add_edge(sc_id, d.error_id, "static_critical_cascade")

    # ── Build final lists ─────────────────────────────────────────────────────
    roots     = [n for n in nodes.values() if n.is_root]
    suppressed = [n for n in nodes.values() if not n.is_root]

    # Set depths with BFS
    from collections import deque
    queue = deque(roots)
    visited: set[str] = set()
    while queue:
        node = queue.popleft()
        if node.diagnostic.error_id in visited:
            continue
        visited.add(node.diagnostic.error_id)
        for child in node.children:
            child.depth = node.depth + 1
            queue.append(child)

    # Build cascade chains (root → leaf paths, for reporting)
    chains: list[list[str]] = []
    for root in roots:
        if root.children:
            chain = [root.diagnostic.error_id]
            node = root
            while node.children:
                node = node.children[0]
                chain.append(node.diagnostic.error_id)
            chains.append(chain)

    return FailureGraph(
        nodes=nodes,
        roots=roots,
        suppressed=suppressed,
        suppressed_count=len(suppressed),
        cascade_chains=chains,
    )
