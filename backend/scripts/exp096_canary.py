"""
Experiment 096: Live Validation of Architecture-Aware Update Methods (Exp095).

Measures whether the Exp095 journey-runner fix (_detect_crud_entity()
returning (resource, update_method), do_edit() using it instead of a
hardcoded PUT) behaves correctly during a REAL generation, with a
custom idea chosen from a domain historically prone to the architect
choosing PATCH (Exp094: sports_league_manager -- PATCH on every
single-entity update route, no PUT anywhere in the app).

Per this experiment's constraints: one or two Cerebras canaries only,
domain preference (sports/league/project-management/task-tracking), no
implementation changes unless a new deterministic issue surfaces.

Instrumentation, non-invasive (production functions called for real,
only observed) -- same methodology as Exp079/082/086/089/093:
  - wraps `app.runtime.user_journey_runner._detect_crud_entity` to
    record the architecture-declared (resource, update_method) tuple
    for every call.
  - wraps `app.runtime.user_journey_runner.run_user_journey` to record
    the full JourneyResult (all steps' passed/detail/request/response),
    particularly "Edit entity", so ExchangeRecorder's captured
    method/url can be directly inspected.
Both call sites (`backend_runner.py`, `playwright_workflow.py`) import
`run_user_journey` inline, at call time, so patching the module
attribute before generation starts is sufficient -- no need to patch
each caller separately.

Usage:
    python scripts/exp096_canary.py --label exp096-validation-r1 --no-deploy --provider cerebras
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))
sys.path.insert(0, str(_BACKEND_ROOT / "scripts"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import run_canary
from run_canary import (
    HISTORY_PATH, _load_history, _save_history, _acquire_lock, _release_lock, _regressed,
)

INVOCATIONS_PATH = _BACKEND_ROOT / "benchmark_results" / "exp096_method_detection_invocations.json"

# A sports-league idea closely matching the real, already-analyzed
# sports_league_manager app (Exp094's confirmed PATCH-only case) --
# best odds of reproducing the same architect verb choice, though not
# guaranteed (LLM instruction-following variance, same caveat noted in
# every prior live-validation cycle in this series).
_SPORTS_LEAGUE_IDEA = (
    "A sports league management platform with user login. Admins create, "
    "edit, and archive leagues (name, season, start/end dates). Add teams "
    "to a league, edit team details, and assign coaches. Maintain player "
    "rosters (name, position, jersey number) with unique jersey numbers "
    "per team, and allow editing player details. Schedule matches between "
    "teams, record match results/scores, and automatically compute league "
    "standings (wins, draws, losses, points) sorted by ranking."
)

# A project/task-management idea closely matching the real, already-
# analyzed teamflow_pm app (Exp094's confirmed PATCH-only case:
# `PATCH /api/tasks/{task_id}`) -- second domain per this experiment's
# preference list, tried after Run 1 (sports league) resolved to PUT.
_TASK_MANAGEMENT_IDEA = (
    "A project management tool with secure user login. Users create, "
    "edit, and delete projects with a name and description. Within each "
    "project, create tasks with a title, description, due date, and "
    "status (todo/in_progress/done); edit task details and update their "
    "status as work progresses. Add other users to a project as members "
    "with a manager or member role, and enforce that only managers can "
    "delete the project or remove members."
)

_IDEAS = {
    "sports_league": _SPORTS_LEAGUE_IDEA,
    "task_management": _TASK_MANAGEMENT_IDEA,
}

_detect_calls: list[dict] = []
_journey_results: list[dict] = []


def _make_detect_wrapper(original_fn):
    def wrapper(architecture, api_prefix):
        result = original_fn(architecture, api_prefix)
        _detect_calls.append({
            "api_prefix": api_prefix,
            "result": list(result) if result else None,
        })
        return result
    return wrapper


def _make_journey_wrapper(original_fn):
    def wrapper(project_path, architecture=None, backend_port=8001):
        result = original_fn(project_path, architecture, backend_port=backend_port)
        steps_summary = []
        for s in result.steps:
            entry = {"name": s.name, "passed": s.passed, "detail": s.detail}
            if s.request:
                entry["request"] = s.request
            if s.response:
                entry["response"] = s.response
            steps_summary.append(entry)
        _journey_results.append({
            "entity": result.entity,
            "success": result.success,
            "steps_passed": result.steps_passed,
            "steps_failed": result.steps_failed,
            "steps": steps_summary,
        })
        return result
    return wrapper


def _install_instrumentation():
    import app.runtime.user_journey_runner as ujr
    original_detect = ujr._detect_crud_entity
    original_journey = ujr.run_user_journey
    ujr._detect_crud_entity = _make_detect_wrapper(original_detect)
    ujr.run_user_journey = _make_journey_wrapper(original_journey)
    return ujr, original_detect, original_journey


def _uninstall_instrumentation(ujr, original_detect, original_journey):
    ujr._detect_crud_entity = original_detect
    ujr.run_user_journey = original_journey


def main():
    parser = argparse.ArgumentParser(description="Experiment 096 canary: live validation of Exp095's architecture-aware update method")
    parser.add_argument("--label", default="exp096-validation")
    parser.add_argument("--no-deploy", action="store_true")
    parser.add_argument("--provider", default="cerebras")
    parser.add_argument("--idea-key", default="sports_league")
    args = parser.parse_args()

    ujr, original_detect, original_journey = _install_instrumentation()
    _acquire_lock(args.label)
    try:
        deploy = not args.no_deploy
        history = _load_history()
        prior_by_app = {}
        if history["runs"]:
            last_run = history["runs"][-1]
            prior_by_app = {r["app"]: r for r in last_run["results"]}

        r = run_canary._check_result(args.idea_key, _IDEAS[args.idea_key], deploy, provider=args.provider)

        print(f"\n{'='*70}\n  EXP096 CANARY SUMMARY  ({args.label})\n{'='*70}")
        prev = prior_by_app.get(r["app"])
        regressions = _regressed(prev, r) if prev else []
        status = "REGRESSION" if regressions else ("BASELINE" if not prev else "OK")
        print(f"  [{status:10}] {r['app']:10} score={r['forge_score']:.1f}  "
              f"build={r['build_ok']} runtime={r['runtime_ok']} crud={r['crud_ok']} "
              f"browser={r['browser_ok']} deployed={r['deployed']}  ({r['elapsed_s']}s)")
        for reason in regressions:
            print(f"                 -- {reason}")

        history["runs"].append({
            "label": args.label,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "deploy": deploy,
            "results": [r],
        })
        history["runs"] = history["runs"][-50:]
        _save_history(history)

        INVOCATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        prior_invocations = []
        if INVOCATIONS_PATH.exists():
            try:
                prior_invocations = json.loads(INVOCATIONS_PATH.read_text(encoding="utf-8")).get("all_runs", [])
            except Exception:
                pass
        prior_invocations.append({
            "label": args.label,
            "detect_calls": _detect_calls,
            "journey_results": _journey_results,
        })
        INVOCATIONS_PATH.write_text(
            json.dumps({"all_runs": prior_invocations}, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        print(f"\n_detect_crud_entity() calls: {len(_detect_calls)}")
        for call in _detect_calls:
            print(f"  api_prefix={call['api_prefix']!r} -> {call['result']}")

        print(f"\nJourney results captured: {len(_journey_results)}")
        for jr in _journey_results:
            print(f"  entity={jr['entity']} success={jr['success']} passed={jr['steps_passed']} failed={jr['steps_failed']}")
            for s in jr["steps"]:
                if s["name"] == "Edit entity":
                    print(f"    Edit entity: passed={s['passed']} detail={s['detail']}")
                    if "request" in s:
                        print(f"      request:  {s['request']}")
                    if "response" in s:
                        print(f"      response: {s['response']}")

        print(f"\n{'='*70}")
        if regressions:
            print("  EXP096 CANARY: regression(s) detected vs. prior run")
        else:
            print("  EXP096 CANARY: no regression detected")
        print(f"{'='*70}\n")
    finally:
        _release_lock()
        _uninstall_instrumentation(ujr, original_detect, original_journey)


if __name__ == "__main__":
    main()
