"""
One-off runner for 2 tougher, multi-role/multi-entity apps (hospital
management, advanced fleet management) with explicit landing-page style
variety -- NOT a change to run_canary.py's fixed 3-app canary.

Calls generate_project_v15() directly, sequentially, with deploy=True and
different style_override values so the visual-polish landing pages get
real variety instead of always defaulting to Auto.

Usage:
    python scripts/run_tougher_apps.py
    python scripts/run_tougher_apps.py --apps hospital
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.services.v15_orchestrator import generate_project_v15

APPS = {
    "hospital": (
        "hospital_management",
        (_BACKEND_ROOT.parent / "benchmarks" / "advanced" / "hospital.txt")
        .read_text(encoding="utf-8").strip(),
        "neubrutalist",
        "bold",
    ),
    "fleet": (
        "fleet_management",
        (_BACKEND_ROOT.parent / "benchmarks" / "advanced" / "fleet_advanced.txt")
        .read_text(encoding="utf-8").strip(),
        "bento",
        "moderate",
    ),
}

RESULTS_PATH = _BACKEND_ROOT / "benchmark_results" / "tougher_apps_pass.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apps", default="hospital,fleet")
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--no-deploy", action="store_true")
    args = parser.parse_args()

    wanted = [a.strip() for a in args.apps.split(",") if a.strip()]
    results = []

    for key in wanted:
        if key not in APPS:
            print(f"Unknown app key: {key}")
            continue
        name, idea, style, motion = APPS[key]
        print(f"\n{'='*70}\n  RUN: {key} ({name}) style={style} motion={motion}\n{'='*70}")
        t0 = time.time()
        try:
            result = generate_project_v15(
                idea=idea,
                provider=args.provider,
                deploy=not args.no_deploy,
                deploy_to="both",
                style_override=style,
                motion_intensity=motion,
                include_landing_page=True,
            )
            elapsed = round(time.time() - t0, 1)
            score = result.get("forge_score", {})
            print(
                f"  DONE {key}: score={score} "
                f"deployed={result.get('deployed')} "
                f"project_path={result.get('project_path')} "
                f"elapsed={elapsed}s"
            )
            results.append({
                "app": key, "name": name, "style": style, "motion": motion,
                "elapsed_s": elapsed,
                "forge_score": score,
                "deployed": result.get("deployed"),
                "deploy_urls": result.get("deploy_urls") or result.get("deployment"),
                "project_path": result.get("project_path"),
                "crashed": False,
            })
        except Exception as exc:
            elapsed = round(time.time() - t0, 1)
            print(f"  CRASHED {key}: {type(exc).__name__}: {exc}")
            results.append({
                "app": key, "name": name, "style": style, "motion": motion,
                "elapsed_s": elapsed,
                "crashed": True, "error": f"{type(exc).__name__}: {exc}",
            })

        RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULTS_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n{'='*70}\n  ALL DONE -- summary written to {RESULTS_PATH}\n{'='*70}")
    for r in results:
        print(" ", r["app"], "->", r.get("forge_score"), "deployed:", r.get("deployed"), "crashed:", r.get("crashed"))


if __name__ == "__main__":
    main()
