"""
Standalone re-scoring for an EXISTING generated project directory -- no
generation, just VerificationEngine + ScoringEngine run against whatever
code is currently on disk, using the exact same engine/weights the live
V15 pipeline scores with (see app/core/pipeline.py's own
`self.verification_engine.run(ctx)` -> `self.scoring_engine.score(ctx, ...)`
sequence).

Built for scripts/agy_repair_loop.py to check whether a round of fixes
actually moved the ForgeScore, and to hand back concrete failing
diagnostics for the next fix prompt.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.context import GenerationContext
from app.verification.engine import VerificationEngine
from app.scoring.engine import ScoringEngine

_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def _load_architecture(project_path: Path):
    """Without this, ctx.architecture stays None and the runtime endpoint
    smoke-test stage has nothing to test against -- API Functionality
    (weight 0.15) scores a structural 0 for every project regardless of
    actual code quality, capping the reachable score around ~77 (found live
    by repair-group-c mid-run: university_course_management rescored 70.15
    vs its real pipeline-computed baseline of 76.86 -- two different
    scoring contexts, not a real regression). metadata.json's own
    "architecture" key is exactly the dict the real pipeline sets
    ctx.architecture to during generation (confirmed: api_endpoints/
    database_schema/folder_structure/frontend_structure), so re-loading it
    here restores parity with how the project was originally scored."""
    meta_path = project_path / "metadata.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return meta.get("architecture")


def rescore_project(project_path: Path, idea: str, project_name: str) -> dict:
    ctx = GenerationContext(
        job_id=uuid.uuid4().hex,
        idea=idea,
        project_path=project_path,
        project_name=project_name,
    )
    ctx.architecture = _load_architecture(project_path)
    VerificationEngine().run(ctx)
    score = ScoringEngine().score(ctx, attempt_number=0)

    diagnostics = sorted(
        ctx.all_diagnostics(),
        key=lambda d: _SEVERITY_ORDER.get(getattr(d.severity, "name", str(d.severity)), 4),
    )

    return {
        "forge_score": score.overall,
        "grade": score.grade,
        "deployment_ready": score.deployment_ready,
        "dimensions": [
            {"name": d.name, "score": d.score, "passed": d.passed, "details": d.details}
            for d in score.dimensions
        ],
        "top_issues": [
            f"[{getattr(d.severity, 'name', d.severity)}] ({d.source}) {d.message}"
            for d in diagnostics[:25]
        ],
    }


if __name__ == "__main__":
    import argparse
    import json

    p = argparse.ArgumentParser(description="Re-score an existing generated project directory")
    p.add_argument("project_path")
    p.add_argument("idea")
    p.add_argument("project_name")
    args = p.parse_args()

    result = rescore_project(Path(args.project_path), args.idea, args.project_name)
    print(json.dumps(result, indent=2, default=str))
