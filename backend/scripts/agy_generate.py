"""
Greenfield generation via the `agy` (Google Antigravity) CLI instead of the
internal V15 pipeline -- the counterpart to scripts/forgebench_hard.py, but
building each app with `agy -p` from an empty directory rather than
generate_project_v15(). Re-scores every result with the SAME
VerificationEngine/ScoringEngine the live pipeline uses (via
scripts/_project_rescorer.py), so results are directly comparable to any
pipeline-generated app's ForgeScore.

Each app is generated in its own fresh generated_projects/<name>/ directory
(agy invoked with cwd set there + --new-project, confirmed via smoke test
to be the correct way to scope agy to a specific directory -- --add-dir
alone does NOT do this).

Usage:
    python scripts/agy_generate.py
    python scripts/agy_generate.py --start-at 3
"""
from __future__ import annotations

import argparse
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

RESULTS_PATH = _BACKEND_ROOT / "benchmark_results" / "agy_generate_results.json"
GENERATED_PROJECTS = _REPO_ROOT / "generated_projects"
AGY_SUBPROCESS_TIMEOUT_S = 2400  # hard kill if agy itself hangs
AGY_PRINT_TIMEOUT = "35m0s"      # agy's own internal wait budget -- greenfield build is bigger than a fix

STRUCTURE_SPEC = (
    "Build the app directly in the current directory (do not create a "
    "subdirectory) using EXACTLY this layout, matching this project's "
    "existing generated-app convention:\n"
    "  app/main.py            -- FastAPI app entrypoint (`app = FastAPI()`)\n"
    "  app/database.py        -- SQLAlchemy engine/session setup, SQLite file "
    "at ./app.db\n"
    "  app/models/            -- SQLAlchemy ORM models\n"
    "  app/schemas/           -- Pydantic request/response schemas\n"
    "  app/routes/            -- FastAPI routers, one per resource, "
    "included in main.py\n"
    "  app/services/          -- business logic used by routes\n"
    "  app/requirements.txt   -- backend Python deps (fastapi, uvicorn, "
    "sqlalchemy, pydantic, python-jose or pyjwt, bcrypt or passlib, "
    "python-multipart)\n"
    "  src/                   -- React frontend (Vite), App.jsx as the "
    "root component, react-router-dom for routing\n"
    "  package.json, index.html, vite.config.js, postcss.config.js, "
    "tailwind.config.js -- frontend build config (Tailwind for styling)\n"
    "  .env.example            -- documents required env vars\n\n"
    "Requirements: JWT-based auth (register/login) matching the roles "
    "described below, protected API routes, a polished React UI (Tailwind) "
    "covering every entity's full CRUD, and correct relational integrity "
    "(foreign keys, cascade rules implied by the spec). The backend must "
    "start cleanly with `uvicorn app.main:app`, and `npm install && npm "
    "run build` must succeed with zero errors. Do not leave any TODO, "
    "stub, or placeholder implementation -- every described feature must "
    "actually work end-to-end."
)

