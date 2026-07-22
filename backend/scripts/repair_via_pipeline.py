"""
Repair existing, already-generated project directories using ForgeAI's REAL
production repair loop -- the exact RetryManager escalation ladder +
FixOrchestrator + best-state snapshot/revert that V15Pipeline.run() uses for
a freshly-generated app -- just entered at Stage 2 (deterministic patch +
verify + fix loop) instead of Stage 1, since the project already exists on
disk and needs no (re)generation.

This replaces the earlier ad-hoc `agy`-CLI blind-repair approach, which had
no regression protection and made every project it touched catastrophically
worse (e.g. 86.93 -> 18.62). This script reuses ForgeAI's own
already-safe, already-regression-protected mechanism (best-state snapshot
via `_ProjectSnapshot`, revert-if-worse, early-stop on stalled attempts)
instead of reinventing one -- zero changes to any existing ForgeAI module.

Usage:
    python scripts/repair_via_pipeline.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_ROOT.parent
sys.path.insert(0, str(_BACKEND_ROOT))
sys.path.insert(0, str(_BACKEND_ROOT / "scripts"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.context import GenerationContext
from app.core.pipeline import V15Pipeline, _frontend_build_ok
from app.repair.orchestrator import _ProjectSnapshot
from app.retry.manager import RetryManager
from _project_rescorer import _load_architecture

RESULTS_PATH = _BACKEND_ROOT / "benchmark_results" / "repair_via_pipeline_results.json"
FORGEBENCH_RESULTS = _BACKEND_ROOT / "benchmark_results" / "forgebench_v1_results.json"
FORGEBENCH_HARD_RESULTS = _BACKEND_ROOT / "benchmark_results" / "forgebench_hard_results.json"
CORRECTED_SCORES = _BACKEND_ROOT / "benchmark_results" / "corrected_scores.json"
GENERATED_PROJECTS = _REPO_ROOT / "generated_projects"

TARGET_SCORE = 90.0


def repair_project(project_path: Path, idea: str, project_name: str, max_attempts: int = 5) -> dict:
    t0 = time.time()
    pipeline = V15Pipeline(deploy=False)
    pipeline.retry_manager = RetryManager(max_attempts=max_attempts)

    ctx = GenerationContext(
        job_id=uuid.uuid4().hex, idea=idea,
        project_path=project_path, project_name=project_name,
    )
    # Without this, API Functionality (weight 0.15) is a structural 0 for
    # every project regardless of code quality -- see _load_architecture's
    # docstring. Found live by repair-group-c before any fix attempts were
    # wasted chasing an artificial ceiling.
    ctx.architecture = _load_architecture(project_path)

    pipeline._deterministic_patch(ctx)
    pipeline._verify_and_score(ctx, 0)
    baseline_score = ctx.latest_score

    rm = pipeline.retry_manager
    stalled = 0
    max_stalled = int(os.environ.get("FORGE_MAX_STALLED_FIX_ATTEMPTS", "3"))
    best_score = ctx.latest_score
    best_snapshot = None
    attempts_log: list[dict] = []

    while (not ctx.is_deployment_ready or not _frontend_build_ok(ctx)) and not rm.exhausted:
        if stalled >= max_stalled:
            print(f"  [{project_name}] {stalled} stalled attempts -- stopping fix loop")
            break
        cfg = rm.next_strategy(ctx)
        if cfg is None:
            break

        fix_attempt = pipeline.fix_orchestrator.run_attempt(ctx, cfg)
        rm.record_result(cfg, fix_attempt.score_before, fix_attempt.score_after or 0)

        if (fix_attempt.score_after or 0) > fix_attempt.score_before:
            stalled = 0
        else:
            stalled += 1
        if (fix_attempt.score_after or 0) > best_score:
            best_score = fix_attempt.score_after or 0
            best_snapshot = _ProjectSnapshot(ctx.project_path)

        print(f"  [{project_name}] attempt {cfg.attempt} ({cfg.strategy.value}): "
              f"{fix_attempt.score_before:.1f} -> {fix_attempt.score_after or 0:.1f} "
              f"{'REGRESSION' if fix_attempt.regression_detected else ''}")

        attempts_log.append({
            "attempt": cfg.attempt, "strategy": cfg.strategy.value,
            "score_before": fix_attempt.score_before, "score_after": fix_attempt.score_after,
            "regression_detected": fix_attempt.regression_detected,
            "files_modified": len(fix_attempt.files_modified),
        })

    if best_snapshot is not None and ctx.latest_score < best_score:
        print(f"  [{project_name}] final ({ctx.latest_score:.1f}) worse than best "
              f"({best_score:.1f}) -- restoring best state")
        best_snapshot.revert()
        pipeline._verify_and_score(ctx, attempt=len(ctx.fix_attempts))

    return {
        "project_name": project_name,
        "baseline_score": baseline_score,
        "final_score": ctx.latest_score,
        "grade": ctx.current_score.grade if ctx.current_score else "F",
        "attempts": attempts_log,
        "elapsed_s": round(time.time() - t0, 1),
    }


def _load_candidates() -> list[dict]:
    """Every project below TARGET_SCORE, using corrected_scores.json (the
    fixed-rescorer, correct-Python pass) as the authoritative current score
    where available -- the raw forgebench_v1/forgebench_hard result files
    predate the rescorer/interpreter fixes and are known unreliable."""
    corrected: dict[str, dict] = {}
    if CORRECTED_SCORES.exists():
        data = json.loads(CORRECTED_SCORES.read_text(encoding="utf-8"))
        for r in data["results"]:
            corrected[r["project_name"]] = r

    out = []
    seen_names: set[str] = set()
    for results_path, ideas_source in (
        (FORGEBENCH_RESULTS, "forgebench_v1"),
        (FORGEBENCH_HARD_RESULTS, "forgebench_hard"),
    ):
        if not results_path.exists():
            continue
        data = json.loads(results_path.read_text(encoding="utf-8"))
        for r in data["results"]:
            name = r.get("project_name")
            if not name or name in seen_names:
                continue
            score = r.get("forge_score")
            if name in corrected and corrected[name].get("new_score") is not None:
                score = corrected[name]["new_score"]
            if score is None or score >= TARGET_SCORE:
                continue
            project_dir = GENERATED_PROJECTS / name
            if not project_dir.is_dir():
                continue
            seen_names.add(name)
            if ideas_source == "forgebench_v1":
                from forgebench_v1 import APPS
                idea = dict(APPS)[r["app"]]
            else:
                from forgebench_hard import APPS as HARD_APPS
                idea = dict(HARD_APPS)[r["app"]]
            out.append({
                "app_key": r["app"], "project_name": name,
                "project_dir": str(project_dir), "idea": idea,
                "baseline_score": score, "source": ideas_source,
            })
    return out


def _load_results() -> list[dict]:
    if RESULTS_PATH.exists():
        try:
            return json.loads(RESULTS_PATH.read_text(encoding="utf-8")).get("results", [])
        except Exception:
            return []
    return []


def _save_results(results: list[dict]):
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps({
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "target_score": TARGET_SCORE,
        "results": results,
    }, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def main():
    candidates = _load_candidates()
    results = _load_results()
    done_names = {r["project_name"] for r in results}

    print(f"\n{'='*70}\n  REPAIR-VIA-PIPELINE -- {len(candidates)} candidates below {TARGET_SCORE}\n{'='*70}")
    for c in candidates:
        if c["project_name"] in done_names:
            print(f"  skipping {c['project_name']} (already processed)")
            continue
        print(f"\n--- {c['project_name']} (baseline {c['baseline_score']}) ---")
        try:
            outcome = repair_project(Path(c["project_dir"]), c["idea"], c["project_name"])
        except Exception as e:
            import traceback
            outcome = {"project_name": c["project_name"], "baseline_score": c["baseline_score"],
                       "final_score": c["baseline_score"], "error": f"{type(e).__name__}: {e}",
                       "traceback": traceback.format_exc()[-2000:]}
        results.append(outcome)
        _save_results(results)
        print(f"  [{c['project_name']}] DONE: {outcome.get('baseline_score')} -> {outcome.get('final_score')}")

    print(f"\n{'='*70}\n  REPAIR-VIA-PIPELINE COMPLETE\n{'='*70}")
    for r in results:
        print(f"  {r['project_name']}: {r['baseline_score']} -> {r.get('final_score')}")


if __name__ == "__main__":
    main()
