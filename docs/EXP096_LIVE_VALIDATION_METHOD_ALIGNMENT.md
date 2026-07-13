# Experiment 096 — Live Validation of Architecture-Aware Update Methods

2026-07-13. Live, two Cerebras canaries (both allowed by this
experiment's own constraint), $0.3009 total (r1 $0.1463 / r2 $0.1546),
501,474 tokens. `backend/scripts/exp096_canary.py` wraps
`app.runtime.user_journey_runner._detect_crud_entity` and
`run_user_journey` to record the architecture-declared
`(resource, update_method)` tuple and the full journey step
(pass/fail/detail/request/response) for every call, non-invasively —
same methodology as Exp079/082/086/089/093. Both call sites
(`backend_runner.py`, `playwright_workflow.py`) import
`run_user_journey` inline at call time, so patching the module
attribute before generation starts is sufficient.

## 1. Runs (Task 1)

| Label | Idea (domain) | Score | Fix attempts |
|---|---|---|---|
| `exp096-validation-r1` | Sports league management (`sports_league_manager`-inspired) | 99.3/100 (A+), deploy-ready | 2/5 |
| `exp096-validation-r2` | Project/task management (`teamflow_pm`-inspired) | 92.4/100 (A), deploy-ready | 0/5 |

Both projects independently on disk:
`generated_projects/sports_league_manager` (regenerated) and
`generated_projects/secure_project_manager` (new).

## 2. Architecture-declared update method (Task 2) and Journey Runner's selection (Task 3)

Both directly captured via the instrumented `_detect_crud_entity()`
wrapper — the exact function the journey runner calls, not an inferred
guess:

```
r1 (sports_league): 5 calls, all -> ('leagues', 'PUT')
r2 (task_management): 3 calls, all -> ('projects', 'PUT')
```

**Independently cross-verified** against the actual generated code (not
just trusting the instrumentation): `generated_projects/sports_league_manager/
app/routes/league_routes.py` now declares `@league_router.put("/leagues/{league_id}", ...)`
(this regeneration chose PUT, unlike the historical instance Exp094
analyzed, which chose PATCH — LLM variance, expected) and
`generated_projects/secure_project_manager/app/routes/project_routes.py`
declares `@project_router.put("/projects/{project_id}", ...)`. Both match
the instrumented capture exactly. Journey Runner's method selection is
therefore **confirmed correct in both runs** — it used PUT because PUT
is what the architecture actually declared, not because of any
hardcoded assumption (verified by reading the modified
`_detect_update_method` logic executing live, not just its offline
tests).

**Neither run exercised the PATCH branch** — the architect chose PUT for
the test-selected entity both times. This is the same "null result,
uninformative but not concerning" scenario flagged in advance by
Exp095's own recommendation (LLM verb-choice variance means this can't
be forced deterministically; Exp094's corpus scan already put the
current base rate at roughly 2-4% of projects). Notably, `secure_project_manager`'s
`task_routes.py` update route is nested (`/projects/{project_id}/tasks/{task_id}`,
not a flat `tasks/{id}` shape), so it wouldn't have been eligible for
`_detect_update_method`'s exact-shape check regardless of verb — `projects`
was correctly the only full-CRUD candidate.

## 3. CRUD journey execution (Task 4) and success-criteria checks (Task 5)

**r1 (sports_league)**: first verification pass showed
`Edit entity: 422` — **not a 405**, a different, already-identified,
explicitly out-of-scope defect (Exp094 §2: the `LeagueUpdate` schema
required `season`/`start_date`/`end_date`/`status` on every update
instead of treating them as optional for partial updates). The
runtime-fix loop patched `app/schemas/league.py` (LLM fix, unrelated to
Exp095) and all 3 subsequent re-verifications showed
`Edit entity: 200`, journey PASS (11/11).

**r2 (task_management)**: first verification pass showed
`Edit entity: no entity_id captured` — a cascading consequence of an
earlier Create-path hiccup (already-closed thread, Exp091-093), not an
Edit-path defect; correctly short-circuited by `do_edit()`'s existing
`if entity_id is None` guard. Resolved automatically by existing
deterministic infrastructure (0 LLM fix attempts needed — `Fix Attempts: 0/5`).
Both subsequent re-verifications showed `Edit entity: 200`, journey
PASS (11/11).

Checking against this experiment's explicit success criteria:

