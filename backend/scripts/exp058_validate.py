"""
Experiment 058: live validation of Exp057's fix (the Exp053 regression
Exp056 found and Exp057 fixed offline). Measurement only -- implements
no fixes, changes no production code.

Same pattern as exp056_measure.py (reuses run_canary.py's CANARY_APPS
list, idea files, and concurrency lock -- no changes to run_canary.py
itself), same full-result-dict capture. Difference: Exp058's own budget
explicitly targets "the minimum number of Cerebras generations" and its
Primary Questions are all about `todo` specifically (the app with the
clearest, least-confounded regression signal in Exp056 -- blog_cms was
already failing runtime before Exp053, muddying interpretation; crm
never needed the runtime-fix loop at all). Defaults to running ONLY
`todo` per round, not all 3 canary apps, to avoid "consuming credits
chasing secondary issues" per the experiment's own rule. Pass
--apps todo,blog_cms,crm to widen if ever needed.

Usage:
    python scripts/exp058_validate.py --round 1
    python scripts/exp058_validate.py --round 2 --apps todo
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))
sys.path.insert(0, str(_BACKEND_ROOT / "scripts"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.services.v15_orchestrator import generate_project_v15
from run_canary import CANARY_APPS, BENCHMARKS_DIR, _acquire_lock, _release_lock

OUT_DIR = _BACKEND_ROOT / "benchmark_results" / "exp058"


def run_round(round_num: int, apps: list[str], provider: str = "auto", deploy: bool = False) -> list[dict]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    app_map = dict(CANARY_APPS)
    selected = [(k, app_map[k]) for k in apps if k in app_map]

    _acquire_lock(f"exp058-round{round_num}")
    round_results = []
    try:
        for idea_key, filename in selected:
            idea = (BENCHMARKS_DIR / filename).read_text(encoding="utf-8").strip()
            print(f"\n{'='*70}\n  EXP058 round {round_num}: {idea_key}\n{'='*70}")
            buf = io.StringIO()
            t0 = time.time()
            crashed = False
            error = None
            result: dict = {}
            try:
                with contextlib.redirect_stdout(buf):
                    result = generate_project_v15(idea=idea, provider=provider, deploy=deploy)
            except Exception as exc:
                crashed = True
                error = f"{type(exc).__name__}: {exc}"
            elapsed = round(time.time() - t0, 1)
            log_text = buf.getvalue()
            sys.stdout.write(log_text[-4000:])

            (OUT_DIR / f"round{round_num}_{idea_key}.log").write_text(log_text, encoding="utf-8")
            record = {
                "round": round_num,
                "app": idea_key,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "crashed": crashed,
                "error": error,
                "elapsed_s": elapsed,
                "result": result,
            }
            (OUT_DIR / f"round{round_num}_{idea_key}.json").write_text(
                json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
            )
            round_results.append(record)
            name_error_present = "patch_model_field_mismatches' is not defined" in log_text
            print(f"[exp058] round {round_num} {idea_key}: crashed={crashed} "
                  f"score={result.get('forge_score')} grade={result.get('grade')} "
                  f"fix_attempts={result.get('fix_attempts')} elapsed={elapsed}s "
                  f"NameError_present={name_error_present}")
    finally:
        _release_lock()

    combined_path = OUT_DIR / f"round{round_num}_combined.json"
    combined_path.write_text(json.dumps(round_results, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return round_results


def main():
    parser = argparse.ArgumentParser(description="Exp058 live regression validation")
    parser.add_argument("--round", type=int, required=True)
    parser.add_argument("--apps", default="todo", help="comma-separated subset of CANARY_APPS keys")
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--deploy", action="store_true")
    args = parser.parse_args()
    run_round(args.round, [a.strip() for a in args.apps.split(",") if a.strip()],
               provider=args.provider, deploy=args.deploy)


if __name__ == "__main__":
    main()