APPS: list[tuple[str, str]] = [
    ("g01_tournament_bracket_manager", (
        "An esports tournament organizer platform with organizer and "
        "team-captain roles. Organizers create a tournament and open team "
        "registration; team captains register their team. Once "
        "registration closes, the organizer generates a double-"
        "elimination bracket (a winners bracket and a losers bracket) "
        "from the registered teams. Organizers record match results; a "
        "match loss in the winners bracket drops a team into the losers "
        "bracket, while a loss in the losers bracket eliminates the team "
        "entirely. Winners-bracket wins advance a team forward through "
        "that bracket. Organizers and captains can both view the current "
        "state of both brackets and each match's scheduled time."
    )),
    ("g02_clinical_trial_enrollment", (
        "A clinical trial enrollment platform with coordinator and "
        "physician roles. Coordinators define trial protocols, each with "
        "eligibility criteria: a minimum and maximum patient age, a list "
        "of required conditions, and a list of excluded conditions. "
        "Coordinators screen a patient against a protocol by entering the "
        "patient's age and conditions; the system determines and records "
        "whether the patient is eligible based on the criteria. Eligible "
        "patients can be enrolled and are scheduled for a fixed sequence "
        "of study visits. Physicians log adverse events for an enrolled "
        "patient with a severity (mild, moderate, or severe); any severe "
        "event is automatically flagged for coordinator review."
    )),
    ("g03_multi_currency_expense_ledger", (
        "A multi-currency expense ledger for small teams, with admin and "
        "member roles. Admins set a base currency and define supported "
        "currencies with an exchange rate to the base currency (rates are "
        "updated manually by admins, not fetched live). Members submit "
        "expenses in any supported currency; the system stores both the "
        "original amount/currency and the computed base-currency amount "
        "at the time of submission. Admins approve or reject expenses; "
        "approved expenses count toward a monthly team total shown in the "
        "base currency. Members view their own submission history with "
        "both original and converted amounts."
    )),
    ("g04_warehouse_pick_pack_workflow", (
        "A warehouse pick-and-pack fulfillment system with picker and "
        "supervisor roles. Supervisors create orders, each with a list of "
        "SKU line items and quantities. Orders enter a pick queue; a "
        "picker claims an unclaimed order (locking it from other "
        "pickers), marks each line item as picked (which decrements that "
        "SKU's on-hand inventory, rejecting the pick if insufficient "
        "stock remains), and then marks the order fully packed once every "
        "line item is picked. Supervisors see the pick queue with each "
        "order's status (unclaimed, picking, packed, shipped) and can "
        "mark a packed order shipped. Low-stock SKUs (below a defined "
        "reorder threshold) are flagged on the supervisor's inventory "
        "view."
    )),
]

assert len({k for k, _ in APPS}) == len(APPS), "duplicate app keys in agy_generate APPS"


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
        "benchmark": "agy-generate",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_apps": len(APPS),
        "results": results,
    }, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _run_agy_build(project_dir: Path, idea: str) -> dict:
    prompt = f"Build this application from scratch:\n\n{idea}\n\n{STRUCTURE_SPEC}"
    cmd = [
        "agy", "-p", prompt,
        "--new-project",
        "--dangerously-skip-permissions",
        "--print-timeout", AGY_PRINT_TIMEOUT,
    ]
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=str(project_dir), capture_output=True, text=True,
            timeout=AGY_SUBPROCESS_TIMEOUT_S, encoding="utf-8", errors="replace",
        )
        return {"ok": proc.returncode == 0, "returncode": proc.returncode,
                "stdout_tail": proc.stdout[-3000:], "stderr_tail": proc.stderr[-2000:],
                "elapsed_s": round(time.time() - t0, 1)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"agy timed out after {AGY_SUBPROCESS_TIMEOUT_S}s",
                "elapsed_s": round(time.time() - t0, 1)}
    except FileNotFoundError:
        return {"ok": False, "error": "agy CLI not found on PATH", "elapsed_s": 0.0}


def main():
    parser = argparse.ArgumentParser(description="Greenfield app generation via the agy CLI")
    parser.add_argument("--start-at", type=int, default=1)
    args = parser.parse_args()

    results = _load_results()
    done_keys = {r["app"] for r in results}

    print(f"\n{'='*70}\n  AGY-GENERATE -- {len(APPS)} apps\n{'='*70}")
    for i, (app_key, idea) in enumerate(APPS, start=1):
        if i < args.start_at or app_key in done_keys:
            continue
        project_dir = GENERATED_PROJECTS / app_key
        project_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n{'='*70}\n  AGY-GENERATE -- App {i}/{len(APPS)}: {app_key}\n{'='*70}")

        build_result = _run_agy_build(project_dir, idea)
        print(f"  [{app_key}] agy build ok={build_result.get('ok')} elapsed={build_result.get('elapsed_s')}s")

        try:
            rescore = rescore_project(project_dir, idea, app_key)
            score, grade = rescore["forge_score"], rescore["grade"]
        except Exception as e:
            rescore = {"error": str(e)}
            score, grade = 0.0, "F"

        entry = {"app": app_key, "project_name": app_key, "build": build_result,
                  "forge_score": score, "grade": grade, "rescore": rescore}
        results.append(entry)
        _save_results(results)
        print(f"  [{app_key}] score={score:.1f} ({grade})")

    print(f"\n{'='*70}\n  AGY-GENERATE -- {len(results)}/{len(APPS)} apps done\n{'='*70}")


if __name__ == "__main__":
    main()
