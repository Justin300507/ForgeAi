"""
ForgeBench-Hard -- a batch of new, never-before-generated applications with
deliberately heavier prompts than the existing 25-app ForgeBench v1.0 suite:
multi-step workflows, computed/derived state (dependency recalculation,
proxy-bid auto-increment, tiered commission math, double-entry invariants,
bracket advancement), and stricter business rules, not just more CRUD
entities. Every idea below was checked against generated_projects/ and the
existing 25/3-app suites for domain overlap before being written.

Reuses forgebench_v1.py's isolated-subprocess runner as-is (same
hang-safety via `taskkill /T`, same incremental-write-per-app, same shared
canary/forgebench lock file via _acquire_lock) -- this file only supplies a
different APPS list; zero changes to any existing ForgeAI module.

Usage:
    python scripts/forgebench_hard.py                 # run all, resume if partial results exist
    python scripts/forgebench_hard.py --start-at 5     # resume from app #5 (1-indexed)
    python scripts/forgebench_hard.py --provider auto
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

from forgebench_v1 import _run_one_isolated, _acquire_lock, _release_lock, _classify_failure

RESULTS_PATH = _BACKEND_ROOT / "benchmark_results" / "forgebench_hard_results.json"

APPS: list[tuple[str, str]] = [
    ("h01_saas_usage_billing", (
        "A multi-tenant SaaS platform with usage-metered billing. "
        "Organizations sign up and choose a subscription tier (each tier "
        "has a monthly price and an included API-call quota). The "
        "platform records metered API-call usage per organization per "
        "day. Usage beyond an organization's monthly quota is billed as "
        "overage at a fixed per-call rate. At the end of each month, "
        "generate an invoice per organization summing the tier price "
        "plus any overage charges. Roles: org owner (manages billing, "
        "views invoices) and org member (uses the product, cannot view "
        "billing). Owners see current-month usage against quota in "
        "real time."
    )),
    ("h02_collectibles_escrow_marketplace", (
        "A marketplace for authenticated collectibles with escrow "
        "payments. Sellers list items (name, description, asking price, "
        "category) which start in a pending-authentication state. A "
        "verifier role inspects each listing and marks it authentic or "
        "fake before it can be purchased. Buyers purchase authenticated "
        "listings; payment is held in escrow, not released to the "
        "seller immediately. The verifier confirms the item was shipped "
        "as described, which releases escrow to the seller -- or the "
        "buyer can open a dispute, which an admin resolves by releasing "
        "the escrow to the seller or refunding the buyer. Track each "
        "listing's full state (pending_auth, authentic, fake, purchased, "
        "escrow_held, released, refunded, disputed)."
    )),
    ("h03_ats_recruiting_pipeline", (
        "An applicant tracking system with recruiter and interviewer "
        "roles. Recruiters post job requisitions (title, department, "
        "description). Candidates apply to a requisition with a resume "
        "text and cover letter. Each application moves through a fixed "
        "pipeline of stages in order: applied, phone_screen, "
        "technical_interview, onsite, offer, and finally hired or "
        "rejected -- rejection can happen from any stage but forward "
        "progress must follow the stage order. Interviewers are "
        "assigned to a stage for a candidate and submit a scorecard "
        "(1-5 rating plus notes) for that stage. Recruiters view a "
        "funnel showing how many candidates are currently at each stage "
        "per requisition."
    )),
    ("h04_freight_load_board", (
        "A freight brokerage load-matching platform with shipper and "
        "carrier roles. Shippers post loads (origin, destination, "
        "pickup date, weight in lbs, rate offered). Carriers browse "
        "available loads and submit bids with a counter-rate. Shippers "
        "review bids on their load and accept exactly one, which "
        "assigns that carrier and marks the load 'booked' (all other "
        "bids on it are automatically rejected). Carriers update a "
        "booked load's status through transit: booked -> picked_up -> "
        "in_transit -> delivered. Shippers see, per carrier they've "
        "worked with, total loads completed and on-time-delivery rate "
        "(delivered by the pickup date + an implied transit window)."
    )),
    ("h05_commission_payout_engine", (
        "A sales commission tracking engine with rep and manager roles. "
        "Reps log closed deals (deal value, product category, close "
        "date). Each product category has tiered commission rates: the "
        "rate a deal earns increases once the rep's total closed value "
        "in that category for the current pay period crosses defined "
        "thresholds (e.g. a higher rate applies only to the portion of "
        "value above each threshold). Commissions accrue per rep per pay "
        "period. Managers review a pay period's accrued commissions and "
        "approve a payout run, which locks that period's totals as paid. "
        "If a deal is later marked refunded, its commission is clawed "
        "back as a negative adjustment against the rep's next open pay "
        "period."
    )),
    ("h06_gantt_dependency_scheduler", (
        "A project scheduling tool with manager and team-member roles. "
        "Projects contain tasks, each with a start date, a duration in "
        "days, an assignee, and an optional list of prerequisite tasks "
        "that must finish before it can start. A task's earliest "
        "possible start date is computed from the latest finish date "
        "among its prerequisites (a task with no prerequisites can start "
        "any time). Editing a task's duration or start date must "
        "automatically recompute and shift the start date of every task "
        "that depends on it, directly or transitively through a chain of "
        "prerequisites. Managers mark tasks complete; team members view "
        "only their own assigned tasks and current computed start dates."
    )),
    ("h07_double_entry_ledger", (
        "A bookkeeping ledger for a small business enforcing double-"
        "entry accounting, with bookkeeper and owner roles. Bookkeepers "
        "define accounts, each with a category (asset, liability, "
        "equity, revenue, or expense). Every transaction is recorded as "
        "a set of line items, each a debit or credit against one "
        "account, and the transaction can only be saved when its total "
        "debits equal its total credits. Each account shows a running "
        "balance computed from all its line items. Owners can view a "
        "trial balance report listing every account's current balance, "
        "with the sum of all debit balances shown against the sum of all "
        "credit balances so they can be confirmed equal."
    )),
    ("h08_social_feed_platform", (
        "A social networking core with a single user role. Users create "
        "posts (text content, optional image caption) and follow other "
        "users. Each user's home feed shows posts only from users they "
        "follow, ranked by a score combining recency and like count (a "
        "newer post with fewer likes can still outrank an older post "
        "with more, up to a defined recency decay). Users can like and "
        "unlike posts. A user receives a notification when someone "
        "follows them or likes their post, and sees an unread-"
        "notification count that clears when they open the notification "
        "list."
    )),
    ("h09_patent_portfolio_tracker", (
        "An IP portfolio management tool for a law firm, with attorney "
        "and paralegal roles. Attorneys manage patent and trademark "
        "applications per client, each with a filing date, jurisdiction, "
        "and status (filed, pending, granted, or abandoned). For granted "
        "applications, compute the next renewal deadline from the grant "
        "date plus a jurisdiction-specific renewal interval, and flag any "
        "application with a renewal deadline within 60 days. Attorneys "
        "record licensing agreements against a granted patent (licensee "
        "name, royalty rate) and log each quarterly royalty payment "
        "received under that agreement. Paralegals can view all "
        "deadlines and log payments but cannot create new agreements."
    )),
    ("h10_courier_dispatch", (
        "An on-demand courier dispatch platform with customer, courier, "
        "and dispatcher roles. Customers request a delivery with a "
        "pickup address, dropoff address, and package size (small/"
        "medium/large); the system computes a price from an estimated "
        "distance and the package size. Any available courier can "
        "accept an unassigned request, which immediately locks it from "
        "being accepted by anyone else and marks the courier busy. The "
        "assigned courier updates delivery status through assigned -> "
        "picked_up -> delivered, which frees them to accept another "
        "request. Customers rate the delivery (1-5) after it's "
        "delivered. Dispatchers see a live list of unassigned requests "
        "and every courier's current status (available or busy)."
    )),
    ("h11_proxy_auction_platform", (
        "An online auction platform with seller and bidder roles. "
        "Sellers list items with a starting price and an auction end "
        "time. Bidders place a maximum proxy bid; the system "
        "automatically sets the item's current visible price to just "
        "above the second-highest proxy bid (capped at the "
        "highest bidder's own maximum), so bidders don't have to keep "
        "manually re-bidding as others bid. When the auction's end time "
        "passes, it closes automatically and the highest proxy bidder "
        "wins at the computed final price. Sellers mark won auctions as "
        "paid and then shipped. Bidders can view all auctions they're "
        "currently winning or have been outbid on."
    )),
    ("h12_tournament_bracket_manager", (
        "An esports tournament organizer platform with organizer and "
        "team-captain roles. Organizers create a tournament and open "
        "team registration; team captains register their team. Once "
        "registration closes, the organizer generates a double-"
        "elimination bracket (a winners bracket and a losers bracket) "
        "from the registered teams. Organizers record match results; a "
        "match loss in the winners bracket drops a team into the losers "
        "bracket, while a loss in the losers bracket eliminates the team "
        "entirely. Winners bracket wins advance a team forward through "
        "that bracket. Organizers and captains can both view the current "
        "state of both brackets and each match's scheduled time."
    )),
    ("h13_clinical_trial_enrollment", (
        "A clinical trial enrollment platform with coordinator and "
        "physician roles. Coordinators define trial protocols, each "
        "with eligibility criteria: a minimum and maximum patient age, a "
        "list of required conditions, and a list of excluded conditions. "
        "Coordinators screen a patient against a protocol by entering "
        "the patient's age and conditions; the system determines and "
        "records whether the patient is eligible based on the criteria. "
        "Eligible patients can be enrolled and are scheduled for a fixed "
        "sequence of study visits. Physicians log adverse events for an "
        "enrolled patient with a severity (mild, moderate, or severe); "
        "any severe event is automatically flagged for coordinator "
        "review."
    )),
    ("h14_paper_trading_simulator", (
        "A stock paper-trading simulator with a single user role. Each "
        "user starts with a virtual cash balance. Users place buy or "
        "sell orders for a stock ticker at a given simulated price; a "
        "buy order is rejected if it would exceed the user's current "
        "cash balance, and a sell order is rejected if it would exceed "
        "the shares the user currently holds. Executed orders "
        "immediately update the user's cash balance and share holdings. "
        "The portfolio page shows current holdings per ticker with "
        "average cost basis and unrealized gain or loss versus the "
        "latest simulated price. A transaction history lists every "
        "executed order with its price, quantity, and timestamp."
    )),
    ("h15_warranty_rma_processing", (
        "A product warranty and RMA (return merchandise authorization) "
        "system for a retailer, with customer and support-agent roles. "
        "Customers look up their past purchase by order number and file "
        "a warranty claim describing the defect. Agents review claims, "
        "which move through a fixed workflow: submitted -> under_review "
        "-> approved or denied -> (if approved) replacement_shipped or "
        "refund_issued. Agents attach a short resolution note at each "
        "workflow transition. Managers view total claims and average "
        "time-to-resolution grouped by defect category, and can "
        "reassign a claim to a different agent."
    )),
]

assert len({k for k, _ in APPS}) == len(APPS), "duplicate app keys in ForgeBench-Hard APPS"


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
            "benchmark": "ForgeBench-Hard",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "total_apps": len(APPS),
            "results": results,
        }, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description="ForgeBench-Hard -- heavy-prompt, never-before-run apps")
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--start-at", type=int, default=1, help="1-indexed app number to start/resume at")
    parser.add_argument("--timeout", type=int, default=1500, help="per-app hard timeout in seconds")
    parser.add_argument("--limit", type=int, default=0, help="run at most N remaining apps then exit (0 = all)")
    args = parser.parse_args()

    _acquire_lock("forgebench-hard")
    try:
        results = _load_results()
        done_keys = {r["app"] for r in results}
        ran = 0

        for i, (app_key, idea) in enumerate(APPS, start=1):
            if i < args.start_at or app_key in done_keys:
                continue
            if args.limit and ran >= args.limit:
                break
            print(f"\n{'='*70}\n  FORGEBENCH-HARD -- App {i}/{len(APPS)}: {app_key}\n{'='*70}")
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

        print(f"\n{'='*70}\n  FORGEBENCH-HARD -- {len(results)}/{len(APPS)} apps done ({ran} run this invocation)\n{'='*70}")
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
