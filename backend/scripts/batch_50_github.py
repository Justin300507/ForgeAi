"""
One-off batch: generate 50 new apps locally against the live V15 pipeline,
each pushed to its own new GitHub repo, targeting Forge Score >= 80.

Deliberately does NOT deploy to Vercel/Neon for all 50 -- 50 fresh Vercel
projects + 50 fresh Neon databases risks hitting an account quota
partway through the batch (unknown headroom), and full hosting wasn't
explicitly asked for. GitHub push is called directly for every app that
clears the score bar, independent of the pipeline's own >=95 deploy gate.

FORGE_DEPLOY_THRESHOLD is overridden here, in THIS PROCESS ONLY, before
any app.* import -- app.core.context.GenerationContext reads it once at
class-definition time. This never touches Railway's production env var.

Usage: python scripts/batch_50_github.py
Resume a partial run: python scripts/batch_50_github.py --resume
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

os.environ["FORGE_DEPLOY_THRESHOLD"] = "999"  # never let the pipeline's own >=95 gate auto-deploy; we push manually below
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.v15_orchestrator import generate_project_v15  # noqa: E402
from app.services.v14_orchestrator import _push_to_github  # noqa: E402

RESULTS_PATH = Path(__file__).parent / "batch_50_results.json"
PUSH_SCORE_THRESHOLD = 80.0

IDEAS = [
    "A recipe manager with ingredients, categories, and meal planning",
    "A book club app with reading lists, discussions, and ratings",
    "A freelancer invoice tracker with clients, invoices, and payment status",
    "A fitness workout logger with exercises, sets, and progress charts",
    "A job application tracker with companies, stages, and interview notes",
    "A plant care reminder app with watering schedules and species info",
    "A movie watchlist app with ratings, genres, and watch status",
    "A budget travel planner with destinations, itineraries, and expenses",
    "A pet care tracker with vet visits, vaccinations, and feeding schedules",
    "A study flashcard app with decks, cards, and spaced repetition tracking",
    "A local event finder with venues, categories, and RSVP tracking",
    "A grocery list app with shared lists, categories, and quantities",
    "A car maintenance log with service records, mileage, and reminders",
    "A volunteer hour tracker with organizations, events, and hour logs",
    "A wine cellar inventory with bottles, vintages, and tasting notes",
    "A subscription tracker with services, billing cycles, and cost totals",
    "A garden planner with plots, plants, and harvest schedules",
    "A meal prep planner with recipes, weekly plans, and shopping lists",
    "A rental property manager with units, tenants, and maintenance requests",
    "A conference talk scheduler with sessions, speakers, and rooms",
    "A coworking space booking app with desks, rooms, and reservations",
    "A tutoring marketplace with tutors, subjects, and session bookings",
    "A donation tracker for a nonprofit with donors, campaigns, and receipts",
    "A recipe box with family recipes, tags, and cooking notes",
    "A running club with members, races, and personal bests",
    "A board game collection tracker with games, players, and match history",
    "A freelance time tracker with projects, tasks, and billable hours",
    "A library management system with books, members, and checkouts",
    "A parking spot finder with locations, availability, and reservations",
    "A home inventory app with rooms, items, and estimated value",
    "A wedding planning app with vendors, budget, and guest list",
    "A daily journal app with entries, moods, and tags",
    "A team standup tracker with updates, blockers, and team members",
    "A car pooling app for coworkers with rides, drivers, and passengers",
    "A community lending library for tools with items, borrowers, and due dates",
    "A menu planning app for a small restaurant with dishes, ingredients, and pricing",
    "A skill-sharing app with offered skills, requests, and meetups",
    "A habit-based savings app with goals, deposits, and progress",
    "A neighborhood watch app with incidents, reports, and members",
    "A freelance portfolio manager with projects, clients, and testimonials",
    "A classroom attendance tracker with students, classes, and sessions",
    "A charity auction app with items, bids, and winners",
    "A co-op grocery buying club with orders, members, and pickup schedules",
    "A local farmers market vendor directory with vendors, products, and schedules",
    "A personal reading challenge tracker with books, genres, and progress",
    "A car rental booking system with vehicles, bookings, and pricing",
    "A meetup group organizer with events, RSVPs, and venues",
    "A freelance contract tracker with contracts, clients, and deadlines",
    "A recipe rating and review app with recipes, reviews, and ratings",
    "A simple CRM for real estate agents with listings, clients, and showings",
]

assert len(IDEAS) == 50


def _load_results() -> list[dict]:
    if RESULTS_PATH.exists():
        try:
            return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_results(results: list[dict]) -> None:
    RESULTS_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")


def main() -> None:
    resume = "--resume" in sys.argv
    results = _load_results() if resume else []
    done_ideas = {r["idea"] for r in results}

    print(f"# Batch 50: {len(IDEAS) - len(done_ideas)} remaining ({len(done_ideas)} already done)")

    for i, idea in enumerate(IDEAS, 1):
        if idea in done_ideas:
            continue

        print(f"\n{'=' * 70}\n[{i}/50] {idea}\n{'=' * 70}")
        t0 = time.time()
        entry: dict = {"idea": idea, "index": i}
        try:
            result = generate_project_v15(
                idea=idea,
                provider="auto",
                deploy=False,  # we push to GitHub ourselves below, regardless of the pipeline's own >=95 gate
                job_id=uuid.uuid4().hex,
            )
            score = (result.get("forge_score") or {})
            score_val = score.get("score", 0) if isinstance(score, dict) else (score or 0)
            project_name = result.get("project_name") or result.get("project", {}).get("name")
            project_path = result.get("project_path") or (f"generated_projects/{project_name}" if project_name else None)

            entry.update({
                "score": score_val,
                "project_name": project_name,
                "elapsed_s": round(time.time() - t0, 1),
            })

            if score_val >= PUSH_SCORE_THRESHOLD and project_path and project_name:
                gh = _push_to_github(str(project_path), project_name, idea)
                entry["github"] = gh
                entry["pushed"] = bool(gh.get("success"))
                print(f"  -> score {score_val:.1f}, GitHub push: {'OK ' + str(gh.get('repo_url')) if gh.get('success') else 'FAILED ' + str(gh.get('error'))}")
            else:
                entry["pushed"] = False
                entry["skip_reason"] = f"score {score_val:.1f} < {PUSH_SCORE_THRESHOLD}"
                print(f"  -> score {score_val:.1f} — below push threshold, not pushed")

        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            entry["pushed"] = False
            print(f"  -> FAILED: {entry['error']}")

        results.append(entry)
        _save_results(results)

    pushed = [r for r in results if r.get("pushed")]
    scored = [r for r in results if isinstance(r.get("score"), (int, float))]
    above_80 = [r for r in scored if r["score"] >= 80]
    print(f"\n{'=' * 70}\nBATCH COMPLETE: {len(results)}/50 attempted")
    print(f"  Pushed to GitHub: {len(pushed)}")
    print(f"  Scored >= 80:     {len(above_80)}/{len(scored)}")
    if scored:
        print(f"  Average score:    {sum(r['score'] for r in scored) / len(scored):.1f}")
    print(f"  Full results: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
