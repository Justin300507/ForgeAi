"""
Experiment 103: Live Validation of Exp102's Role-Vocabulary Discovery Extension.

Hypothesis (one sentence): a live generation of the ForgeBench
15_event_management idea -- the exact app whose required-role-field /
getattr()-gate shapes Exp102's regex extensions were built from -- now
resolves a role vocabulary via _discover_role_vocabulary(), so the
V20.1.5 role-aware journey retry can elevate past a legitimate 403
instead of recording a phantom JourneyCRUDFailure.

Exp102 validated the fix only against static, already-generated corpus
code (73-project replay, 9 vocabularies found vs 6 before, zero
regression). This is the queued live confirmation before any
ForgeBench v1.1: one Cerebras generation, no deploy, no code changes
unless a new deterministic issue surfaces.

Instrumentation, non-invasive (production functions called for real,
only observed) -- same methodology as Exp079/082/086/089/093/096/099:
- wraps app.services.deterministic_patcher._discover_role_vocabulary
  (called lazily by both the auth-template patcher and the journey
  runner's 403 retry, so a module-attribute wrap captures every call)
  to record each invocation's result;
- wraps app.runtime.user_journey_runner.run_user_journey (imported
  lazily by backend_runner and playwright_workflow) to record every
  journey step's name/passed/detail, so an "elevated after 403" retry
  is directly observable rather than inferred from the score.

Usage:
    python scripts/exp103_canary.py --label exp103-validation-r1 --no-deploy --provider cerebras
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

from run_canary import (
    _check_result, _load_history, _save_history,
    _acquire_lock, _release_lock, _regressed,
)

OBSERVATIONS_PATH = _BACKEND_ROOT / "benchmark_results" / "exp103_role_discovery_observations.json"

# The exact ForgeBench v1 idea (forgebench_v1.py, "15_event_management")
# that produced event_manager_platform's confirmed Option A incident.
_EVENT_MANAGEMENT_IDEA = (
    "An event management platform with organizer and attendee "
    "roles. Organizers create events (name, description, date, "
    "location, capacity). Attendees browse upcoming events and "
    "register to attend; registration is blocked once an event "
    "reaches capacity. Organizers see a list of registered attendees "
    "per event. Attendees can cancel their own registration. "
    "Organizers view total registrations and remaining capacity per "
    "event."
)

_discovery_calls: list[dict] = []
_journey_records: list[dict] = []


def _install_instrumentation():
    import app.services.deterministic_patcher as dp
    import app.runtime.user_journey_runner as ujr

    original_discover = dp._discover_role_vocabulary
    original_journey = ujr.run_user_journey

    def discover_wrapper(project_path):
        result = original_discover(project_path)
        _discovery_calls.append({
            "project_path": str(project_path),
            "result": list(result) if result else None,
        })
        return result

    def journey_wrapper(*args, **kwargs):
        jr = original_journey(*args, **kwargs)
        try:
            _journey_records.append({
                "success": getattr(jr, "success", None),
                "skipped": getattr(jr, "skipped", None),
                "entity": getattr(jr, "entity", ""),
                "steps": [
                    {"name": s.name, "passed": s.passed, "detail": s.detail}
                    for s in getattr(jr, "steps", [])
                ],
            })
        except Exception:
            pass
        return jr

    dp._discover_role_vocabulary = discover_wrapper
    ujr.run_user_journey = journey_wrapper
    return (dp, original_discover), (ujr, original_journey)


def _uninstall_instrumentation(discover_pair, journey_pair):
    dp, original_discover = discover_pair
    ujr, original_journey = journey_pair
    dp._discover_role_vocabulary = original_discover
    ujr.run_user_journey = original_journey


def main():
    parser = argparse.ArgumentParser(
        description="Experiment 103 canary: live validation of Exp102's role-vocabulary discovery extension")
    parser.add_argument("--label", default="exp103-validation")
    parser.add_argument("--no-deploy", action="store_true")
    parser.add_argument("--provider", default="cerebras")
    args = parser.parse_args()

    discover_pair, journey_pair = _install_instrumentation()
    _acquire_lock(args.label)
    try:
        deploy = not args.no_deploy
        history = _load_history()
        prior_by_app = {}
        if history["runs"]:
            prior_by_app = {r["app"]: r for r in history["runs"][-1]["results"]}

        r = _check_result("event_management", _EVENT_MANAGEMENT_IDEA, deploy,
                          provider=args.provider)

        print(f"\n{'='*70}\n  EXP103 CANARY SUMMARY  ({args.label})\n{'='*70}")
        prev = prior_by_app.get(r["app"])
        regressions = _regressed(prev, r) if prev else []
        status = "REGRESSION" if regressions else ("BASELINE" if not prev else "OK")
        print(f"  [{status:10}] {r['app']:16} score={r['forge_score']:.1f}  "
              f"build={r['build_ok']} runtime={r['runtime_ok']} crud={r['crud_ok']} "
              f"browser={r['browser_ok']}  ({r['elapsed_s']}s)")
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

        # ── Mechanism observations ─────────────────────────────────────
        print(f"\n_discover_role_vocabulary() invocations: {len(_discovery_calls)}")
        for c in _discovery_calls:
            print(f"  {Path(c['project_path']).name}: {c['result']}")

        elevated = False
        print(f"\nJourney runs observed: {len(_journey_records)}")
        for jrec in _journey_records:
            print(f"  entity={jrec['entity']!r} success={jrec['success']} skipped={jrec['skipped']}")
            for s in jrec["steps"]:
                marker = "PASS" if s["passed"] else "FAIL"
                print(f"    [{marker}] {s['name']}: {s['detail']}")
                if "elevated after 403" in (s["detail"] or ""):
                    elevated = True

        OBSERVATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        prior_obs = []
        if OBSERVATIONS_PATH.exists():
            try:
                prior_obs = json.loads(OBSERVATIONS_PATH.read_text(encoding="utf-8")).get("all_runs", [])
            except Exception:
                pass
        prior_obs.append({
            "label": args.label,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": r,
            "discovery_calls": _discovery_calls,
            "journeys": _journey_records,
        })
        OBSERVATIONS_PATH.write_text(
            json.dumps({"all_runs": prior_obs}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print(f"\n{'='*70}")
        vocab_found = any(c["result"] for c in _discovery_calls)
        if vocab_found:
            print("  EXP103: role vocabulary DISCOVERED during live run")
        else:
            print("  EXP103: no role vocabulary discovered -- either the generated "
                  "app declared none (regenerate to check shape), or discovery "
                  "failed on a new, unhandled shape (inspect the project source)")
        if elevated:
            print("  EXP103: role-aware retry ELEVATED past a 403 -- full mechanism confirmed live")
        else:
            print("  EXP103: no 403 elevation observed this run (only meaningful "
                  "if Create actually 403'd; a plain-pass Create is also a success)")
        print(f"{'='*70}\n")
    finally:
        _release_lock()
        _uninstall_instrumentation(discover_pair, journey_pair)


if __name__ == "__main__":
    main()
