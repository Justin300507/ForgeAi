"""
Experiment 056: post-hardening reliability baseline. Measurement only --
implements no fixes, changes no production code.

Calls generate_project_v15() exactly like run_canary.py does (same
CANARY_APPS list, same idea files, same concurrency lock reused from
run_canary.py rather than duplicated) but captures the FULL result dict
-- including fields run_canary.py's own _check_result() strips out
(timeline, score_history, token_usage, deployment_result) -- plus the
complete captured stdout for each app, to disk under
benchmark_results/exp056/. Does NOT touch canary_history.json, so it
can't interfere with run_canary.py's own regression-gate history or
accidentally corrupt the "last known good" baseline that other
experiments compare against.

Usage:
    python scripts/exp056_measure.py --round 1
    python scripts/exp056_measure.py --round 2 --provider auto
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

OUT_DIR = _BACKEND_ROOT / "benchmark_results" / "exp056"


def run_round(round_num: int, provider: str = "auto", deploy: bool = False) -> list[dict]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _acquire_lock(f"exp056-round{round_num}")
    round_results = []
    try:
        for idea_key, filename in CANARY_APPS:
            idea = (BENCHMARKS_DIR / filename).read_text(encoding="utf-8").strip()
            print(f"\n{'='*70}\n  EXP056 round {round_num}: {idea_key}\n{'='*70}")
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
            sys.stdout.write(log_text[-4000:])  # tail to console so the run is watchable live

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
            print(f"[exp056] round {round_num} {idea_key}: crashed={crashed} "
                  f"score={result.get('forge_score')} grade={result.get('grade')} "
                  f"fix_attempts={result.get('fix_attempts')} elapsed={elapsed}s")
    finally:
        _release_lock()

    combined_path = OUT_DIR / f"round{round_num}_combined.json"
    combined_path.write_text(json.dumps(round_results, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return round_results


def main():
    parser = argparse.ArgumentParser(description="Exp056 post-hardening reliability baseline")
    parser.add_argument("--round", type=int, required=True, help="Round number (for filenames/labels)")
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--deploy", action="store_true", help="Deploy (off by default -- measurement only)")
    args = parser.parse_args()
    run_round(args.round, provider=args.provider, deploy=args.deploy)


if __name__ == "__main__":
    main()
