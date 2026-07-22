"""
Re-verify every project from forgebench_v1_results.json and
forgebench_hard_results.json with the FIXED rescorer (architecture loaded
from metadata.json) under the correct venv Python (has playwright). Both
bugs were silently under-scoring every project generated/verified during
this session -- e.g. university_course_management's real score is 96.67,
not the ~76 baseline on record. This produces a corrected, trustworthy
score for every existing project before deciding what actually still needs
repair, instead of chasing phantom failures.

Read-only: does not modify any project's files, just re-verifies them.

Usage:
    C:\\...\\backend\\venv\\Scripts\\python.exe scripts\\rescore_all.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_ROOT.parent
sys.path.insert(0, str(_BACKEND_ROOT))
sys.path.insert(0, str(_BACKEND_ROOT / "scripts"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from _project_rescorer import rescore_project

RESULTS_PATH = _BACKEND_ROOT / "benchmark_results" / "corrected_scores.json"
GENERATED_PROJECTS = _REPO_ROOT / "generated_projects"

SOURCES = [
    (_BACKEND_ROOT / "benchmark_results" / "forgebench_v1_results.json", "forgebench_v1"),
    (_BACKEND_ROOT / "benchmark_results" / "forgebench_hard_results.json", "forgebench_hard"),
]


def _load_candidates() -> list[dict]:
    out = []
    for results_path, source in SOURCES:
        if not results_path.exists():
            continue
        data = json.loads(results_path.read_text(encoding="utf-8"))
        for r in data["results"]:
            name = r.get("project_name")
            old_score = r.get("forge_score")
            if not name:
                continue
            project_dir = GENERATED_PROJECTS / name
            if not project_dir.is_dir():
                continue
            if source == "forgebench_v1":
                from forgebench_v1 import APPS
                idea = dict(APPS)[r["app"]]
            else:
                from forgebench_hard import APPS as HARD_APPS
                idea = dict(HARD_APPS)[r["app"]]
            out.append({"app_key": r["app"], "project_name": name,
                        "project_dir": str(project_dir), "idea": idea,
                        "old_score": old_score, "source": source})
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
        "results": results,
    }, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def main():
    candidates = _load_candidates()
    results = _load_results()
    done_names = {r["project_name"] for r in results}

    print(f"\n{'='*70}\n  RESCORE-ALL -- {len(candidates)} projects\n{'='*70}")
    for c in candidates:
        if c["project_name"] in done_names:
            continue
        print(f"\n--- {c['project_name']} (old score {c['old_score']}) ---")
        try:
            r = rescore_project(Path(c["project_dir"]), c["idea"], c["project_name"])
            new_score, grade = r["forge_score"], r["grade"]
        except Exception as e:
            new_score, grade = None, None
            r = {"error": str(e)}
        entry = {"project_name": c["project_name"], "app_key": c["app_key"],
                  "source": c["source"], "old_score": c["old_score"],
                  "new_score": new_score, "grade": grade, "rescore": r}
        results.append(entry)
        _save_results(results)
        delta = (new_score - c["old_score"]) if (new_score is not None and c["old_score"] is not None) else None
        print(f"  [{c['project_name']}] {c['old_score']} -> {new_score} ({grade}) delta={delta}")

    print(f"\n{'='*70}\n  RESCORE-ALL COMPLETE\n{'='*70}")
    for r in results:
        print(f"  {r['project_name']}: {r['old_score']} -> {r['new_score']} ({r['grade']})")


if __name__ == "__main__":
    main()