- **No false 405 failures**: confirmed — zero 405s in either run, at
  any verification pass. The two Edit-entity failures that did occur
  (422, no-entity_id) are both pre-existing, already-scoped-out defect
  classes, not new and not method-mismatch related.
- **Endpoint inventory unchanged**: confirmed — 24/24 (r1) and matching
  full pass rate (r2) endpoint smoke tests passed identically across
  every re-verification within each run; no endpoint appeared or
  disappeared between passes.
- **ExchangeRecorder still captures requests correctly**: confirmed
  directly — r1's failing 422 step recorded the full exchange:
  `{'method': 'PUT', 'url': '.../leagues/1', 'json': {...}, 'has_auth': True}`
  / `{'status_code': 422, 'body': {...}}`, proving the forensic
  bundle-capture wrapper (`_ExchangeRecorder`) is untouched and working
  post-Exp095 (this was the specific regression risk flagged and
  avoided in Exp095 by not using `requests.request(...)`).
- **Runtime behavior matches architecture**: confirmed — both runs used
  PUT at runtime because PUT is what the architecture declared; no
  divergence observed.

## 4. Comparison against Exp094 replay expectations (Task 6)

Exp094's replay predicted that a resource whose architecture declares
only PATCH for its `<resource>/{id}` route would 405 pre-fix and pass
post-fix (confirmed offline in Exp095 via the fake-server replay,
git-stash-verified). Neither live run reproduced the PATCH-declaring
condition itself, so this specific prediction wasn't re-exercised live
this cycle — but nothing observed contradicts it, and the PUT path
(Exp094's "fine" case, 46/49 of the corpus) is now doubly confirmed
correct both offline and live.

## 5. Observatory update (Task 7)

- **PATCH detections**: 0 across both live canaries (architect chose
  PUT both times) — consistent with Exp094's corpus-level base rate
  (roughly 2-4% of projects currently exhibit a PATCH-only
  test-selected entity), not a concerning result.
- **Method-selection statistics**: 2/2 runs resolved correctly and
  stably (8 total `_detect_crud_entity()` calls across both runs'
  multiple re-verification passes, zero flip-flopping, 100% match with
  the actual generated code on independent cross-check).
- **CRUD outcome**: both runs reached journey PASS (11/11) by their
  final verification pass; both deploy-ready (99.3/A+, 92.4/A).
- **Remaining JourneyCRUDFailure prevalence**: regenerated
  `backend/observatory_report.html` via `scripts/observatory.py` —
  `top_failure_now` has shifted to `AttributeError`, no longer
  `JourneyCRUDFailure` (`top_failure_historically` still shows
  `JourneyCRUDFailure`, reflecting the full window). Direct count: 3
  `JourneyCRUDFailure` entries in the last 30 `generation_log.jsonl`
  records, of which 2 are Edit-path-shaped (405) — both are the same
  two historical runs (`inventory_manager` 2026-07-11,
  `forge_blog_cms` 2026-07-12) that motivated Exp094/095 in the first
  place; zero new Edit/405 entries logged since the Exp095 fix shipped.
  No permanent new dashboard counter added — same reasoning as every
  prior cycle in this series: 2 live runs is not enough accumulated
  data to justify one, and the existing taxonomy fields already surface
  this correctly.

## 6. Recommendation for Exp097

Close this validation thread. The fix is confirmed correct and
non-regressive via the strongest evidence available short of directly
reproducing a live PATCH case (which two domain-targeted attempts
didn't manage to do, consistent with the known ~2-4% base rate and this
series' established precedent of accepting informative-but-non-firing
live results, e.g. Exp082, Exp086, Exp089, Exp093). Recommend returning
to the broader taxonomy for Exp097: re-scan `generation_log.jsonl`/
`patterns.json` given `top_failure_now` has shifted to `AttributeError`
— that is now the highest-impact remaining active class per the
Observatory's own current computation, and hasn't been investigated in
this series yet.

**Deliverables**: this doc, `experiments.md` entry,
`backend/scripts/exp096_canary.py`,
`backend/benchmark_results/exp096_method_detection_invocations.json`,
regenerated `backend/observatory_report.html`, two canary history
entries (`exp096-validation-r1` BASELINE 99.3,
`exp096-validation-r2` BASELINE 92.4). **Cost: $0.3009, two live
generations.**
