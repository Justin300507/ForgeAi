"""
ForgeBench v1.0 -- 25-application reliability benchmark against the LIVE
V15 pipeline (`generate_project_v15`), per the user's explicit spec.

Deliberately NOT `run_forgebench.py --suite golden`: that runner's
`ForgeAIGenerator` calls the legacy `app.services.project_service
.generate_project` path, not V15 -- confirmed broken for this purpose
(Exp019's own housekeeping note: a prior attempt scored 0.0 on all 3
apps and was abandoned in favor of `run_canary.py`'s direct
`generate_project_v15()` call, which every canary/experiment script in
this project's history has used since). This script follows that same,
proven pattern -- a new, standalone file; zero changes to any existing
ForgeAI module, per this benchmark's own "do not modify ForgeAI" rule.

Sequential by design (not parallel): a first, v1.0-scale run should
prioritize consistent, uncontended resource usage (each generation
spawns its own uvicorn + npm build) over wall-clock speed. Parallelism
is a natural v1.1 (100-app) scale-up candidate, not this run's concern.

Writes incrementally after every app (benchmark_results/forgebench_v1_results.json)
so a partial run is never lost, and prints a one-line summary per app
for progress visibility without needing dimension-level detail.

Usage:
    python scripts/forgebench_v1.py                     # run all 25, resume if partial results exist
    python scripts/forgebench_v1.py --start-at 5         # resume from app #5 (1-indexed)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))
sys.path.insert(0, str(_BACKEND_ROOT / "scripts"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.services.v15_orchestrator import generate_project_v15
from run_canary import _acquire_lock, _release_lock, _dim_lookup, _DIM_MAP

RESULTS_PATH = _BACKEND_ROOT / "benchmark_results" / "forgebench_v1_results.json"
GEN_LOG_PATH = _BACKEND_ROOT / "failure_memory" / "generation_log.jsonl"

# ---------------------------------------------------------------------------
# 25 applications. 10 reuse existing benchmarks/golden/*.txt ideas verbatim
# (already-vetted wording); 1 reuses Exp096's live-tested sports-league idea;
# 14 are new, written in the same style (user roles, 2-4 entities, CRUD
# focus, 2-3 distinguishing features) as the existing golden corpus.
# ---------------------------------------------------------------------------

_GOLDEN = _BACKEND_ROOT.parent / "benchmarks" / "golden"


def _golden(name: str) -> str:
    return (_GOLDEN / name).read_text(encoding="utf-8").strip()


APPS: list[tuple[str, str]] = [
    ("01_todo_list", _golden("01_todo.txt")),
    ("02_notes_app", (
        "A personal note-taking app with user login. Users create notes "
        "with a title, body text, and tags. Notes can be pinned, "
        "archived, or deleted. Users can search notes by title or tag, "
        "and filter by pinned/archived status. Each note shows created "
        "and last-edited timestamps."
    )),
    ("03_blog_cms", _golden("09_blog.txt")),
    ("04_inventory_manager", _golden("18_inventory.txt")),
    ("05_crm", _golden("04_crm.txt")),
    ("06_expense_tracker", _golden("02_expense_tracker.txt")),
    ("07_project_management", (
        "A project management tool with secure user login. Users create, "
        "edit, and delete projects with a name and description. Within "
        "each project, create tasks with a title, description, due date, "
        "and status (todo/in_progress/done); edit task details and update "
        "their status as work progresses. Add other users to a project as "
        "members with a manager or member role, and enforce that only "
        "managers can delete the project or remove members."
    )),
    ("08_task_manager", (
        "A team task manager with user login. Users create tasks with a "
        "title, description, assignee (another user), due date, and "
        "priority (low/medium/high). Tasks have status (todo/in_progress/"
        "review/done). Users can filter tasks by assignee, status, or "
        "priority. Dashboard shows task counts per status and overdue "
        "task count."
    )),
    ("09_recipe_manager", (
        "A recipe manager with user login. Users create recipes with a "
        "title, ingredients (list of name and quantity), instructions, "
        "prep time, and cuisine type. Users can mark recipes as favorites "
        "and rate them 1-5 stars. Search recipes by title, cuisine, or "
        "ingredient. Users can create, edit, and delete their own "
        "recipes."
    )),
    ("10_library_management", (
        "A library management system with librarian and member roles. "
        "Librarians manage books (title, author, ISBN, category, total "
        "copies, available copies). Members search the catalog and "
        "borrow available books; borrowing reduces available copies by "
        "one. Librarians record returns, which restores availability. "
        "Members view their current borrowed books and borrowing "
        "history. Librarians see overdue books (borrowed more than 14 "
        "days)."
    )),
    ("12_employee_directory", (
        "An employee directory with HR admin and employee roles. HR "
        "admins add employees (name, email, department, job title, hire "
        "date, manager). Employees can view the directory, search by "
        "name or department, and see an org chart showing manager "
        "relationships. HR admins can update employee details, change "
        "department or manager, and mark employees as inactive when they "
        "leave."
    )),
    ("13_help_desk", (
        "A help desk ticketing system with customer and agent roles. "
        "Customers submit support tickets with a subject, description, "
        "and priority (low/medium/high/urgent). Agents view all open "
        "tickets, assign tickets to themselves, and post replies. "
        "Tickets have status (open/in_progress/resolved/closed). "
        "Customers can view their own ticket history and add follow-up "
        "replies. Agents see a dashboard of open ticket counts by "
        "priority."
    )),
    ("14_gym_tracker", _golden("03_gym_tracker.txt")),
    ("15_event_management", (
        "An event management platform with organizer and attendee "
        "roles. Organizers create events (name, description, date, "
        "location, capacity). Attendees browse upcoming events and "
        "register to attend; registration is blocked once an event "
        "reaches capacity. Organizers see a list of registered attendees "
        "per event. Attendees can cancel their own registration. "
        "Organizers view total registrations and remaining capacity per "
        "event."
    )),
    ("16_restaurant_ordering", _golden("20_restaurant_pos.txt")),
    ("17_appointment_booking", _golden("08_booking.txt")),
    ("18_sports_league_manager", (
        "A sports league management platform with user login. Admins "
        "create, edit, and archive leagues (name, season, start/end "
        "dates). Add teams to a league, edit team details, and assign "
        "coaches. Maintain player rosters (name, position, jersey "
        "number) with unique jersey numbers per team, and allow editing "
        "player details. Schedule matches between teams, record match "
        "results/scores, and automatically compute league standings "
        "(wins, draws, losses, points) sorted by ranking."
    )),
    ("19_volunteer_management", (
        "A volunteer management system with coordinator and volunteer "
        "roles. Coordinators create volunteer opportunities (title, "
        "description, date, location, number of slots needed). "
        "Volunteers browse opportunities and sign up for available "
        "slots; sign-up is blocked once an opportunity is full. "
        "Coordinators track hours logged per volunteer after each "
        "opportunity. Volunteers view their upcoming sign-ups and total "
        "hours contributed."
    )),
    ("21_hotel_booking", (
        "A hotel booking system with hotel-staff and guest roles. Staff "
        "manage rooms (room number, type: single/double/suite, nightly "
        "rate, status: available/occupied/maintenance). Guests search "
        "available rooms by date range and room type, and book a room "
        "for a date range; overlapping bookings for the same room are "
        "rejected. Guests view their upcoming and past bookings. Staff "
        "can check guests in and out, which updates room status, and "
        "see today's arrivals and departures."
    )),
    ("22_course_management", (
        "A university course management system with registrar and "
        "student roles. Registrars create courses (code, title, "
        "credits, instructor name, capacity, semester). Students browse "
        "the course catalog and enroll in courses; enrollment is "
        "blocked once a course reaches capacity. Students view their "
        "enrolled courses and total credits for the semester. "
        "Registrars see enrollment counts per course and can drop a "
        "student from a course."
    )),
    ("23_vehicle_fleet_manager", (
        "A vehicle fleet management system with fleet-manager and "
        "driver roles. Fleet managers register vehicles (license plate, "
        "make, model, year, status: available/in_use/maintenance). "
        "Drivers request a vehicle for a trip (start date, end date, "
        "purpose); fleet managers approve or reject requests. Approved "
        "trips mark the vehicle in_use for that period. Fleet managers "
        "log maintenance records (date, description, cost) per vehicle "
        "and view total maintenance cost per vehicle. Drivers view their "
        "trip history."
    )),
    ("24_donation_tracker", (
        "A donation tracking system for a nonprofit with admin and donor "
        "roles. Admins manage fundraising campaigns (name, goal amount, "
        "start/end date, description). Donors make donations to a "
        "campaign (amount, date, optional message); campaign progress "
        "shows total raised versus goal. Admins see a list of donors per "
        "campaign and total raised across all campaigns. Donors view "
        "their own donation history across campaigns."
    )),
    ("25_medical_clinic_manager", _golden("19_hospital.txt")),
    # Moved to end (was #20): outer wrapper process killed by the
    # environment 3x in a row before producing any output at all (not a
    # detected hang -- no tasklist evidence of a stuck subprocess each
    # time, just an external kill). Revisit last rather than block the
    # rest of the queue.
    ("20_real_estate_listings", (
        "A real estate listings platform with agent and buyer roles. "
        "Agents create property listings (address, price, bedrooms, "
        "bathrooms, square footage, description, status: available/"
        "pending/sold). Buyers browse and search listings by price "
        "range, bedrooms, or location, and save favorite listings. "
        "Buyers can request a viewing for a listing; agents see and "
        "manage viewing requests (confirm/decline). Agents update "
        "listing status as deals progress."
    )),
    # Moved to end (was #11): hung 3x consecutively at generation, each
    # attempt with a flat/unchanged process memory footprint over 10-15
    # min (confirmed via tasklist before/after comparison, not assumed) --
    # a deterministic-looking pattern specific to this idea, not random
    # infra flakiness. Revisit last so the rest of the benchmark isn't
    # blocked on it; investigate afterward if it hangs again.
    ("11_student_management", (
        "A student management system for a school with admin and teacher "
        "roles. Admins register students (name, grade, enrollment date) "
        "and manage classes (name, teacher, subject). Teachers record "
        "attendance per class session (present/absent per student) and "
        "enter grades for assignments (assignment name, score, max "
        "score). Admins view a student's overall attendance rate and "
        "grade summary across classes."
    )),
]

assert len(APPS) == 25, f"expected 25 apps, got {len(APPS)}"

# ---------------------------------------------------------------------------
# Existing failure taxonomy (Exp077-100) -- anything else classifies as
# NEW_UNCLASSIFIED, per this benchmark's own explicit rule.
# ---------------------------------------------------------------------------
KNOWN_TAXONOMY = {
    "JourneyCRUDFailure", "AttributeError", "ConfigAttributeError",
    "PydanticSerializationError", "SQLAlchemyError", "ImportError",
    "ResponseValidationError", "ModelFieldMismatchError", "TypeError",
    "NotNullViolationError", "TimestampNotNullError",
}


def _classify_failure(dominant_errors: list[str]) -> str | None:
    """Extract the [BracketTag] from the final dominant_errors list and
    classify against the known taxonomy. None if the run succeeded."""
    for e in dominant_errors:
        if e.startswith("["):
            tag = e.split("]")[0][1:]
            return tag if tag in KNOWN_TAXONOMY else f"NEW_UNCLASSIFIED ({tag})"
    if dominant_errors:
        return "NEW_UNCLASSIFIED (untagged: " + dominant_errors[0][:60] + ")"
    return None


def _load_gen_log() -> list[dict]:
    if not GEN_LOG_PATH.exists():
        return []
    out = []
    for line in GEN_LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _run_one(app_key: str, idea: str, provider: str) -> dict:
    t0 = time.time()
    job_id = uuid.uuid4().hex
    gen_log_before = len(_load_gen_log())

    try:
        result = generate_project_v15(idea=idea, provider=provider, deploy=False, job_id=job_id)
    except Exception as exc:
        return {
            "app": app_key, "crashed": True, "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-2000:],
            "elapsed_s": round(time.time() - t0, 1),
        }

    dims = result.get("dimensions", [])
    build_dim   = _dim_lookup(dims, _DIM_MAP["build"])
    runtime_dim = _dim_lookup(dims, _DIM_MAP["runtime"])
    crud_dim    = _dim_lookup(dims, _DIM_MAP["crud"])
    browser_dim = _dim_lookup(dims, _DIM_MAP["browser"])
    api_dim     = next((d for d in dims if d["name"] == "API Functionality"), None)

    # New generation_log.jsonl entries this call produced (one per
    # fix-loop attempt); the LAST one reflects the final state.
    gen_log_after = _load_gen_log()
    new_entries = gen_log_after[gen_log_before:]
    final_entry = new_entries[-1] if new_entries else {}

    succeeded = bool(final_entry.get("succeeded", result.get("forge_score", 0) >= 80))
    fix_attempts = result.get("fix_attempts", 0)
    first_pass_success = succeeded and fix_attempts == 0

    return {
        "app": app_key,
        "crashed": False,
        "project_name": result.get("project_name"),
        "forge_score": result.get("forge_score", 0.0),
        "grade": result.get("grade", "F"),
        "succeeded": succeeded,
        "first_pass_success": first_pass_success,
        "repair_success": succeeded and fix_attempts > 0,
        "build_ok": bool(build_dim and build_dim.get("passed")) if build_dim and not build_dim.get("na") else None,
        "runtime_ok": bool(runtime_dim and runtime_dim.get("passed")) if runtime_dim and not runtime_dim.get("na") else None,
        "crud_ok": bool(crud_dim and crud_dim.get("passed")) if crud_dim and not crud_dim.get("na") else None,
        "browser_ok": bool(browser_dim and browser_dim.get("passed")) if browser_dim and not browser_dim.get("na") else None,
        "endpoint_inventory": (api_dim or {}).get("details", "n/a"),
        "deployed": result.get("deployed", False),
        "fix_attempts": fix_attempts,
        "elapsed_s": round(time.time() - t0, 1),
        "total_tokens": result.get("total_tokens", 0),
        "estimated_cost": result.get("estimated_cost", 0.0),
        "score_history": result.get("score_history", []),
        "final_dominant_errors": final_entry.get("dominant_errors", []),
        "failure_class": None if succeeded else _classify_failure(final_entry.get("dominant_errors", [])),
        "dimensions": dims,
    }


def _load_results() -> list[dict]:
    if RESULTS_PATH.exists():
        try:
            return json.loads(RESULTS_PATH.read_text(encoding="utf-8")).get("results", [])
        except Exception:
            return []
    return []


def _save_results(results: list[dict]):
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps({
            "benchmark": "ForgeBench v1.0",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "total_apps": len(APPS),
            "results": results,
        }, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _run_one_isolated(app_key: str, idea: str, provider: str, timeout_s: int) -> dict:
    """
    Run one app's generation in its own subprocess with a hard timeout.

    Added mid-run after 4 confirmed hangs (tasklist-verified flat memory
    across consecutive checks, not assumed) on 2 different apps within
    ~90 minutes -- an intermittent, no-timeout-guarded blocking call
    somewhere in the pipeline, not specific to one idea. Isolating each
    app in its own process lets a hang be killed (including any child
    uvicorn/npm descendants, via `taskkill /T`) without losing the rest
    of the benchmark run or requiring manual intervention.
    """
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(prefix="forgebench_worker_") as tmp:
        input_path = Path(tmp) / "input.json"
        output_path = Path(tmp) / "output.json"
        input_path.write_text(
            json.dumps({"app": app_key, "idea": idea, "provider": provider}),
            encoding="utf-8",
        )
        worker = _BACKEND_ROOT / "scripts" / "_forgebench_worker.py"
        t0 = time.time()
        proc = subprocess.Popen(
            [sys.executable, str(worker), str(input_path), str(output_path)],
            cwd=str(_BACKEND_ROOT),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            print(f"  [{app_key}] TIMEOUT after {timeout_s}s -- killing process tree")
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                capture_output=True)
            else:
                proc.kill()
            return {
                "app": app_key, "crashed": True,
                "error": f"TIMEOUT after {timeout_s}s (hung subprocess, killed)",
                "elapsed_s": round(time.time() - t0, 1),
            }

        if output_path.exists():
            try:
                return json.loads(output_path.read_text(encoding="utf-8"))
            except Exception as e:
                return {"app": app_key, "crashed": True,
                        "error": f"worker output unreadable: {e}",
                        "elapsed_s": round(time.time() - t0, 1)}
        return {
            "app": app_key, "crashed": True,
            "error": f"worker exited (code {proc.returncode}) with no output",
            "elapsed_s": round(time.time() - t0, 1),
        }


def main():
    parser = argparse.ArgumentParser(description="ForgeBench v1.0 -- 25-app reliability benchmark")
    parser.add_argument("--provider", default="cerebras")
    parser.add_argument("--start-at", type=int, default=1, help="1-indexed app number to start/resume at")
    parser.add_argument("--timeout", type=int, default=900, help="per-app hard timeout in seconds (default 900 = 15 min)")
    parser.add_argument("--limit", type=int, default=0,
                         help="run at most N remaining apps then exit (0 = all remaining). "
                              "Added after the outer wrapper process was externally killed "
                              "several times mid-run for reasons outside this script's control -- "
                              "running one app per invocation bounds the damage from any single "
                              "kill to at most one app's progress.")
    args = parser.parse_args()

    _acquire_lock("forgebench-v1")
    try:
        results = _load_results()
        done_keys = {r["app"] for r in results}
        ran = 0

        for i, (app_key, idea) in enumerate(APPS, start=1):
            if i < args.start_at or app_key in done_keys:
                continue
            if args.limit and ran >= args.limit:
                break
            print(f"\n{'='*70}\n  FORGEBENCH v1.0 -- App {i}/25: {app_key}\n{'='*70}")
            r = _run_one_isolated(app_key, idea, args.provider, args.timeout)
            results.append(r)
            _save_results(results)
            ran += 1

            if r.get("crashed"):
                print(f"  [{app_key}] CRASHED: {r.get('error')}")
            else:
                print(f"  [{app_key}] score={r['forge_score']:.1f} ({r['grade']}) "
                      f"succeeded={r['succeeded']} first_pass={r['first_pass_success']} "
                      f"fix_attempts={r['fix_attempts']} cost=${r['estimated_cost']:.4f} "
                      f"tokens={r['total_tokens']} time={r['elapsed_s']}s "
                      f"failure_class={r['failure_class']}")

        print(f"\n{'='*70}\n  FORGEBENCH v1.0 -- {len(results)}/25 apps done ({ran} run this invocation)\n{'='*70}")
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
