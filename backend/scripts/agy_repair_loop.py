"""
Autonomous repair loop: for every already-generated ForgeBench v1.0 project
still scoring below TARGET_SCORE (per benchmark_results/forgebench_v1_results.json),
invoke the `agy` (Google Antigravity) CLI to make targeted code fixes
directly in that project's own directory, then re-score with the SAME
VerificationEngine/ScoringEngine the live pipeline uses (via
scripts/_project_rescorer.py) to check whether the fix actually worked.
Repeats up to MAX_ATTEMPTS per project or until forge_score >= TARGET_SCORE.

Runs unattended for hours: `--dangerously-skip-permissions` on every agy
call (no human is present to approve edits), scoped per-call to a single
generated_projects/<name> directory via `--add-dir` so the blast radius is
always one disposable generated app -- never ForgeAI's own source tree.

Usage:
    python scripts/agy_repair_loop.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_ROOT.parent
sys.path.insert(0, str(_BACKEND_ROOT))
sys.path.insert(0, str(_BACKEND_ROOT / "scripts"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from _project_rescorer import rescore_project

RESULTS_PATH = _BACKEND_ROOT / "benchmark_results" / "agy_repair_results.json"
FORGEBENCH_RESULTS = _BACKEND_ROOT / "benchmark_results" / "forgebench_v1_results.json"
GENERATED_PROJECTS = _REPO_ROOT / "generated_projects"

TARGET_SCORE = 90.0
MAX_ATTEMPTS = 4
AGY_SUBPROCESS_TIMEOUT_S = 1800  # hard kill if agy itself hangs
AGY_PRINT_TIMEOUT = "25m0s"      # agy's own internal wait budget per call


def _load_candidates() -> list[dict]:
    data = json.loads(FORGEBENCH_RESULTS.read_text(encoding="utf-8"))
    out = []
    for r in data["results"]:
        name = r.get("project_name")
        score = r.get("forge_score")
        if not name or score is None or score >= TARGET_SCORE:
            continue
        project_dir = GENERATED_PROJECTS / name
        if not project_dir.is_dir():
            continue
        from forgebench_v1 import APPS
        idea = dict(APPS)[r["app"]]
        out.append({
            "app_key": r["app"],
            "project_name": name,
            "project_dir": str(project_dir),
            "idea": idea,
            "baseline_score": score,
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


def _run_agy_fix(project_dir: str, idea: str, issues: list[str], attempt: int) -> dict:
    issues_text = "\n".join(f"- {i}" for i in issues[:20]) or "(no specific diagnostics captured -- inspect the app yourself)"
    prompt = (
        f"You are debugging an auto-generated full-stack app (FastAPI backend + "
        f"React frontend) at this exact directory: {project_dir}\n\n"
        f"Original spec it was generated from:\n{idea}\n\n"
        f"It currently fails automated verification (attempt {attempt} of {MAX_ATTEMPTS}). "
        f"Top diagnostics from the last verification pass:\n{issues_text}\n\n"
        "Fix the underlying bugs directly in the existing backend/ and frontend/ code "
        "so that: (1) the FastAPI backend starts cleanly under uvicorn, (2) the React "
        "frontend builds and loads without console errors, (3) a full CRUD user "
        "journey works end-to-end (register, login, create/edit/delete the core "
        "entities), and (4) the diagnostics listed above are resolved. Make targeted, "
        "minimal fixes -- do not rewrite the app from scratch or change its feature "
        "scope. Do not touch anything outside this directory."
    )
    # `--new-project` (not `--add-dir`) is what makes agy treat `cwd` as
    # the project root -- confirmed via smoke test: --add-dir alone left
    # it operating out of its own default scratch project, seeing none of
    # the target directory's files. Each attempt is a fresh --print call
    # (no conversation continuity needed), so a new project per attempt
    # is harmless.
    cmd = [
        "agy", "-p", prompt,
        "--new-project",
        "--dangerously-skip-permissions",
        "--print-timeout", AGY_PRINT_TIMEOUT,
    ]
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=project_dir, capture_output=True, text=True,
            timeout=AGY_SUBPROCESS_TIMEOUT_S, encoding="utf-8", errors="replace",
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-3000:],
            "stderr_tail": proc.stderr[-2000:],
            "elapsed_s": round(time.time() - t0, 1),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"agy subprocess timed out after {AGY_SUBPROCESS_TIMEOUT_S}s",
                "elapsed_s": round(time.time() - t0, 1)}
    except FileNotFoundError:
        return {"ok": False, "error": "agy CLI not found on PATH", "elapsed_s": 0.0}


def _process_one(c: dict) -> dict:
    print(f"\n{'='*70}\n  {c['app_key']} ({c['project_name']}) -- baseline {c['baseline_score']}\n{'='*70}")
    attempts_log: list[dict] = []
    current_score = c["baseline_score"]
    final_rescore: dict | None = None
    attempt = 0

    while True:
        try:
            rescore = rescore_project(Path(c["project_dir"]), c["idea"], c["project_name"])
        except Exception as e:
            rescore = {"forge_score": current_score, "top_issues": [f"(rescore failed: {e})"], "dimensions": None}
        current_score = rescore.get("forge_score", current_score)
        final_rescore = rescore
        print(f"  [{c['project_name']}] score={current_score:.1f} (attempt {attempt})")

        if current_score >= TARGET_SCORE or attempt >= MAX_ATTEMPTS:
            break

        attempt += 1
        print(f"  [{c['project_name']}] invoking agy (attempt {attempt}/{MAX_ATTEMPTS})...")
        agy_result = _run_agy_fix(c["project_dir"], c["idea"], rescore.get("top_issues", []), attempt)
        print(f"  [{c['project_name']}] agy ok={agy_result.get('ok')} elapsed={agy_result.get('elapsed_s')}s")
        attempts_log.append({"attempt": attempt, "agy": agy_result, "score_before_this_attempt": current_score})

    status = "reached_target" if current_score >= TARGET_SCORE else "exhausted"
    return {
        "app_key": c["app_key"],
        "project_name": c["project_name"],
        "baseline_score": c["baseline_score"],
        "final_score": current_score,
        "final_dimensions": final_rescore.get("dimensions") if final_rescore else None,
        "attempts": attempts_log,
        "final_status": status,
    }


def main():
    candidates = _load_candidates()
    results = _load_results()
    done_names = {r["project_name"] for r in results if r.get("final_status") in ("reached_target", "exhausted")}

    print(f"\n{'='*70}\n  AGY REPAIR LOOP -- {len(candidates)} projects below {TARGET_SCORE}\n{'='*70}")
    for c in candidates:
        if c["project_name"] in done_names:
            print(f"  skipping {c['project_name']} (already processed)")
            continue
        outcome = _process_one(c)
        results = [r for r in results if r["project_name"] != outcome["project_name"]]
        results.append(outcome)
        _save_results(results)

    print(f"\n{'='*70}\n  AGY REPAIR LOOP COMPLETE\n{'='*70}")
    for r in results:
        print(f"  {r['project_name']}: {r['baseline_score']} -> {r['final_score']} ({r['final_status']})")


if __name__ == "__main__":
    main()
