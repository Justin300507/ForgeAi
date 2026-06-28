"""
Long-Term Project History

Stores the complete outcome of every generation run:
  - architecture, components, fixes, runtime issues, deployment issues,
    browser failures, final score, generation time

On new generation requests, retrieves similar past projects so the
planner and architect can learn from both successes and failures.

Why this beats arch_db alone:
  - arch_db stores structure only (entities, endpoints, files)
  - project_history stores the engineering narrative: what broke, how it
    was fixed, what the final product looked like, and how much it cost

Storage: backend/failure_memory/project_history.jsonl  (append-only)
Index:   backend/failure_memory/project_index.json     (category → best runs)
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


_MEM_DIR      = Path(__file__).parent.parent.parent / "failure_memory"
_HISTORY_PATH = _MEM_DIR / "project_history.jsonl"
_INDEX_PATH   = _MEM_DIR / "project_index.json"

# Keep the JSONL bounded — only the last N entries matter for learning
_MAX_HISTORY_ENTRIES = 500


@dataclass
class ProjectRun:
    # Identity
    run_id:        str
    idea:          str
    category:      str
    timestamp:     str

    # Architecture
    entities:      list[str] = field(default_factory=list)   # table/entity names
    endpoints:     list[str] = field(default_factory=list)   # "METHOD /path"
    components:    list[str] = field(default_factory=list)   # detected components

    # Repair history
    fix_count:     int   = 0
    fix_files:     list[str] = field(default_factory=list)   # files that needed fixes
    runtime_fixes: int   = 0

    # Issues (what broke)
    static_errors:     list[str] = field(default_factory=list)
    runtime_issues:    list[str] = field(default_factory=list)  # endpoint timeout paths
    deployment_issues: list[str] = field(default_factory=list)
    browser_issues:    list[str] = field(default_factory=list)

    # Outcomes
    validation_passed:  bool  = False
    runtime_passed:     bool  = False
    crud_passed:        bool  = False
    frontend_built:     bool  = False
    deployment_success: bool  = False
    forge_score:        float = 0.0
    endpoint_pass_rate: float = 0.0

    # Cost & timing
    generation_time_s: float = 0.0
    estimated_cost_usd: float = 0.0
    provider:          str   = "auto"

    def summary(self) -> str:
        return (
            f"[{self.category}] score={self.forge_score:.0f} "
            f"runtime={self.runtime_passed} crud={self.crud_passed} "
            f"fixes={self.fix_count} time={self.generation_time_s:.0f}s"
        )

    def to_dict(self) -> dict:
        return asdict(self)


class ProjectHistoryDB:
    """
    Append-only log of all project runs with fast category-based index.
    """

    def __init__(self):
        self._index: dict[str, list[dict]] = {}  # category → [run dicts, best first]
        self._load_index()

    def _load_index(self):
        if _INDEX_PATH.exists():
            try:
                self._index = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
            except Exception:
                self._index = {}

    def _save_index(self):
        _MEM_DIR.mkdir(parents=True, exist_ok=True)
        _INDEX_PATH.write_text(json.dumps(self._index, indent=2), encoding="utf-8")

    def record(self, run: ProjectRun) -> None:
        """
        Append this run to the JSONL log and update the index.
        Only indexes runs with forge_score >= 70 (failures still logged, not indexed).
        """
        _MEM_DIR.mkdir(parents=True, exist_ok=True)

        # Append to JSONL
        with _HISTORY_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(run.to_dict()) + "\n")

        # Update index with good runs only
        if run.forge_score >= 70:
            bucket = self._index.setdefault(run.category, [])
            bucket.append(run.to_dict())
            # Keep top-10 per category, sorted by composite score
            bucket.sort(key=_composite_score, reverse=True)
            self._index[run.category] = bucket[:10]
            self._save_index()

    def retrieve_similar(self, idea: str, k: int = 5) -> list[ProjectRun]:
        """
        Retrieve the top-K most relevant past project runs for a new idea.

        Uses category match + keyword overlap. Returns both successes and
        instructive failures (low score = negative example).
        """
        from app.knowledge.arch_db import classify_idea, APP_CATEGORIES
        category = classify_idea(idea)
        idea_lower = idea.lower()

        candidates: list[tuple[float, dict]] = []

        # Primary: same category
        for rec in self._index.get(category, []):
            relevance = 20.0 + _composite_score(rec)
            candidates.append((relevance, rec))

        # Secondary: adjacent categories (keyword overlap)
        for cat, records in self._index.items():
            if cat == category:
                continue
            cat_kws = APP_CATEGORIES.get(cat, [])
            overlap = sum(1 for kw in cat_kws if kw in idea_lower)
            if overlap >= 2:
                for rec in records[:3]:
                    relevance = overlap * 5.0 + _composite_score(rec)
                    candidates.append((relevance, rec))

        candidates.sort(key=lambda x: x[0], reverse=True)
        return [ProjectRun(**d) for _, d in candidates[:k]]

    def build_history_context(self, idea: str, k: int = 5) -> str:
        """
        Build a rich context block from similar past runs.

        Shows what broke in failed apps, what worked in successful ones.
        Used by the planner + architect to avoid repeated mistakes.
        """
        runs = self.retrieve_similar(idea, k=k)
        if not runs:
            return ""

        successes = [r for r in runs if r.forge_score >= 85]
        failures  = [r for r in runs if r.forge_score < 70]

        lines = [
            "PROJECT HISTORY (similar past apps — learn from these):",
            "",
        ]

        if successes:
            lines.append("  WHAT WORKED (high-scoring similar apps):")
            for r in successes[:3]:
                lines.append(f"    [{r.category}] score={r.forge_score:.0f}")
                lines.append(f"      Entities: {', '.join(r.entities[:5])}")
                lines.append(f"      Components: {', '.join(r.components[:5])}")
                lines.append(f"      Fixes needed: {r.fix_count}")
                lines.append("")

        if failures:
            lines.append("  WHAT FAILED (avoid these patterns):")
            for r in failures[:2]:
                lines.append(f"    [{r.category}] score={r.forge_score:.0f}")
                if r.static_errors:
                    lines.append(f"      Static errors: {'; '.join(r.static_errors[:2])}")
                if r.runtime_issues:
                    lines.append(f"      Runtime issues: {'; '.join(r.runtime_issues[:2])}")
                if r.deployment_issues:
                    lines.append(f"      Deploy issues: {'; '.join(r.deployment_issues[:2])}")
                lines.append("")

        lines += [
            "INSTRUCTION: Design to replicate the patterns in successful apps above.",
            "Explicitly avoid the error patterns seen in failed apps.",
        ]
        print(f"  [project_history] Context: {len(successes)} successes + {len(failures)} failures")
        return "\n".join(lines)

    def stats(self) -> dict:
        total_indexed = sum(len(v) for v in self._index.values())
        history_lines = 0
        if _HISTORY_PATH.exists():
            history_lines = sum(1 for _ in _HISTORY_PATH.open("r", encoding="utf-8"))
        return {
            "total_runs_logged": history_lines,
            "total_indexed": total_indexed,
            "categories": list(self._index.keys()),
            "best_per_category": {
                cat: {
                    "score": recs[0]["forge_score"] if recs else 0,
                    "idea": recs[0]["idea"][:50] if recs else "",
                }
                for cat, recs in self._index.items()
            },
        }


def _composite_score(rec: dict) -> float:
    """
    Multi-dimensional score used to rank architectures in the evolution context.

    Factors:
      40%  forge_score        — overall static + runtime quality
      25%  deployment_success — did it actually deploy?
      15%  crud_passed        — did the full user journey work?
      10%  fix_penalty        — fewer fixes = better design
       5%  cost_efficiency    — lower cost = simpler = more reliable
       5%  endpoint_pass_rate — did endpoints respond?
    """
    fs   = rec.get("forge_score", 0.0) * 0.40
    dep  = (100.0 if rec.get("deployment_success") else 0.0) * 0.25
    crud = (100.0 if rec.get("crud_passed") else 0.0) * 0.15
    fix_count = rec.get("fix_count", 0)
    fix_pen = max(0, 100.0 - fix_count * 10) * 0.10
    ep = rec.get("endpoint_pass_rate", 1.0) * 100.0 * 0.05
    cost = rec.get("estimated_cost_usd", 0)
    cost_eff = max(0, 100 - cost * 1000) * 0.05   # cheaper = higher score
    return round(fs + dep + crud + fix_pen + ep + cost_eff, 2)


# ── Singleton ──────────────────────────────────────────────────────────────────
project_history = ProjectHistoryDB()


def build_run_from_result(idea: str, result: dict, provider: str = "auto") -> ProjectRun:
    """
    Convenience factory: build a ProjectRun from the dict returned by generate_project().
    """
    from app.knowledge.arch_db import classify_idea

    run_id    = hashlib.sha256(f"{idea}{time.time()}".encode()).hexdigest()[:12]
    category  = classify_idea(idea)
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    arch = result.get("architecture") or {}
    endpoints = [
        f"{ep.get('method','?')} {ep.get('path','/')}"
        for ep in arch.get("api_endpoints", [])[:20]
    ]
    schema = arch.get("database_schema") or {}
    if isinstance(schema, dict):
        entities = [e.get("name") or e.get("table_name", "?") for e in
                    (schema.get("entities") or schema.get("tables") or [])[:10]]
    else:
        entities = []

    rt = result.get("runtime") or {}
    journey = rt.get("journey") or {}
    behavioral = rt.get("behavioral_issues") or []
    runtime_issues = [f"{i.get('method','')} {i.get('path','')}".strip() for i in behavioral]

    validation = result.get("validation") or {}
    static_errors = (validation.get("errors") or [])[:10]

    pm = result.get("pipeline_metrics") or {}
    components = []
    try:
        from app.knowledge.component_db import component_db
        components = component_db.detect_components(result.get("project_path", ""))
    except Exception:
        pass

    forge = result.get("forge_score") or {}
    score = float(forge.get("score", 0) if isinstance(forge, dict) else forge)

    return ProjectRun(
        run_id=run_id,
        idea=idea[:300],
        category=category,
        timestamp=timestamp,
        entities=entities,
        endpoints=endpoints,
        components=components,
        fix_count=result.get("stats", {}).get("fix_attempts", 0),
        runtime_fixes=0,
        static_errors=static_errors,
        runtime_issues=runtime_issues,
        validation_passed=validation.get("passed", False),
        runtime_passed=rt.get("success", False),
        crud_passed=bool(journey.get("success") and not journey.get("skipped")),
        frontend_built=bool((result.get("frontend_build") or {}).get("success")),
        forge_score=score,
        endpoint_pass_rate=float(rt.get("endpoint_pass_rate", 0)),
        generation_time_s=float(result.get("generation_time_seconds", 0)),
        provider=provider,
    )
