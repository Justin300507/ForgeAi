"""
ForgeBench-Hard round 2 -- 15 more never-before-generated, heavy-prompt
apps, distinct from forgebench_v1.py's 25, forgebench_hard.py's 15, and
checked against generated_projects/ for domain overlap. Part of an ongoing
generate -> repair -> generate loop toward a larger cumulative app count.

Reuses forgebench_v1.py's isolated-subprocess runner as-is; this file only
supplies a different APPS list.

Usage:
    python scripts/forgebench_hard2.py
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

RESULTS_PATH = _BACKEND_ROOT / "benchmark_results" / "forgebench_hard2_results.json"

APPS: list[tuple[str, str]] = [
    ("i01_coldchain_shipment_monitoring", (
        "A cold-chain shipment monitoring system with logistics-coordinator "
        "and driver roles. Coordinators create shipments (product type, "
        "origin, destination, required temperature range in Celsius, "
        "departure time). Drivers log periodic temperature readings for "
        "their assigned shipment during transit. Any reading outside the "
        "shipment's required range is automatically flagged as a "
        "compliance breach and the shipment's status changes to "
        "'temperature_excursion'. Coordinators review breached shipments "
        "and record a resolution note. Coordinators can generate a "
        "compliance summary per shipment showing all readings and any "
        "breaches."
    )),
    ("i02_financial_aid_disbursement", (
        "A university financial aid disbursement system with aid-officer "
        "and student roles. Aid officers define aid programs, each with "
        "an eligibility rule (minimum and maximum household income) and "
        "an award amount. Students submit an aid application with their "
        "household income; the system determines and records which "
        "programs they're eligible for. Aid officers approve an "
        "application for a specific eligible program, which schedules "
        "the award to be disbursed across a fixed number of terms (e.g. "
        "split evenly per term of the academic year). Aid officers mark "
        "each term's disbursement as paid; students view their award "
        "schedule and which terms have been paid."
    )),
    ("i03_union_grievance_tracker", (
        "A workplace union grievance tracking system with steward and "
        "member roles. Members file a grievance (description, incident "
        "date, category). Each grievance moves through a fixed "
        "resolution process: filed -> step1_review -> step2_review -> "
        "arbitration -> resolved, only advancing forward one step at a "
        "time (never skipping a step) and any step can instead be marked "
        "'settled' to close it early. Stewards log a note at each step "
        "transition explaining the outcome. Members view the current "
        "status and full step history of their own grievances; stewards "
        "see all open grievances grouped by current step."
    )),
    ("i04_franchise_royalty_reconciliation", (
        "A franchise royalty reconciliation system with franchisor and "
        "franchisee roles. The franchisor defines a royalty rate "
        "(percentage of monthly sales). Franchisees submit their monthly "
        "sales report (total sales amount) for each of their locations. "
        "The system computes the royalty owed as sales times the rate. "
        "Franchisees mark a month's royalty payment as paid; the "
        "franchisor sees a reconciliation view per location showing "
        "reported sales, royalty owed, and payment status, plus any "
        "location that hasn't submitted a report for the current month."
    )),
    ("i05_talent_agency_booking", (
        "A talent agency booking platform with agent and talent roles. "
        "Agents manage talent profiles (stage name, category, standard "
        "rate). Agents create gig bookings for a talent (client name, "
        "event date, agreed fee) and record the agency's commission rate "
        "for that booking. Once a booking is marked completed, the "
        "system computes the talent's payout (fee minus commission) and "
        "the agency's commission earned. Talent view their own upcoming "
        "and completed bookings with payout amounts; agents view total "
        "commission earned across all talent for a given month."
    )),
    ("i06_vaccine_inventory_administration", (
        "A vaccine inventory and administration tracker for a clinic, "
        "with pharmacist and nurse roles. Pharmacists receive vaccine "
        "lots (vaccine name, lot number, quantity, expiration date) into "
        "inventory. Nurses administer a dose to a patient by selecting a "
        "lot, which decrements that lot's remaining quantity (blocked if "
        "the lot is expired or has zero remaining) and records the "
        "patient, lot number, and administration date. Nurses can log an "
        "adverse reaction report against an administered dose (severity, "
        "description). Pharmacists see current inventory per lot with "
        "days until expiration, and a list of any logged adverse "
        "reactions."
    )),
    ("i07_hoa_assessment_violation_tracker", (
        "A condominium HOA management system with board-member and "
        "resident roles. Board members define units and bill a periodic "
        "assessment fee to each unit's resident. Residents view their "
        "assessment balance and mark payments made, which reduces their "
        "balance. Board members can also log a violation against a unit "
        "(category, description, fine amount); residents can appeal a "
        "violation with a written explanation, and board members approve "
        "or deny the appeal -- a denied or unappealed violation's fine "
        "adds to the unit's balance. Board members see all units with "
        "outstanding balances above zero."
    )),
    ("i08_lab_equipment_calibration_compliance", (
        "A laboratory equipment calibration compliance tracker with "
        "lab-manager and technician roles. Lab managers register "
        "instruments (name, model, calibration interval in days). "
        "Technicians log a completed calibration for an instrument "
        "(date performed, certificate number, notes), which computes and "
        "stores that instrument's next-due date as the calibration date "
        "plus its interval. Instruments whose next-due date has passed "
        "are flagged 'overdue' on the lab manager's dashboard, shown "
        "alongside instruments due within the next 14 days. Technicians "
        "can view an instrument's full calibration history."
    )),
    ("i09_debt_collection_case_workflow", (
        "A debt collection agency case management system with agent and "
        "supervisor roles. Supervisors load accounts placed for "
        "collection (debtor name, original creditor, amount owed). "
        "Agents work assigned accounts: log contact attempts (date, "
        "outcome), set up a payment plan (installment amount, number of "
        "installments), and record payments received against the "
        "account, which reduce its remaining balance. An account is "
        "marked 'resolved' once its remaining balance reaches zero. "
        "Agents earn a commission percentage on amounts they personally "
        "recover; supervisors view total recovered and commission owed "
        "per agent for a given month."
    )),
    ("i10_grant_milestone_compliance", (
        "A post-award grant compliance tracker for a nonprofit, with "
        "program-officer and grantee roles. Program officers record "
        "awarded grants (grantee organization, total amount, a list of "
        "funding milestones each with a target date and a release "
        "amount). Grantees submit a compliance report for a milestone "
        "before or after its target date; program officers review and "
        "approve the report, which releases that milestone's funding "
        "amount. Milestones past their target date with no approved "
        "report are flagged 'overdue' for the program officer. Grantees "
        "view total funding released to date versus their grant's total "
        "amount."
    )),
    ("i11_event_staffing_shift_scheduler", (
        "An event staffing platform with staffing-manager and worker "
        "roles. Managers create events, each needing a set of shifts "
        "(role needed, start time, end time, hourly rate). Workers sign "
        "up for an open shift, which fills that shift's single slot "
        "(blocking other workers from also signing up for it). Workers "
        "check in and check out of their shift on the day of the event; "
        "the system computes hours worked and total pay owed (hours "
        "times the shift's hourly rate). Managers view total labor cost "
        "per event and each worker's upcoming shifts and pay owed."
    )),
    ("i12_franchise_inspection_compliance", (
        "A franchise inspection and compliance scoring system with "
        "inspector and location-manager roles. Inspectors define a "
        "standard checklist of inspection items (description, max "
        "points). Inspectors perform an inspection visit at a franchise "
        "location, scoring each checklist item (points earned out of its "
        "max), which computes an overall compliance percentage for that "
        "visit. Any item scored below half its max points becomes a "
        "flagged non-compliance issue with a remediation deadline (visit "
        "date plus 30 days). Location managers view their location's "
        "inspection history, current compliance score trend, and any "
        "open non-compliance issues with remaining days until the "
        "deadline."
    )),
    ("i13_construction_change_order_tracking", (
        "A construction project cost-tracking system with "
        "project-manager and contractor roles. Project managers create "
        "projects with an original contract amount. Contractors submit "
        "change order requests against a project (description, cost "
        "impact which may be positive or negative). Project managers "
        "approve or reject each change order; approved change orders "
        "adjust the project's running current contract total (original "
        "amount plus the sum of approved change orders). Project "
        "managers view a project's full change order history and the "
        "percentage change from the original contract amount; a project "
        "with cumulative approved changes exceeding 20% of the original "
        "amount is flagged for review."
    )),
    ("i14_warranty_extended_service_plans", (
        "An extended service plan (warranty) sales and claims platform "
        "for an appliance retailer, with sales-rep and claims-adjuster "
        "roles. Sales reps sell a service plan against a customer's "
        "purchased product (plan tier, coverage length in months, "
        "purchase date), which computes the plan's expiration date. "
        "Customers (or reps on their behalf) file a claim against an "
        "active, unexpired plan describing the issue; claims filed "
        "against an expired plan are rejected automatically. Adjusters "
        "review claims and approve or deny them with a resolution note; "
        "approved claims record a repair cost. Adjusters view total "
        "approved claim cost per plan tier for a given month."
    )),
    ("i15_multi_location_staff_credential_tracking", (
        "A staff credential compliance tracker for a multi-location "
        "healthcare provider, with compliance-officer and staff-member "
        "roles. Compliance officers define required credential types "
        "(e.g. CPR certification, license renewal) each with a validity "
        "period in months. Staff members upload a record of holding a "
        "credential (credential type, date obtained), which computes an "
        "expiration date from the validity period. Compliance officers "
        "see, per location, every staff member's credential status "
        "(valid, expiring within 30 days, or expired) and can flag a "
        "location as non-compliant if any staff member there has an "
        "expired required credential. Staff members view their own "
        "credentials and upcoming expiration dates."
    )),
]

assert len({k for k, _ in APPS}) == len(APPS), "duplicate app keys in ForgeBench-Hard2 APPS"


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
            "benchmark": "ForgeBench-Hard2",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "total_apps": len(APPS),
            "results": results,
        }, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description="ForgeBench-Hard round 2")
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--start-at", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=1500)
    args = parser.parse_args()

    _acquire_lock("forgebench-hard2")
    try:
        results = _load_results()
        done_keys = {r["app"] for r in results}

        for i, (app_key, idea) in enumerate(APPS, start=1):
            if i < args.start_at or app_key in done_keys:
                continue
            print(f"\n{'='*70}\n  FORGEBENCH-HARD2 -- App {i}/{len(APPS)}: {app_key}\n{'='*70}")
            r = _run_one_isolated(app_key, idea, args.provider, args.timeout)
            results.append(r)
            _save_results(results)

            if r.get("crashed"):
                print(f"  [{app_key}] CRASHED: {r.get('error')}")
            else:
                print(f"  [{app_key}] score={r['forge_score']:.1f} ({r['grade']}) "
                      f"succeeded={r['succeeded']} fix_attempts={r['fix_attempts']} "
                      f"cost=${r['estimated_cost']:.4f} time={r['elapsed_s']}s")

        print(f"\n{'='*70}\n  FORGEBENCH-HARD2 -- {len(results)}/{len(APPS)} apps done\n{'='*70}")
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
