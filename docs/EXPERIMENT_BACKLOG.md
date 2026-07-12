# Engineering Backlog (Experiment 059, Part 10)

2026-07-12. 20 candidate experiments, sourced directly from
`docs/ENGINEERING_REVIEW.md` / `docs/VALIDATOR_REVIEW.md` /
`docs/PERFORMANCE_FINDINGS.md` findings — every item cites the finding
it addresses. Sorted by **ROI ÷ engineering hour**, not total impact or
feature count (a 30-minute fix for a confirmed bug ranks above a
week-long refactor with similar total value).

Effort scale: XS (<1h), S (1-3h), M (half-day to 1 day), L (2-4 days),
XL (1+ week). API cost: $0 (pure code/docs) vs. an estimate in canary
runs where live validation is required.

---

### 1. Fix `deployed_fixer.py`'s try/finally-with-no-except
**Addresses:** Part 2/7 finding #1 (`deployed_fixer.py:210-263`).
**Effort:** XS. **API cost:** $0. **Expected ROI:** High — prevents one
buggy fix function from silently discarding every other unrelated fix
AND the independent `_resync_frontend` step in the same job. **Risk:**
Low (adding an except clause is additive, easy to verify with a
git-stash-replay test matching this session's own established pattern).
**Prerequisites:** None. **Success criteria:** A forced exception in one
dispatched fix function no longer prevents the remaining fixes/resync
step from running; regression test proves it (mirrors
`test_frontend_patch_isolation.py`'s shape).

### 2. Fix `deployment_fix_service.py`'s uncaught deterministic-fix dispatch
**Addresses:** Part 2/7 finding #2 (`deployment_fix_service.py:270`).
**Effort:** XS. **API cost:** $0. **Expected ROI:** High — same class of
fix as #1, same file family. **Risk:** Low. **Prerequisites:** None.
**Success criteria:** A raising deterministic fix no longer propagates
uncaught out of `generate_deployment_fix()`; test proves it.

### 3. Drop the duplicate `compute_prevention_rate` call in `/observatory`
**Addresses:** Performance Finding 6 (`main.py:882` vs `:885`).
**Effort:** XS. **API cost:** $0. **Expected ROI:** Low-medium (small
absolute cost today, zero behavior risk since the discarded result is
provably unused by the frontend). **Risk:** Near-zero — confirmed via
direct source read that `cockpit.prevention_by_category`/`.prevention_total`
are never read by `Observatory.jsx`. **Prerequisites:** None. **Success
criteria:** `compute_prevention_rate` runs once per request, not twice;
`/observatory`'s response is byte-identical otherwise.

### 4. Consolidate the duplicate `_find_free_port`
**Addresses:** Part 1 (`playwright_runner.py:29` vs `docker_validator.py:45`).
**Effort:** XS. **API cost:** $0. **Expected ROI:** Low (cosmetic/DRY,
not a live bug). **Risk:** Near-zero — confirmed byte-for-byte-identical
algorithm, only start-port/range differ (can be parameters). **Prerequisites:**
None. **Success criteria:** One shared `app/utils/net.py::find_free_port(start, count)`,
both call sites pass their own start/range, existing tests for both
still pass.

### 5. Fix `job_queue.py`'s silent `config_json` corruption swallow
**Addresses:** Part 7 finding #3 (`job_queue.py:274-277`).
**Effort:** S. **API cost:** $0. **Expected ROI:** Medium (prevents a
job silently running with an empty/wrong config with zero trace of why).
**Risk:** Low — adding a log line + a corruption flag on the `Job`
object is additive. **Prerequisites:** None. **Success criteria:** A
corrupted `config_json` value is logged and flagged, not silently
replaced with `{}`; test with a deliberately malformed row proves it.

### 6. Add errata to `docs/REPAIR_DEBT.md` / resolve the contradiction with `REPAIR_ARCHITECTURE.md`
**Addresses:** Part 8 top finding (Rank 2 contradiction; Rank 4/6/7 stale
claims). **Effort:** S. **API cost:** $0. **Expected ROI:** Medium-high
— this is the single highest-risk documentation finding (could cause a
real regression if followed literally). **Risk:** Zero (docs only).
**Prerequisites:** None. **Success criteria:** `REPAIR_DEBT.md` either
gets a dated errata section noting which findings are resolved and why
Rank 2's recommendation was superseded, or is explicitly marked
historical/superseded by `REPAIR_ARCHITECTURE.md`.

### 7. Fix `run_canary.py`'s stale `--provider` help text
**Addresses:** Part 8 (`run_canary.py:218`). **Effort:** XS. **API
cost:** $0. **Expected ROI:** Low (single string), but actively
misleading about live behavior every time `--help` is read. **Risk:**
Zero. **Prerequisites:** None. **Success criteria:** Help text matches
the actual Cerebras-first chain.

### 8. Add per-validator timing to the 13 standalone validators
**Addresses:** Part 3 (`docs/VALIDATOR_REVIEW.md` recommendation #2).
**Effort:** S. **API cost:** $0. **Expected ROI:** Medium (immediate
debuggability payoff if `static-validation`'s duration ever regresses —
currently impossible to attribute to a specific check). **Risk:** Low —
purely additive `t0`/`elapsed` wrapping, matches `verification/engine.py`'s
own existing convention. **Prerequisites:** None. **Success criteria:**
Every one of the 13 validators reports its own elapsed time; existing
tests unaffected.

### 9. Add minimal logging to the 13 silent validators
**Addresses:** Part 3 (`docs/VALIDATOR_REVIEW.md` recommendation #3).
**Effort:** S. **API cost:** $0. **Expected ROI:** Medium (cheap
debuggability). **Risk:** Low. **Prerequisites:** None (can be combined
with #8 in one pass). **Success criteria:** Each validator prints
name + pass/fail + error count.

### 10. Consolidate `_COLUMN_TYPE_RULES` / `_SCHEMA_FIELD_TYPE_RULES` into one table
**Addresses:** Part 2 (`database_patcher.py:498` vs `:1145`, confirmed
live drift — the `"value"` suffix is present in one, absent in the
other). **Effort:** S-M. **API cost:** $0. **Expected ROI:** Medium-high
— fixes an already-manifested behavioral inconsistency, and prevents
the next drift before it happens. **Risk:** Low-medium (must verify both
call sites' existing test coverage still passes with the merged table).
**Prerequisites:** None. **Success criteria:** One `_FIELD_TYPE_RULES`
table serves both `_infer_column_spec` and `_infer_schema_field_spec`;
existing `database_patcher` tests green; new test asserts the `"value"`
suffix now behaves identically for both column and schema inference.

### 11. Cache or tail-read `experiments.md` parsing for Observatory
**Addresses:** Performance Finding 5 (`experiment_log.py:17-31`, 4074-line
full-file re-parse per request). **Effort:** S-M. **API cost:** $0.
**Expected ROI:** Low today, growing — worth doing before it becomes
noticeable, not after. **Risk:** Low (cache invalidated on file mtime,
or read-from-tail with a fallback to full-read if fewer than N headings
found in the tail slice). **Prerequisites:** None. **Success criteria:**
`/observatory` response unchanged; measured wall-clock for this one call
drops proportionally to file size no longer mattering.

### 12. Write a redundant-scan-count baseline regression test
**Addresses:** Part 6's own top testing recommendation — enables #16
below to be done safely later. **Effort:** S. **API cost:** $0.
**Expected ROI:** Medium (de-risks a much larger future refactor).
**Risk:** Zero (a new test, no production code touched). **Prerequisites:**
None. **Success criteria:** A test instruments `os.walk`/`rglob` call
counts during one `validate_project()` invocation and asserts the
CURRENT count (~20) as a documented baseline, so a future consolidation
has a concrete before/after to prove against.

### 13. Centralize repair metadata (name/description/category)
**Addresses:** Part 2 (scattered print-string metadata; the
`DETERMINISTIC_PREVENTION_CATEGORIES` drift risk in
`reliability_metrics.py`). **Effort:** M. **API cost:** $0. **Expected
ROI:** Medium (prevents a new patcher silently missing categorization,
mirrors the #10 drift-prevention logic for a different subsystem).
**Risk:** Low-medium (touches many call sites, but purely additive —
metadata lookup, not behavior change). **Prerequisites:** None ideally
paired with #9 (both touch "what does a repair report about itself").
**Success criteria:** Every patcher's name/description/category is
declared once and looked up, not hand-typed at each print site; a new
patcher added to the registry automatically appears in Observatory's
prevention-by-category breakdown without a separate manual edit.

### 14. Investigate the recurring `SignupRequest`/`User` schema-field `AttributeError`
**Addresses:** Exp056 §4 / Exp058 (confirmed twice live as `todo`'s
actual score-capping issue, not the Exp053/057 regression). **Effort:**
M. **API cost:** ~2-4 canary generations (live validation required —
this is a generation-quality bug, not a pure-code fix; needs fresh,
non-cached samples to investigate properly, unlike Exp058's own
cache-heavy runs). **Expected ROI:** High — this is the actual remaining
blocker on `todo`'s Forge Score right now, already confirmed reproducible
twice. **Risk:** Medium (generation-quality fixes are harder to verify
in isolation than a scoping bug like Exp057's). **Prerequisites:**
Exp057/058 (done). **Success criteria:** Root cause identified (why does
the LLM keep generating `req.username` against a schema without that
field, and why doesn't the fix loop's cleanup catch it), a fix proposed
and validated to actually change the failure signature (not just the
score) on at least 2 fresh, non-cached generations.

### 15. Write a full-pipeline architecture doc + diagram (idea → deploy)
**Addresses:** Part 8 (no single doc/diagram covers the whole pipeline;
both existing mermaid diagrams start mid-pipeline at the repair layer).
**Effort:** M. **API cost:** $0. **Expected ROI:** Medium (onboarding +
future-review efficiency — this review itself had to reconstruct the
picture from scattered docs). **Risk:** Zero. **Prerequisites:** None.
**Success criteria:** One doc, one diagram, covers
planner→architect→backend/frontend→deterministic-patch→verification→repair→deploy
in one place; cross-links to the existing repair-specific docs rather
than duplicating them.

### 16. Document the 8 undocumented repair-adjacent modules
**Addresses:** Part 8 (`architecture_fix_service.py`, `diff_repair_service.py`,
`fixer_service.py`, `fixture_loader_service.py`, `fix_log_service.py`,
`fix_writer_service.py`, `frontend_fix_service.py`, `runtime_fix_writer.py`
— zero mentions in any doc). **Effort:** M. **API cost:** $0. **Expected
ROI:** Medium (closes an unknown-coverage gap — these were never in
scope for any prior repair audit, so their reliability posture is
literally unassessed). **Risk:** Low (documentation-first; may surface a
need for follow-up code fixes, which would become separate backlog
items). **Prerequisites:** None. **Success criteria:** Each module has
at least a short doc entry (purpose, call sites, known issues if any
found while reading it for the doc).

### 17. Unify the 4 validator result shapes into `Diagnostic`
**Addresses:** Part 3, the review's single highest-confidence finding
(already caused a confirmed live bug — wrong file fixed due to missing
`file_path`). **Effort:** L. **API cost:** $0 (pure refactor + tests),
though a live canary re-validation afterward would strengthen
confidence. **Expected ROI:** High — closes the exact gap that already
caused a real repair-loop failure. **Risk:** Medium (touches 13+
validator files; must preserve every existing error message's
observable content even while changing its container type). **Prerequisites:**
#12-style baseline tests recommended first for each validator touched.
**Success criteria:** All 13 standalone validators + `runtime_validator_service.py`
+ `user_journey_runner.py`'s `JourneyStep` either migrate to `Diagnostic`
or get an explicit, documented adapter; `verification/engine.py:1641-1662`'s
regex-based file-path recovery workaround becomes unnecessary and can be
removed; a regression test proves a `Diagnostic`'s `file_path` is now
always populated for what used to be string-only errors.

### 18. Consolidate `validator_service.py`'s ~20 redundant `os.walk`/`rglob` calls
**Addresses:** Performance Finding 1, the single largest performance
finding in the review. **Effort:** L. **API cost:** $0 (pure refactor),
live re-validation optional afterward. **Expected ROI:** High (10-20x
I/O reduction on the hottest path — this runs once per fix-loop retry
attempt, and the retry loop is exactly what Exp057 just fixed to run
MORE often, making this more relevant, not less). **Risk:** Medium-high
(touches 12+ function signatures; must not silently drop a check).
**Prerequisites:** #12 (baseline test) strongly recommended first.
**Success criteria:** One shared file-list computed once per
`validate_project()` call, passed to all delegated validators; #12's
baseline test now shows the walk count drop from ~20 to ~1-2; all
existing validator tests still pass; a live canary run (optional, small
budget) shows no change in validation outcomes, only wall-clock.

### 19. Decompose `run_user_journey` (worst complexity/depth in the repo: 166/15)
**Addresses:** Part 1 (`app/runtime/user_journey_runner.py:344-976`).
**Effort:** L-XL. **API cost:** $0 for the refactor; live canary
re-validation strongly recommended given this function directly drives
CRUD-journey scoring. **Expected ROI:** Medium-high long-term
(maintainability + reduces the risk of a future silent behavior change
in the function that produces the CRUD pass/fail signal this whole
reliability cycle has been optimizing). **Risk:** High — this function
has 21 commits of accumulated fixes; a decomposition could easily
change subtle behavior no test currently pins down. **Prerequisites:**
Thorough test coverage of current behavior FIRST (this function's
current test coverage wasn't assessed in this review — that assessment
itself should precede any refactor attempt). **Success criteria:**
Complexity/depth substantially reduced (target: no single function over
~150 lines / complexity 30), full existing CRUD-journey test suite
green, a live canary run shows byte-identical journey outcomes on the
same 3 canary apps.

### 20. Decompose `generate_project_v6` (highest-risk function in the repo)
**Addresses:** Part 1 — highest churn (41 commits) among the top-5
complexity functions, and the exact function that already produced one
confirmed regression (Exp053→Exp056→Exp057) from an edit that passed its
own test suite. **Effort:** XL. **API cost:** $0 for the refactor itself;
mandatory live canary re-validation given this function's centrality
(same budget-conscious approach as Exp058). **Expected ROI:** High
long-term (this is the single biggest standing regression-risk reducer
identified in the whole review) but the LOWEST ROI/hour of any item here
given the effort and risk involved — ranked last deliberately, not
because it doesn't matter. **Risk:** High — this is precisely the kind
of edit that caused the Exp053 regression in the first place; doing it
again without exceptional care could reintroduce a similar class of bug.
**Prerequisites:** #19 should be done first as a lower-stakes trial of
the same "decompose a giant, high-complexity, high-churn function"
playbook; comprehensive scope-correctness tests (in the style of
`test_runtime_fix_loop_scope.py`, which was purpose-built for exactly
this function) should cover every extracted piece before and after.
**Success criteria:** Function broken into named, independently-testable
stages (planner call, architect call, tech-lead review, generation,
deterministic-patch, verification, runtime-fix loop, export) with no
change to external behavior; full existing suite green; live canary
scores match pre-refactor baseline within noise on all 3 apps.

---

## Sort key recap (ROI ÷ engineering hour, highest first)

Items 1-9: XS-S effort, $0, low risk, immediate or near-immediate payoff
— the "do these regardless" tier. Items 10-13: S-M effort, $0, closes
confirmed drift/debt with moderate payoff. Items 14-16: M effort, mixed
$0/small-budget, addresses the actual remaining live reliability
question (#14) and documentation gaps. Items 17-18: L effort, $0, high
absolute value but enough risk/scope to need dedicated cycles with their
own test-first prerequisites. Items 19-20: L-XL effort, highest risk,
highest long-term value, deliberately last by ROI/hour — these are
"schedule a dedicated cycle for this," not "fit it into a review."
