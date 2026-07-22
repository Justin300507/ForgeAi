"""
ForgeBench-Basic -- plan correction after ForgeBench-Hard/Hard2 showed the
live pipeline is unreliable on heavy, multi-workflow, computed-state prompts
(mostly D/F grades, several crashes: tournament brackets, auctions,
commission engines, etc.). Simple 2-4 entity CRUD apps (the original golden
corpus style) reliably score 90+. This batch drops back to that proven
complexity level -- never-before-run apps, but no exotic business logic,
state machines, or multi-role workflows beyond a single owner/admin role.

Reuses forgebench_v1.py's isolated-subprocess runner as-is.

Usage:
    python scripts/forgebench_basic.py
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

from forgebench_v1 import _run_one_isolated, _acquire_lock, _release_lock

RESULTS_PATH = _BACKEND_ROOT / "benchmark_results" / "forgebench_basic_results.json"

APPS: list[tuple[str, str]] = [
    ("j01_reading_list_tracker", (
        "A personal reading list tracker with user login. Users add books "
        "(title, author, genre, status: want_to_read/reading/finished). "
        "Users rate finished books 1-5 stars and add a short review. "
        "Search books by title or author, filter by status or genre."
    )),
    ("j02_address_book", (
        "A personal address book with user login. Users add contacts "
        "(name, phone, email, address, category: family/friend/work). "
        "Search contacts by name, filter by category. Users can edit and "
        "delete their own contacts, and mark a contact as a favorite."
    )),
    ("j03_medication_reminder", (
        "A personal medication tracker with user login. Users add "
        "medications (name, dosage, frequency, start date). Log each dose "
        "taken with a timestamp. View medication history and which doses "
        "were taken today. Users can mark a medication as discontinued."
    )),
    ("j04_pet_vaccination_log", (
        "A pet vaccination record tracker with user login. Users register "
        "pets (name, species, breed, birth date). Log vaccinations per pet "
        "(vaccine name, date given, next due date). View upcoming and "
        "overdue vaccinations across all their pets."
    )),
    ("j05_household_chore_rotation", (
        "A household chore tracker with user login. Users create chores "
        "(name, description, frequency: daily/weekly/monthly) and assign "
        "them to household members. Mark a chore complete for the current "
        "period, which records who did it and when. View completion "
        "history per chore and per member."
    )),
    ("j06_suggestion_box", (
        "A workplace suggestion box with employee and manager roles. "
        "Employees submit suggestions (title, description, category). "
        "Managers review suggestions and update status (new/under_review/"
        "approved/declined) with a response note. Employees view the "
        "status of their own submitted suggestions."
    )),
    ("j07_classroom_attendance", (
        "A single-classroom attendance tracker with teacher login. "
        "Teachers add students (name, student ID). For each class date, "
        "mark each student present or absent. View a student's attendance "
        "history and overall attendance rate."
    )),
    ("j08_potluck_signup", (
        "A potluck event signup sheet with organizer and guest roles. "
        "Organizers create an event (name, date, location) with a list of "
        "needed dish categories (appetizer, main, dessert, drink). Guests "
        "sign up to bring a dish under a category, entering the dish name. "
        "Organizers view who's bringing what per category."
    )),
    ("j09_book_collection_catalog", (
        "A personal book collection cataloger with user login. Users add "
        "owned books (title, author, ISBN, shelf location, condition: "
        "new/good/worn). Mark a book as loaned out to someone (borrower "
        "name, loan date) and mark it returned. Search the collection by "
        "title, author, or shelf location."
    )),
    ("j10_office_lost_and_found", (
        "An office lost-and-found log with employee login. Anyone can "
        "post a found item (description, location found, date, photo "
        "description text). Anyone can post a lost item they're looking "
        "for (description, date lost). Staff mark a found item as "
        "claimed, recording who claimed it."
    )),
    ("j11_party_rsvp", (
        "A party RSVP tracker with host and guest roles. Hosts create an "
        "event (name, date, location, max guests). Guests RSVP as "
        "attending, maybe, or not attending, and can note a plus-one. "
        "Hosts view a summary of RSVP counts and the full guest list."
    )),
    ("j12_reading_challenge_tracker", (
        "A yearly reading challenge tracker with user login. Users set a "
        "yearly goal (number of books to read). Log each book finished "
        "(title, author, date finished, page count). View progress toward "
        "the yearly goal and total pages read this year."
    )),
    ("j13_simple_guestbook", (
        "A simple website guestbook with visitor and admin roles. "
        "Visitors leave a guestbook entry (name, message). Admins can "
        "delete inappropriate entries. Visitors browse all entries sorted "
        "newest first, with pagination."
    )),
    ("j14_office_equipment_checkout", (
        "A small-office equipment checkout log with employee login. Staff "
        "register equipment items (name, asset tag, category). Employees "
        "check out an available item (recording who and when), and check "
        "it back in. View which items are currently checked out and by "
        "whom."
    )),
    ("j15_loyalty_punch_card", (
        "A simple customer loyalty punch-card system with cashier and "
        "customer roles. Cashiers register customers and add a punch to a "
        "customer's card after a purchase. Once a card reaches 10 punches, "
        "it's marked eligible for a free reward; cashiers redeem it, which "
        "resets the punch count to zero. Customers view their own current "
        "punch count."
    )),
]

assert len({k for k, _ in APPS}) == len(APPS), "duplicate app keys in ForgeBench-Basic APPS"


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
            "benchmark": "ForgeBench-Basic",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "total_apps": len(APPS),
            "results": results,
        }, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description="ForgeBench-Basic -- simple, reliable CRUD apps")
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--start-at", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()

    _acquire_lock("forgebench-basic")
    try:
        results = _load_results()
        done_keys = {r["app"] for r in results}

        for i, (app_key, idea) in enumerate(APPS, start=1):
            if i < args.start_at or app_key in done_keys:
                continue
            print(f"\n{'='*70}\n  FORGEBENCH-BASIC -- App {i}/{len(APPS)}: {app_key}\n{'='*70}")
            r = _run_one_isolated(app_key, idea, args.provider, args.timeout)
            results.append(r)
            _save_results(results)

            if r.get("crashed"):
                print(f"  [{app_key}] CRASHED: {r.get('error')}")
            else:
                print(f"  [{app_key}] score={r['forge_score']:.1f} ({r['grade']}) "
                      f"succeeded={r['succeeded']} fix_attempts={r['fix_attempts']} "
                      f"cost=${r['estimated_cost']:.4f} time={r['elapsed_s']}s")

        print(f"\n{'='*70}\n  FORGEBENCH-BASIC -- {len(results)}/{len(APPS)} apps done\n{'='*70}")
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
