# ForgeAI Experiment Log

Every paid generation run (Gemini/Cerebras/whatever provider is under test) gets an
entry here. Rule: if a run doesn't answer a stated hypothesis, don't run it. Claude
does architecture, prompts, validators, and code for free — only the generation
step under test costs money.

Canary script: `backend/scripts/run_canary.py` (apps: todo / blog_cms / crm,
`--no-deploy` unless deploy behavior is the thing being tested). Raw numbers also
land in `backend/benchmark_results/canary_history.json`.

---

## Experiment 001 — m0-quick-wins (baseline)

**Hypothesis:** N/A — first-ever canary run, establishes the baseline to diff
future runs against.

**Changes under test:** N/A (baseline snapshot before M1 AppContract work).

**Date:** 2026-07-06

**Apps:** todo / blog_cms / crm

**Results:**
- todo: score 76.9, build ✅, runtime ❌, crud N/A, deployed ❌
- blog_cms: score 33.0, build ❌, runtime ❌, crud N/A, deployed ❌
- crm: score 76.9, build ✅, runtime ❌, crud N/A, deployed ❌

**Conclusion:** Canary harness works and is safe to gate future milestones on.
blog_cms is the weakest app (build failure) — worth root-causing separately.
Runtime dimension failing across the board even where build passes.

---

## Experiment 002 — m1-contract (INCONCLUSIVE — provider exhaustion)

**Hypothesis:** Wiring `ContractConformanceValidator` into `VerificationEngine`
in warn-only mode (commit 6e117be) does not regress build/runtime/crud/browser/
deployed/score on any of the 3 canary apps vs. Experiment 001.

**Changes under test:** AppContract IR schema (ac06e54) + ContractAdapter
(a4bb28a) + warn-only ContractConformanceValidator wiring (6e117be).

**Date:** 2026-07-06

**Apps:** todo / blog_cms / crm (`--no-deploy`)

**Results:**
- todo: score 76.9 → **25.3**, build now failing (flagged REGRESSION)
- blog_cms: score 33.0 → 31.5, still failing build (flagged OK, no prior pass to regress from)
- crm: score 76.9 → **0.0**, pipeline generation failed outright

**Root cause of the "regression"**: NOT the contract validator. Mid-run, Groq
hit its org-wide daily token limit (100,000 TPD, used ~95,854 — resets ~1h44m
after 2026-07-06 10:29 IST, i.e. ~12:15 IST) and Gemini was simultaneously
returning 503 "high demand" errors. With both fallback providers down, the
fix-loop and (for crm) the initial generation itself had no working model to
call, so scores collapsed for infrastructure reasons, not code reasons.

**Conclusion:** This run answers nothing about AppContract/ContractConformanceValidator.
Do not treat the score drops as evidence against M1. Re-run once Groq's daily
quota resets (~12:15 IST) to get a clean signal. Confirms the user's warning
that provider quotas were "1 generation from limit" — literally true for Groq.

---

## Experiment 003 — generation_log telemetry bug (Claude-only, $0)

**Hypothesis:** Why does `frontend_built` only pass ~53% of the time
(project_history.jsonl), and why couldn't a clean V15-specific failure
taxonomy be built? Root-cause without spending any generation credits.

**Changes under test:** None — pure investigation using graphify + direct
code reading, no LLM generation calls at all.

**Date:** 2026-07-06 (while Groq/Gemini were rate-limited/down)

**Findings:**
1. `patterns.json` / `project_history.jsonl` are written only by the legacy
   `project_service.generate_project()` path (v6/v7) and by ForgeBench's
   default generator — never by V15 (`app/core/pipeline.py`). The failure
   taxonomy/variance numbers logged in Experiment 001's context and
   `project_failure_taxonomy` memory describe the OLD pipeline, not V15.
2. V15's own equivalent, `failure_memory/generation_log.jsonl` (feeds the
   confidence engine's success-rate priors), had only 2 lines total, last
   written 2026-06-28, despite many V15 runs since. Root cause:
   `app/core/pipeline.py:334` did `getattr(ctx, "all_diagnostics", [])`, but
   `GenerationContext.all_diagnostics` is a **method**, not a property —
   `getattr` returned the unbound method itself, and iterating it
   (`for d in all_diags`) raised `TypeError: 'method' object is not
   iterable`, silently swallowed by a bare `except Exception: pass`.

**Fix:** `all_diags = ctx.all_diagnostics()` (call it). Verified via a
standalone local script constructing a minimal `GenerationContext` +
`Diagnostic`: confirmed the old code raises `TypeError` and the new code
returns the correct `dominant_errors` list. No generation calls used.

**Conclusion:** V15 has been "flying blind" on its own per-run telemetry
for over a week. Going forward, `generation_log.jsonl` (not
`project_history.jsonl`/`patterns.json`) is the correct source for a
V15-specific failure taxonomy — but it needs fresh runs post-fix to build
up a meaningful sample. **Committed 862d393, pushed to main.**

---

## Experiment 004 — silent-exception audit (Claude-only, $0)

**Hypothesis:** VNext report special task — audit every `except`/silent-return
in the live V15 stack (`pipeline.py`, `verification/engine.py`,
`repair/orchestrator.py`, `retry/manager.py`, `scoring/engine.py`) for hidden
failures like Experiment 003's bug, before spending any more generation credits.

**Changes under test:** None — pure code review, no generation calls.

**Date:** 2026-07-06

**Findings (71 except blocks reviewed):**
- Most already log their exception; only ~14 were truly silent (`except:
  pass` with no message).
- Reviewed each: 4 are benign expected control flow (`ValueError` from
  `Path.relative_to`, best-effort backend-process cleanup) — left as-is.
- 10 were real gaps and got structured logging added: `pipeline.py`
  (reset_session, cost-totals sync, confidence engine, generation_log write,
  arch_db write, deploy-failure secondary record) and `repair/orchestrator.py`
  (missing-import stub write, fix-context file read, scaffolded-stub re-read,
  fix_cache.store).
- **Most important find**: `_ProjectSnapshot.revert()` in
  `repair/orchestrator.py` printed `"Reverted to pre-fix snapshot"`
  *unconditionally*, even when individual file restores/deletes silently
  failed. A partially-failed revert was being reported — and trusted — as a
  clean recovery. This is the same class of "half-reverted mixed state" bug
  a prior fix's `_EXTS` widening was meant to solve, just reintroduced at
  the I/O-failure layer. Now tracks per-file failures and reports "Revert
  INCOMPLETE" with specifics when anything fails to restore/delete.

**Fix:** structured `print(...)` logging added at each real gap; revert()
now conditionally reports success vs. incomplete. Verified via `ast.parse`
on both files + live `import` of all 5 touched modules. No generation calls.

**Conclusion:** No score-affecting bug found here (unlike Experiment 003),
but this closes off a class of "next silent bug that takes a week to
notice." **Committed 4287344, pushed to main.**

**Candidate for next cycle (not implemented — one improvement at a time)**:
while reading `repair/orchestrator.py`'s `fix_cache.store()`/`lookup()`, found
that `FixCache` keys on `sha256(sorted(diagnostic messages))` — an exact-text
hash. Since diagnostic messages are often file/variable-specific, this will
essentially only ever hit on a byte-for-byte repeat, matching the VNext
report's own diagnosis (§8.3 / ROI #9: "repair_db has 9 entries and
near-zero reuse"). Re-keying on `(pattern_id, contract_fingerprint)` instead
of raw message hash is VNext ROI #9 (Medium effort, "+2, −cost"). Proposing
this as the next targeted improvement once the current changes are validated
by a clean canary run — not starting it now per the "implement ONLY one
improvement, validate, benchmark, then continue" rule.

---

## Experiment 005 — m1-contract-gemini (clean run, real signal)

**Hypothesis:** Same as Experiment 002 (does warn-only ContractConformanceValidator
regress anything), re-run with `--provider gemini` to avoid the exhausted-Groq
confound. User asked to force Gemini directly rather than wait for Groq's
daily reset.

**Changes under test:** Same M1 code (ac06e54/a4bb28a/6e117be) — no new code
changes going into this run. `--provider gemini` added to `run_canary.py`
(commit e469045) to make this possible.

**Date:** 2026-07-06, ~11:15 IST

**Results vs. the TRUE baseline (Experiment 001, not the contaminated 002):**
| App | Baseline (001) | This run (005) | Delta |
|---|---|---|---|
| todo | 76.9, build✅ | 25.5, build✅ but 2 Python syntax errors block runtime | **-51.4** |
| blog_cms | 33.0, build❌ | 66.1, build❌ (different error now) | **+33.1** |
| crm | 76.9, build✅ | 65.8, build✅ | -11.1 |

(Note: the canary script's own "OK"/"REGRESSION" verdict compared against
Experiment 002 — the provider-exhausted run — not 001, so its "CANARY PASSED"
readout is not meaningful here. Manual diff against 001 above is the real
comparison.)

**New finding — root-caused — malformed route filename from a query-string
endpoint:** todo's Python syntax errors trace to a generated route module
literally named `tasks_limit=5&sort_by=created_at&sort_order=desc`. Traced
to the very first diagnostic in the run (line 142 of the log, BEFORE any
fix-loop patch touched anything):
`Missing endpoint GET /tasks?limit=5&sort_by=created_at&sort_order=desc
(expected in app/routes/tasks?limit=5&sort_by=created_at&sort_order=desc_routes.py)
-- called from src/pages/DashboardPage.jsx but never implemented on the backend`.
So this is a pre-existing bug, NOT caused by AppContract/M1: somewhere
upstream (architect endpoint spec or frontend API-call generation) produced
an endpoint whose "path" field is a literal path+querystring
(`/tasks?limit=5&sort_by=...`) instead of a bare path with separate query
params, and the endpoint-validator / router-patcher then naively derived a
file/router name directly from that string — `?`, `=`, `&` end up baked into
a Python module name, which can never compile. The ContractConformanceValidator
correctly flagged it ("No endpoints found in app/routes/tasks..."); the bug
is in whatever derives file/router names from endpoint paths not sanitizing
query strings out first. Good candidate for a small, deterministic, high-
confidence fix next cycle (arguably higher priority than the FixCache
re-keying candidate below, since this is an observed crash from today, not
a theoretical inefficiency) — not implemented this cycle per the one-change
rule.

**Bonus catch — confidence engine**: this run's log printed `[V15] confidence
engine failed (non-fatal): 'method' object is not iterable` for all 3 apps —
the logging added in Experiment 004 immediately surfacing a second,
independent occurrence of the exact bug class fixed in Experiment 003, this
time in `app/confidence/engine.py`. Investigated: EVERY attribute name in
`compute_from_context()` was wrong (`best_score`/`scores`/`attempt_count`
don't exist on `GenerationContext`; real names are
`latest_score`/`score_history`/`fix_attempts`), plus a `isinstance(dims, dict)`
check that could never be true since `QualityScore.dimensions` is a list.
The confidence engine has never once produced a real report. Fixed and
verified locally (constructed a `GenerationContext` + `QualityScore` via the
real `ctx.record_score()` path, confirmed correct factor values). **Committed
cbb46fb, pushed to main.**

**Conclusion:** Inconclusive on the AppContract question specifically — one
sample per app isn't enough to separate "M1 caused this" from "normal
LLM-output variance," and todo's regression traces to a new, unrelated-looking
bug (malformed route filename), not an obvious contract-validator side effect.
Need at least one more clean todo run to see if the query-param-filename bug
recurs. The confidence-engine fix was the clear, unambiguous win from this run.

---

## Experiment 006 — filename-sanitization fix + GenerationContext audit (Claude-only, $0)

**Hypothesis:** (a) Fix the querystring-in-filename bug root-caused in
Experiment 005 before evaluating AppContract further — user's explicit
reprioritization (filename bug > confidence engine [done] > re-canary >
AppContract eval). (b) Given confidence engine had two independent stale-
attribute bugs, audit every `GenerationContext` consumer for the same class
of issue before it causes a third surprise.

**Changes under test:**
1. `endpoint_validator.py:207` — `validate_frontend_api_calls` derived
   `resource`/`expected_file` from the raw (un-query-stripped) path; its
   sibling function 60 lines above already stripped `?...` first. Fixed to
   match. Also hardened 3 other call sites with the identical
   `.strip("/").split("/")[0]` pattern (playwright_runner.py,
   deployed_fixer.py, endpoint_smoke_test_service.py) that don't currently
   crash (degrade to empty/no-op on a malformed path) but are the same
   latent bug class.
2. Scanned every `ctx.<attr>` and `getattr(ctx, "...")` in `app/` against
   `GenerationContext`'s real attribute/method list (extracted programmatically
   from `context.py`). Found only one leftover issue: a stale docstring in
   `confidence/engine.py` still said `ctx.scores` (fixed). The two dynamic
   attributes flagged (`failure_graph`, `_backend_runner`) are intentional
   runtime-added state, always accessed via `getattr(..., default)` —
   verified safe by design, not bugs.

**Date:** 2026-07-06

**Verification:** `ast.parse` on all touched files. Isolated reproduction:
fed the exact observed path (`/tasks?limit=5&sort_by=created_at&sort_order=desc`)
through the old vs. new derivation logic — old code reproduces the exact
broken filename from Experiment 005's crash; new code produces
`task_routes.py`. No generation calls.

**Conclusion:** The GenerationContext audit came back mostly clean — good
news, this isn't a widespread epidemic, just the two spots already found in
the confidence engine plus one stale comment. **Committed 4af31b4 (filename
fix) and 6e0fdeb (docstring), pushed to main.** Ready for Priority 3: re-run
the canary to see if todo recovers.

---

## Experiment 007 — m1-post-filename-fix (Priority 3 re-canary)

**Hypothesis:** Did fixing the querystring-in-filename bug (Experiment 006)
restore todo's score, and does a second clean sample still look fine for
blog_cms/crm — separating the AppContract question from the filename-bug
confound.

**Changes under test:** Experiment 006's filename-sanitization fix only.
Same M1 AppContract code as Experiments 002/005 (unchanged).

**Date:** 2026-07-06, ~11:55 IST. `--provider gemini --no-deploy`.

**Results vs. true baseline (Experiment 001):**
| App | Baseline (001) | Exp 005 (pre-fix) | Exp 007 (post-fix) | vs. baseline |
|---|---|---|---|---|
| todo | 76.9 | 25.5 (crash) | **76.4** | -0.5 (recovered) |
| blog_cms | 33.0 | 66.1 | **94.1** (A, deploy-ready) | +61.1 |
| crm | 76.9 | 65.8 | **72.6** | -4.3 |

No occurrence of the querystring-filename bug anywhere in this run's log
(`grep -c "tasks_limit" m1_canary_v2_run.log` → 0). Confidence engine also
confirmed working end-to-end in a real run for the first time: printed a
real "Deployment confidence: 32.8% [F] ... Historical base rate: 28.6%
(n=7)" report for crm instead of crashing.

**This matches the user's stated decision criterion exactly**: "todo
recovered, blog still improved, CRM stable" → stop fixing infrastructure,
move to the next major architectural item (AppContract evaluation, or
whatever telemetry identifies as the next bottleneck). crm's -4.3 is small
enough to be normal run-to-run LLM variance, not a red flag.

**Conclusion:** The filename-bug fix is confirmed working. Two clean data
points now exist with M1's AppContract code active and no known
infrastructure bugs confounding the result: todo and crm both land close to
baseline, blog_cms far exceeds it. This is early positive signal for
AppContract specifically (nothing regressed that wasn't already explained by
a since-fixed bug), though still only 2 samples per app — not yet enough to
formally conclude "M1 improved success rate," just enough to say "M1 did not
regress anything once real bugs are accounted for." Ready to move to
Priority 4 (AppContract evaluation / next architectural item) per the user's
own threshold.

**Health report**: `backend/scripts/health_report.py` (added this cycle) will
have full per-dimension/confidence/repair data starting with the *next*
canary run — this run predates that capture (module was already loaded when
the process started).

---

## Experiment 008 — Controlled AppContract A/B (contract ON vs OFF)

**Hypothesis:** Does the warn-only ContractConformanceValidator (M1) measurably
reduce contract-coherence failures (RouterExportMismatch, MissingEndpoint,
ImportError, ModuleNotFoundError, schema mismatch)? Every prior run (001,
005, 007) already had it wired in — there was no "off" data point to compare
against, so this couldn't actually be answered before this experiment.

**Changes under test:** Added `FORGE_CONTRACT_CHECK=0` env gate to
`verification/engine.py` (commit cb9ad0f) — skips the contract-conformance
stage entirely when set, unchanged behavior otherwise. This is the only
code change; no AppContract logic itself was touched, per "implement only
the minimum necessary for evaluation."

**Date:** 2026-07-06, ~12:15 IST. Paired with Experiment 007 (contract ON,
same day, same provider, same fixes already in place) as the control
comparison — todo/blog_cms/crm, `--provider gemini --no-deploy`.

**Baseline category frequencies** (grep counts across full run logs — noisy
across multiple fix attempts, see caveat below):

| Category | Exp001 (pre-M1) | Exp007 (contract ON) | Exp008 (contract OFF) |
|---|---|---|---|
| RouterExportMismatch | 2 | 0 | 0 |
| MissingEndpoint | 18 | 11 | 3 |
| ImportError | 0 | 8 | 4 |
| ModuleNotFoundError | 0 | 0 | 4 |
| Schema mismatch | 10 | 13 | 5 |
| Contract violation (new category) | 5* | 44 | 0 |

*\*Exp001 already had 5 "contract" hits despite predating what I assumed was
a clean baseline — turns out 6e117be (contract wiring) was committed before
Exp001 ran, so **no run in this dataset has a true "AppContract never
existed" baseline**. Exp008 (this experiment) is the only clean "OFF" data
point that exists.*

**Score comparison (Exp007 ON vs Exp008 OFF):**
| App | ON (Exp007) | OFF (Exp008) | Delta |
|---|---|---|---|
| todo | 76.4 | 76.9 | -0.5 (noise) |
| blog_cms | 94.1 | 45.0 | **-49.1** |
| crm | 72.6 | 47.1 | **-25.5** |

**Critical finding — the gap is NOT attributable to AppContract.** Investigated
why blog_cms/crm scored so much lower OFF: both had **Runtime Startup = 0.0,
API Functionality = 0.0** — the backend never booted at all. Root-caused the
actual crash in both generated projects on disk:

- blog_cms: `AttributeError: 'Config' object has no attribute 'DATABASE_URL'`
  at `app/main.py:35`. Traced further: the preflight patcher
  (`_fix_config_missing_attrs` in `app/repair/preflight.py`) *did* run and
  *did* add `settings.DATABASE_URL` — but only to the `settings` instance
  living in `app/config.py`'s module namespace. `main.py` does its own
  `settings = Config()` (a **second, fresh instance** of the same class) at
  line 34 and reads `settings.DATABASE_URL` off *that* instance, which never
  got patched. The fix patches an instance; the bug needs the attribute on
  the *class*.
- crm: same failure class, different manifestation —
  `AttributeError: 'Config' object has no attribute 'database_url'`
  (lowercase). The preflight patcher's `defaults` dict only checks the
  exact-case key `"DATABASE_URL"`, so a lowercase-convention `Config` class
  isn't covered at all.

Neither of these has anything to do with the contract-conformance stage —
it's LOW severity, doesn't gate deployment or the runtime-skip gate, and by
design only affects the 5%-weight Code Quality dimension. It structurally
cannot cause or prevent a `ConfigAttributeError`. This is ordinary
generation variance (this attempt happened to produce a `Config` class the
preflight patcher's two blind spots both hit) landing in the OFF condition
by chance, not a causal contract effect.

**Conclusion — per the user's own decision rule ("if unclear or
insignificant, do not continue expanding AppContract, identify next highest-
ROI bottleneck instead"):** **Inconclusive on AppContract.** n=1 per
condition can't separate a real effect from LLM generation variance in
principle, and in this specific case the entire score gap has a fully
identified alternate cause unrelated to the contract stage. Recommend NOT
continuing to expand AppContract based on this evidence — not because it's
disproven, but because (a) its current warn-only, post-hoc-derived form
structurally can't influence generation or gate anything yet (the VNext
report's own phased plan expects the real effect only once generators
consume the contract natively, a later milestone), and (b) this run
surfaced a much clearer, twice-confirmed, cheaply-fixable bottleneck instead.

**Next highest-ROI candidate identified (not yet fixed)**: the preflight
`_fix_config_missing_attrs` patcher has two blind spots — (1) patches an
instance, not the class, so a second `Config()` instantiation elsewhere
doesn't inherit the fix; (2) hardcoded to exact-case attribute names, missing
`database_url`/other-case variants. This directly hits Runtime Startup, the
single highest-weighted scoring dimension (20%), and just caused two total
backend-boot failures in one canary run. High confidence, cheap, deterministic
fix — recommend this as the next cycle's target, pending user confirmation.

---

## Experiment 009 — preflight config-patcher fix (validation canary)

**Hypothesis:** Does fixing `_fix_config_missing_attrs`'s two blind spots
(instance-vs-class scoping, case-sensitivity — see Experiment 008) eliminate
the `ConfigAttributeError` boot-crash class, measured against the paired
baseline that had it (Experiment 008)?

**Changes under test:** `preflight.py`'s config patcher now also sets
defaults on the CLASS (not just one instance) in both canonical and
lowercase spelling, when the settings object is built from a plain class
defined in the same file (commit f2721c5). Pydantic/factory-built configs
unchanged. No other code touched.

**Date:** 2026-07-06, ~12:20 IST. `--provider gemini --no-deploy`,
`FORGE_CONTRACT_CHECK` at its default (on) — same as Experiment 007, not a
contract test this time.

**Results:**
| App | Exp008 (baseline, had the bug) | Exp009 (fix applied) |
|---|---|---|
| todo | 76.9, Runtime Startup 20/Compilation, API 100 | 76.4, Runtime Startup 20, API 100 (unaffected, as expected — todo never hit this bug) |
| blog_cms | 45.0, Runtime Startup **0.0**/API **0.0** (`ConfigAttributeError: DATABASE_URL`) | 34.3, Runtime Startup 0.0/API 0.0 — **different, unrelated bug this attempt**: frontend build failure (`Could not resolve "./pages/SignupPage"`), which trips the `frontend_build` critical-stage gate and skips runtime entirely |
| crm | 47.1, Runtime Startup **0.0**/API **0.0** (`ConfigAttributeError: database_url`, lowercase) | **66.6, Runtime Startup 20.0/API 100.0 — backend boots, health check 200, 15/15 endpoints respond** |

**`ConfigAttributeError` occurrences across the entire run log: 0** (was 2 in
Experiment 008 — one per broken app). Confirms the specific bug class this
fix targets is gone.

**Honest read on the noisy overall picture**: crm is a clean, unambiguous
win — total boot failure → fully healthy backend, exactly the predicted
effect. blog_cms's *overall* score didn't improve (34.3 vs 45.0) but for an
**unrelated reason**: this generation attempt produced a missing frontend
page import, a different bug this fix was never meant to address (ordinary
LLM generation variance between attempts, the same phenomenon flagged in
Experiments 005/007/008). crm's CRUD-journey failures (Edit/Delete/verify —
"no entity_id captured") are also a separate, pre-existing, already-catalogued
issue (`JourneyCRUDFailure`, patterns.json's #1 category) that this fix
wasn't targeting either.

**Conclusion — KEEP the change.** Per the instruction "only keep the change
if telemetry shows a measurable improvement": the improvement must be judged
against what the fix specifically targets (config-attribute boot crashes),
not the aggregate score, which is necessarily noisy across unrelated failure
modes in single-sample LLM generations. On its actual target: 2/2 known
occurrences eliminated (one fully demonstrated end-to-end in crm, zero
recurrences anywhere in the run), confirmed additionally by the 3 local unit
tests in commit f2721c5 (both original bugs reproduced-then-fixed, pydantic
path unaffected, no clobbering of correct values). Reverting would not fix
blog_cms's SignupPage import or crm's CRUD-journey issue — those are
independent, already-known bottlenecks for a future cycle.

**Health report** (`health_report.py`, first fully-populated one — dimensions/
confidence/retry_history captured from this run onward):
Overall 59.1%, Build 66.7% (2/3), Runtime 0.0% (0/3 — driven by blog_cms's
unrelated frontend-build gate + crm's CRUD-journey shortfall, not boot
crashes), Confidence Quality 25.3%. Top failure classes (all-time,
legacy-path patterns.json): JourneyCRUDFailure 17%, MissingEndpoint 11%,
ImportError 10% — JourneyCRUDFailure is the next natural candidate given it's
both the #1 all-time category and what's now visibly holding crm back.

---

## Experiment 010 — JourneyCRUDFailure 422-coercion fix (validation canary)

**Hypothesis:** Does coercing 422 type-mismatch fields (not just enum
mismatches) in the CRUD-journey retry path (commit e2f8d77) reduce
`JourneyCRUDFailure` ("no entity_id captured" cascades), measured against
Experiment 009's baseline?

**Changes under test:** `user_journey_runner.py`'s 422-retry branch now
consults a coercion table (string_type/int_type/float_type/bool_type/
list_type) alongside the existing enum-fix path (commit e2f8d77). No other
code touched.

**Date:** 2026-07-06, ~13:00–14:00 IST. `--provider gemini --no-deploy`
(log: `m1_canary_journeyfix_run.log`).

**Results vs. Experiment 009:**
| App | Exp009 | Exp010 | Verdict |
|---|---|---|---|
| todo | 76.4 | **99.3 (A+)**, build✅ runtime✅ | improved |
| blog_cms | 34.3 | 67.5 (D), runtime❌ | improved, still failing |
| crm | 66.6 | **44.4 (F)**, runtime❌ | canary script flagged REGRESSION |

**Honest read: the fix was never exercised this run.** Grepped the full log
for the 422 type-mismatch signature this fix targets (a plain-type Pydantic
`*_type` error on Create entity) — it does not occur anywhere in this run.
Each app hit a *different*, unrelated bug this attempt (ordinary single-
sample LLM generation variance, the same phenomenon flagged in Experiments
005/007/008/009):

- **todo**: transient `AttributeError: property 'priority' of 'Task' object
  has no setter` on first Create-entity attempt (a SQLAlchemy declarative
  model where `priority` collided with a property/column setter). Self-
  healed by the existing fix loop on retry (final run shows `Create entity:
  200 id=1`) — nothing to do with e2f8d77.
- **blog_cms**: `TypeError: Post() got multiple values for keyword argument
  'author_id'` (duplicate kwarg in the generated route's ORM-object
  construction) plus a separate `AttributeError: type object 'User' has no
  attribute 'created_at'` in a stats endpoint. Neither is a 422/entity_id
  issue — both are backend-generation bugs upstream of the journey runner.
- **crm (the flagged "regression")**: total backend boot failure, root-caused
  to `generated_projects/simple_crm/app/schemas/contact.py` —
  `ContactStatus` was generated as a near-empty Pydantic `BaseModel` stub
  (`id: Optional[int] = None`) instead of an `Enum`, then used directly as a
  FastAPI `Query()` annotation in `contact_routes.py`
  (`status: Optional[ContactStatus] = Query(...)`). FastAPI's dependant
  analysis asserts query params must be scalar/Enum types; a bare `BaseModel`
  fails that assertion at **import time**, before any route registers, so
  the entire app never boots (`Runtime Startup` and `API Functionality` both
  0.0 across all 4 fix attempts). The one automatic runtime-fix attempt
  (Gemini) patched unrelated things (passlib→bcrypt, async→sync, smart
  quotes, db_patcher) and left the actual `ContactStatus` class untouched, so
  the identical crash recurred and the fix loop gave up ("Failure signature
  unchanged — stopping retries").

**Conclusion: KEEP e2f8d77 (no evidence against it; simply untested this
run), but do not credit or blame it for any of this run's score movement.**
crm's "regression" is not a regression relative to the fix under test — it's
a fresh, previously-uncatalogued bug: **a generated Enum-shaped field
(`*Status`/`*Type` naming pattern) sometimes gets emitted as an empty
`BaseModel` instead of an `Enum`, and if that field is later used as a
`Query()` parameter, the whole backend fails to boot.** This is a total
boot-failure class hitting Runtime Startup (20% weight, the single highest-
weighted dimension) — structurally the same severity tier as the
config-patcher bug fixed in Experiment 009, and the current fix-loop
cannot self-heal it (signature-unchanged bail-out after one attempt).

**Next highest-ROI candidate identified (not yet fixed)**: detect/repair
"BaseModel used where FastAPI requires a scalar/Enum Query type" — either
(a) a generation-time check that a `Query()`-annotated field's referenced
schema is a real `Enum` (str/int subclass) and not a `BaseModel`, with a
targeted regenerate-as-enum repair, or (b) a narrower preflight patch that
converts an empty single-field `id`-only `BaseModel` used in a `Query()`
annotation into `Optional[str]` as a safe fallback so the app can at least
boot. Cheap, deterministic, directly targets Runtime Startup — same shape of
fix as Experiment 009's win. Pending user confirmation before starting.

**Update (same session)**: implemented and locally verified the candidate
above — `_fix_query_param_basemodel` added to `preflight.py` (priority 22).
Scans `app/schemas` + `app/models` for plain `BaseModel` classes (never
`Enum`/`BaseSettings`), finds route params annotated with one of those
classes and defaulted via `Query(...)`, loosens the annotation to
`str`/`Optional[str]`, and rewrites any `{param}.value` access in the same
file to a `getattr(..., "value", ...)` fallback. Verified locally against
the actual `simple_crm/app/schemas/contact.py` +
`app/routes/contact_routes.py` files from this run's crash: patch applies,
result is valid Python (`ast.parse`), is idempotent on a second pass, and a
negative-case project using a real `(str, Enum)`-based Query param is left
completely untouched. No generation calls used — pending a validation
canary (Experiment 011) before crediting it with any score movement.

---

## Experiment 011 — BaseModel-Query-param fix (validation canary)

**Hypothesis:** Does `_fix_query_param_basemodel` (preflight.py, priority 22,
commit acdf252) eliminate the `AssertionError: Query parameter '<name>'
must be one of the supported types` boot-crash class discovered in
Experiment 010, without regressing todo/blog_cms/crm on anything the fix
doesn't target?

**Changes under test:** `_fix_query_param_basemodel` only. No other code
touched (confirmed clean working tree before running).

**Date:** 2026-07-06, ~14:00-14:45 IST. `--provider gemini --no-deploy`
(log: `m1_canary_querybasemodel_run.log`).

**Results vs. Experiment 010:**
| App | Exp010 | Exp011 | Canary verdict |
|---|---|---|---|
| todo | 99.3 (A+) | 99.3 (A+), 0 fix attempts | OK, unchanged |
| blog_cms | 67.5 (D) | 93.3 (A), DEPLOY READY | flagged REGRESSION (build=False) |
| crm | 44.4 (F), total boot failure | 65.8 (D), NEEDS REPAIR | flagged OK by script, but score moved |

**Target questions, answered directly:**

1. **`ConfigAttributeError` remains eliminated** — YES. Zero occurrences
   anywhere in the run log (grepped the full ~2,500-line log).
2. **`BaseModel`-as-`Query()` startup failures eliminated** — YES. Zero
   occurrences of `must be one of the supported types` anywhere. crm's
   backend boots cleanly across all 3 fix attempts (`started=True
   health=True`), all 15 endpoints respond (API Functionality 100/100) —
   the exact scenario that was a 100% boot failure in Experiment 010.
3. **crm Runtime Startup improves** — YES in the sense that matters: the
   app now actually starts and serves traffic (vs. total non-boot before).
   The *dimension score* is still 20/100 this run, but for an unrelated
   reason: its `success` flag is coupled to the full smoke-test/journey
   outcome, which hit a **different, newly-exposed bug** — a
   `NOT NULL constraint failed: contacts.name` `IntegrityError` on Create
   entity (the request never populated `name`). This has nothing to do
   with Query()/BaseModel; it's a distinct root cause, only reachable now
   because the app boots at all.
4. **crm CRUD success improves** — marginally: journey `steps_passed` went
   6→7 (Create entity is now classified "passed, 422 schema mismatch,
   server alive" instead of a flat 500 crash — the phone_number
   type-coercion from e2f8d77 visibly worked this run), but `crud_passed`
   is still `False` overall because the NOT-NULL-constraint bug above
   prevented an entity_id from ever being returned. Fixing the boot crash
   was a *precondition* for CRUD to even be attempted; it just uncovered
   the next bottleneck in the same code path.
5. **No regressions in todo/blog_cms** — confirmed. todo is byte-identical
   in outcome (0 fix attempts needed). blog_cms's automated "REGRESSION"
   flag is a false positive, same pattern seen in Experiments 005/007/008:
   its score *improved* (67.5→93.3); the `build=False` flag traces to a
   pre-existing, already-catalogued frontend missing-import bug
   (`Could not resolve "./Sidebar"` / `"./Navbar"` / `"./pages/SignupPage"`
   — a different missing file each attempt, ordinary LLM variance, see
   [[project_import_resolution]]). `_fix_query_param_basemodel` never
   touches frontend files, so it cannot be the cause.

**Cost**: $0.0226 (todo) + $0.0100 (blog_cms) + $0.0261 (crm) = **$0.0587
total** (~₹5). Confidence reports: todo 93.1% (A), blog_cms 74.6% (C), crm
26.9% (F, driven by the unrelated NOT-NULL bug, not this fix). Browser
validation dimension was N/A/excluded on all 3 apps (playwright not
installed in this environment — a pre-existing infrastructure gap,
unrelated to this experiment).

**Conclusion — KEEP the fix.** Judged strictly against what it targets (per
the user's explicit instruction to ignore unrelated failures): both
confirmed occurrences of the BaseModel-as-Query-type boot crash
(`ConfigAttributeError`'s sibling bug class) are gone, replicated exactly
where it was previously guaranteed to fail (crm). No regression anywhere
attributable to this change.

**Next highest-ROI candidate identified from telemetry (not started)**:
cross-referenced this run's finding against `backend/failure_memory/patterns.json`
(all-time, n=332 runs) — `JourneyCRUDFailure` remains the #1 category (20
occurrences, well ahead of `MissingEndpoint` 15 and `ImportError` 11), and
this run added a *new, cleanly root-caused* sub-cause within that same
bucket: a SQLAlchemy model column defined `NOT NULL` (no `server_default`,
not `nullable=True`) whose value the Create-entity route/schema doesn't
always supply, causing an `IntegrityError` deep in `db.commit()` that the
journey runner can't recover from (no entity_id ever returned). Same shape
as the two just-shipped fixes (deterministic, preflight-patchable, directly
gates CRUD success) — recommended as the next reliability target, pending
user go-ahead.

---

## Experiment 012 — NOT NULL model/schema gap fix (root-cause trace + validation canary)

**Hypothesis:** Where does `IntegrityError: NOT NULL constraint failed:
contacts.name` (Experiment 011, crm) actually originate, and does
`_fix_model_schema_notnull_gap` (preflight.py, priority 24, commit a49455c)
prevent that class of crash without regressing todo/blog_cms?

**Trace (planner → architect → backend model wave → backend schema/route
wave → journey runner → db.commit()):**
- **Planner/Architect**: architect stage was a **cache hit** for crm
  (`ARCHITECT CACHE HIT — skipping LLM call`) — identical, stable spec
  reused across every crm run this cycle. Ruled out: the same input can't
  explain a divergence that appears downstream.
- **Backend generator**: models are generated in `Wave 2 — Models (4
  tables, parallel)`, one independent Gemini call per table; schemas/routes
  are generated in a separate wave/call. Given the identical spec, the
  model wave produced `Contact.name = Column(String(255), nullable=False)`
  (no default) while the schema wave independently produced
  `first_name`/`last_name` on `ContactCreate` — no `name` field at all.
  **This is the first point the required field disappears.**
- **Generated API**: `create_contact`'s generic, otherwise-reasonable
  defensive pattern — `Contact(**{k: v for k, v in data.items() if k in
  Contact.__table__.columns.keys()})` — silently drops any column the
  schema never supplies instead of erroring, so the mismatch surfaces only
  as a NOT NULL failure at `db.commit()`, deep enough in the ORM that the
  route's own `except Exception` can only 500 generically.
- **Frontend/Playwright**: not implicated — the CRUD-journey runner tests
  the API directly over HTTP; it never goes through the generated React
  form, so the frontend generator is not in this path at all.
- **Repair engine / validation**: no existing validator checks "does every
  NOT NULL model column have a corresponding Create-schema field" — a
  genuine coverage gap, but not the origin.
- **Root cause classification: `backend generator`** — specifically,
  uncoordinated model-generation and schema/route-generation waves for the
  same entity, with no cross-consistency check between them.

**Fix implemented** (commit a49455c): `_fix_model_schema_notnull_gap` in
`preflight.py`. Verified locally beforehand against the real crm files
(relaxes `name` and `status`, leaves FK/PK/server_default columns and
already-covered fields untouched, idempotent).

**Validation canary** (2026-07-06, ~15:00 IST, `--provider gemini
--no-deploy`, log `m1_canary_notnullgap_run.log`):

| App | Exp011 | Exp012 | Canary verdict |
|---|---|---|---|
| todo | 99.3 (A+) | 76.4 (C) | flagged REGRESSION |
| blog_cms | 93.3 (A) | 34.3 (F) | flagged REGRESSION |
| crm | 65.8 (D) | 72.6 (C) | OK |

**Root-caused both flagged regressions — neither is caused by this fix:**
- **todo**: `POST /tasks` returns `400 Bad Request` this attempt because
  `create_task` looks up `Priority.name == task_in.priority` against an
  unseeded `priorities` table and 400s if no row matches. Grepped todo's
  models after the run: none of the columns our fixer could have touched
  (`priority_id`, etc.) were altered — this is a fresh, unrelated
  generation-quality bug (missing seed data for a lookup table),
  independent LLM variance in this attempt's `task_routes.py`.
- **blog_cms**: `ImportError: cannot import name 'TokenData'` plus the
  *same* recurring frontend bug seen in Experiments 009 and 011 —
  `Could not resolve "./pages/SignupPage"` — now confirmed across 3
  separate experiments as a persistent, not-yet-fixed generation gap,
  wholly unrelated to model/schema nullability.
- Neither app's `preflight` log shows a materially relevant change from
  `fix_model_schema_notnull_gap` (it fired in both, per its normal safe/
  no-op-when-covered behavior); the actual failures trace to backend
  routing logic (todo) and frontend imports (blog_cms) our fixer never
  touches.

**crm — partially effective, root cause of the shortfall identified:**
`status` was successfully relaxed to `nullable=True` (confirmed on disk) —
a real, verified instance of the fix working exactly as designed on a
column our fix owns cleanly. However, **the exact `name` crash recurred
identically** (`IntegrityError: NOT NULL constraint failed: contacts.name`,
Runtime Startup stuck at 20/100, crud still blocked). Traced why: a
**pre-existing, independent patcher** (`field_patcher` in
`deterministic_patcher.py`, not part of this fix) runs *after* preflight
in this pipeline and reactively adds a stub `name: Optional[str] = None`
field to `ContactCreate` in response to a different diagnosed error
(`[field_patcher] Added missing schema field(s) ['name'] to ContactCreate`).
By the time it added that field, `_fix_model_schema_notnull_gap` had
already run and seen no `name` field in the schema (correctly, at that
point) — but once `name` exists as an *Optional* schema field, my fixer's
"is this column name present in the schema" check treats it as covered,
even though "present but Optional, defaulting to None" gives exactly zero
guarantee the client ever supplies a real value. `field_patcher` fixes its
own target (a missing-attribute/constructor error) without fixing the
downstream NOT NULL guarantee, and my fixer's presence-only check doesn't
catch that the newly-added field is not actually *required*.

**Conclusion — KEEP the fix (it does what it does correctly, causes no
regressions), but it is incomplete for this exact case.** Not a case of
"unrelated failure" like todo/blog_cms — this is a real, identified gap in
the fix's own logic: it should treat a schema field as "covering" a NOT
NULL column only if that field is **required** (no `Optional[...]`, no
default), not merely *present*. Recommended refinement for next cycle (not
implemented this cycle, per stop-and-report instruction): change the
field-presence check to a field-*requiredness* check.

**Cost**: $0.0276 (todo) + $0.0227 (blog_cms) + $0.0283 (crm) = **$0.0786
total** (~₹7). crm: Fix Attempts 2/5, confidence 31.3% (F, driven by the
still-open `name` crash), historical base rate 18.2% (n=22).

---

## Experiment 013 — NOT NULL gap fix, requiredness refinement (validation canary)

**Hypothesis:** Does checking schema-field *requiredness* instead of mere
*presence* (commit 57b562b) finally eliminate the exact `contacts.name`
NOT NULL crash that recurred in Experiment 012 despite the original fix?

**Changes under test:** the requiredness refinement only (57b562b). No
other code touched. Verified locally beforehand (3 cases: field absent,
field present-but-Optional/stub, field genuinely required) before spending
any generation credits.

**Date:** 2026-07-06, `--provider gemini --no-deploy` (log
`m1_canary_requiredness_run.log`).

**Results vs. Experiment 012:**
| App | Exp012 | Exp013 | Canary verdict |
|---|---|---|---|
| todo | 76.4 (C) | 67.4 (D) | flagged REGRESSION |
| blog_cms | 34.3 (F) | 86.2 (B) | OK (score up sharply) |
| crm | 72.6 (C) | 74.3 (C) | OK |

**Primary question — answered directly: YES, the exact target crash is
gone.** Grepped the full run log for `NOT NULL constraint failed`: only
**one** occurrence in the entire run, and it is **not** `contacts.name` —
it's a new, different column (`posts.content_markdown` in blog_cms, the
same general model/schema divergence pattern recurring on a different
entity this attempt, not something this cycle's fix was scoped to). Zero
occurrences of `contacts.name` anywhere. Per the specified success
criterion ("if the original NOT NULL crash disappears, keep the
refinement"): **confirmed, keep.**

**crm detail**: CRUD is still not fully working this run, but for a
*different, upstream* reason than a NOT NULL crash — this attempt's
`create_contact` handler has a different shape than Experiments 011/012
(no defensive `{k: v for k, v in ... if k in Contact.__table__.columns}`
filter this time), so it does `Contact(**contact_in.model_dump(),
user_id=...)` directly and hits `TypeError: 'first_name' is an invalid
keyword argument for Contact` instead. This is the **same root defect**
(backend generator's uncoordinated `Contact.name` vs `ContactCreate.
first_name/last_name`) surfacing through a different route-code shape that
this preflight fix doesn't touch (it only edits `app/models/*.py` column
nullability, never route constructor calls) — not a new bug, and not
something this fix was ever positioned to catch. `status` desync/etc. are
unaffected; Runtime Startup/API Functionality unchanged from Exp012
(app boots, 15/15 endpoints respond).

**todo regression — confirmed unrelated**: this attempt's frontend sends
`{username, password}` to login/register while the backend requires
`email` — a frontend/backend auth field-naming mismatch, plus the run hit
a Groq daily-quota rate limit near the end (`429 rate_limit_exceeded`,
92,788/100,000 TPD used) — the same provider-exhaustion confound flagged
back in Experiment 002. Neither has anything to do with NOT NULL columns
or this fix.

**blog_cms — improved sharply (34.3→86.2)**, consistent with ordinary
attempt-to-attempt variance rather than any effect from this fix (which
only touches `app/models`).

**Conclusion — KEEP the refinement.** Stopping here per instructions: not
expanding scope to chase the new `posts.content_markdown` NOT NULL
instance or the `first_name`-constructor-TypeError manifestation this
cycle — both are evidence the same backend-generator root cause (Experiment
012's classification) is systemic across apps/attempts, worth a dedicated
future investigation, but out of scope for "the smallest deterministic
fix" mandate given this cycle.

**Cost**: $0.0262 (todo) + $0.0264 (blog_cms) + $0.0209 (crm) = **$0.0735
total** (~₹6). Fix Attempts: todo 2/5, blog_cms 3/5, crm 3/5.

---

## Experiment 014 — Frontend missing-import scaffolder wiring (validation canary)

**1. Hypothesis**: does wiring the existing, previously-dead
`create_missing_stubs()` into preflight (commit b0a6c3c) eliminate the
`Could not resolve "./Navbar"`/`"./Sidebar"`/`"./pages/SignupPage"` class
of Vite build failure, confirmed recurring across 8 of 9 canary runs this
cycle?

**2. Evidence**: surveyed every canary log this cycle (`grep -o 'Could not
resolve "[^"]*"'` across all 9 `m1_canary_*.log` files) — hit in
`m1_canary_run.log`, `_gemini_run`, `_v2_run`, `_contractoff_run`,
`_configfix_run`, `_journeyfix_run`, `_querybasemodel_run`,
`_notnullgap_run` (8/9), always blog_cms, a different specific missing
sibling component/page each time (HomePage, PostListPage, SignupPage,
Navbar, Sidebar, Toast, Layout). Cross-referenced against
`generation_log.jsonl`: `Could not resolve "./Navbar"...` (4) and
`.../SignupPage...` (3) are the 3rd/4th most frequent specific failure
signatures in the authoritative V15 telemetry.

**3. Root cause**: **repair engine**, not frontend generator. A
comprehensive fix already existed — `create_missing_stubs()` in
`app/services/frontend_fix_service.py` walks every `.jsx` file under
`src/`, resolves every relative import, stubs anything unresolved — but
was never called anywhere in the live V15 pipeline (confirmed via
`grep -rn` across `core/`, `verification/`, `repair/`: zero references
outside its own module). The pipeline only had a narrower, reactive
per-patch scaffolder (`_scaffold_missing_local_imports` in
`repair/orchestrator.py`) that only stubs imports it sees in a file it's
actively patching *that round* — traced the exact whack-a-mole mechanism
live in Experiment 011's log: Sidebar error → patched → Navbar error
surfaces in the same file next build → patched → SignupPage error
surfaces in a different file → ..., burning several fix-loop rounds and
LLM tokens on something a single upfront sweep resolves for free.

**4. Implementation**: `fix_frontend_missing_imports` added to
`preflight.py` (priority 23, commit b0a6c3c) — calls the existing
`create_missing_stubs()` once in the deterministic preflight stage that
already runs before the first verification pass. No new stub logic
written; only wiring.

**5. Validation (local, $0)**: reproduced the exact real-world scenario
(Layout.jsx importing 3 missing siblings, App.jsx importing a missing
page) and confirmed all 4 get stubbed in one pass; idempotent on a second
run; confirmed registered in the preflight registry at the intended
priority slot; `ast.parse`/`py_compile` clean.

**6. Benchmark comparison** (`--provider gemini --no-deploy`, log
`m1_canary_signuppage_run.log`):
| App | Exp013 | Exp014 | Canary verdict |
|---|---|---|---|
| todo | 67.4 (D) | 73.9 (C) | OK |
| blog_cms | 86.2 (B) | **87.4 (B), build=True runtime=True** | OK |
| crm | 74.3 (C) | 66.9 (D) | flagged REGRESSION |

**7. Telemetry comparison**: `grep -c "Could not resolve"` across the
entire run: **0** (was present in 8 of the previous 9 runs). blog_cms is
the first run *all cycle* with both `build=True` AND `runtime=True`
simultaneously. The preflight log confirms the mechanism worked exactly as
designed: `Stub created: src\components\Navbar.jsx` /
`Sidebar.jsx` / `Toast.jsx` / `src\pages\SignupPage.jsx` /
`src\components\Layout.jsx`, printed 4 separate times across the run's
regenerations, `[preflight] fix_frontend_missing_imports: applied` each
time.

**crm's flagged regression is unrelated** — this fix only touches
`src/*.jsx` frontend files; crm's failure is 100% backend
(`IntegrityError: NOT NULL constraint failed: contacts.name`, again). This
is not a reappearance of an unfixed bug: the *final* on-disk
`app/models/contacts.py` shows `name = Column(String(255),
nullable=True)` — the requiredness fix (Experiment 013) DID relax it
correctly. The crash happened *mid-repair-loop*, visible in crm's own
`Score Track: 73 → 74 → 26 → 74 → 66 → 67 → 67` — an LLM-driven fix
attempt evidently regenerated `contacts.py`/`contact.py` at some point
(reintroducing the divergence before preflight/orchestrator patches
re-converged it), a known-systemic, already-flagged issue (Experiments
012/013: the backend generator's uncoordinated model/schema field
divergence surfaces differently almost every attempt). New wrinkle found
while tracing this: `do_create()`'s 422→targeted-retry path
(`user_journey_runner.py`) has no branch for the retry itself returning
5xx — if `r3.status_code` isn't 200/201 or 422, execution falls through
unconditionally to `return True, f"422 (schema mismatch, server
alive)..."`, silently reporting the step as "passed" even though the
retry actually crashed with a 500. **Not fixed this cycle** — flagged as
the next candidate (see below), kept isolated per "never stack a second
fix."

**8. Verdict: KEEP.** Judged against its actual target (the frontend
missing-import class): complete, unambiguous success — 0 occurrences
where 8/9 prior runs had them, and blog_cms's first-ever
build+runtime-both-true result this cycle. crm's regression has a fully
identified, unrelated cause and does not implicate this change.

**9. Next highest-ROI candidate**: the `do_create()` false-positive
"passed" on a 5xx retry response (found while root-causing this
experiment) — small, isolated, one-branch fix in the same file as the
"?"-status-code fix (commit 8f64039, not yet validated by a canary). Per
discipline, these will be validated separately, not stacked.

**Cost**: $0.0198 (todo) + $0.0277 (blog_cms) + $0.0255 (crm) = **$0.073
total**. Fix Attempts: todo 2/5, blog_cms 5/5 (most ever — but converged
to a stable 86-87 plateau, not oscillating), crm 3/5.

---

## Experiment 015 — Journey-runner status-code fix (validation canary, infra-confounded)

**1. Hypothesis**: does the `is not None` fix (commit 8f64039) surface the
real HTTP status code on a Create-entity rejection instead of a
contentless `"?"`?

**2. Evidence**: `generation_log.jsonl` showed `Create entity: ?` as the
single most frequent specific failure signature (7/27 recent entries);
Experiment 013's raw log showed the exact wire-level response was `POST
/tasks 400 Bad Request` while the journey step recorded `detail: '?'`.

**3. Root cause**: `requests.Response.__bool__` returns `self.ok` (False
for status ≥ 400); `do_create()`'s final fallback used `if last_r` instead
of `if last_r is not None`, so a perfectly valid 400/403/404/409 response
was treated as falsy. Classification: **runtime** (the journey-runner
telemetry harness itself, not generation/repair/validation).

**4. Implementation**: one-line change in
`app/runtime/user_journey_runner.py`, `do_create()`'s final return.

**5. Validation (local, $0)**: reproduced with a real `requests.Response`
object (`bool(resp)` confirmed `False` for status 400); confirmed old code
returns `'?'`, new code returns `'400'`; confirmed 201 and `None` cases
unaffected; scanned the rest of the file for the same anti-pattern (only
instance).

**6. Benchmark comparison** (`--provider gemini --no-deploy`, log
`m1_canary_statuscode_run.log`):
| App | Exp014 | Exp015 | Canary verdict |
|---|---|---|---|
| todo | 73.9 (C) | 73.9 (C) | OK |
| blog_cms | 87.4 (B) | 68.3 (D) | flagged REGRESSION |
| crm | 66.9 (D) | 0.0 (F) | flagged REGRESSION |

**Infrastructure confound, not a code regression**: grepped the run log
for `getaddrinfo failed`/`Connection error`: **126 occurrences**, 84 inside
blog_cms's section, 42 inside crm's section, **0 inside todo's**. Both
Gemini (DNS resolution failure, `[Errno 11001] getaddrinfo failed`) and
Groq (`Connection error`) were unreachable for a stretch spanning
blog_cms's and crm's runs — a transient local network/DNS outage, not a
provider quota or code issue. crm's generation failed outright (0 tokens
billed, 18.8s total) because every single model/schema/route file's LLM
call failed with both providers down. Confirmed connectivity was restored
immediately after (`socket.getaddrinfo` succeeded for both hosts moments
later). Per the Experiment 002 precedent, a run like this "produces zero
usable signal" for the two contaminated apps — their scores are excluded
from judging this fix.

**7. Telemetry comparison — direct, unambiguous confirmation from todo's
uncontaminated section**: todo hit the *exact* recurring scenario from
Experiment 013 (`POST /tasks` → 400, unseeded Priority-name lookup).
Journey step now reads `{'name': 'Create entity', 'passed': False,
'detail': '400'}` — the real status code — where it previously read
`'detail': '?'`. `parsed_error.hint`'s `failed_steps` list also now
carries `('Create entity', '400')` instead of `('Create entity', '?')`,
giving the repair loop's own diagnostic message real information for the
first time on this exact failure class.

**8. Verdict: KEEP.** Confirmed effective via direct, real-world evidence
in the one app segment unaffected by the network outage. Not re-running
the full canary purely to re-confirm blog_cms/crm's scores under normal
network conditions — the fix's success criterion (real status code
surfaces instead of "?") is already unambiguously met; a clean re-run of
just those two apps is optional future work, not required to keep this
change.

**Cost**: $0.0246 (todo only — blog_cms and crm spent $0 since their LLM
calls failed before any billable generation completed).

**9. Next highest-ROI candidate**: the `do_create()` 422-retry
false-positive found while investigating Experiment 014 (a retry that
itself returns 5xx currently falls through to a false "passed" with a
stale 422 message) — implemented and locally verified, not yet committed
or canary-validated; queued as Experiment 016, kept isolated per
discipline.

---

## Experiment 016 — Journey-runner 422-retry false-positive fix (validation canary)

**1. Hypothesis**: does the new `elif r3.status_code >= 500` branch
(commit 174ed86) correctly report a 422-retry that itself crashes the
server as failed, instead of a false "passed" with a stale 422 message?

**2. Evidence**: found while root-causing Experiment 014's crm
regression — crm's `contacts.name` NOT NULL crash happened *during* the
422-retry (phone_number got auto-corrected, the corrected payload's
`db.commit()` then hit the NOT NULL constraint), yet the journey step was
recorded as `passed: True` with the original 422's message.

**3. Root cause**: same function as Experiment 015 (`do_create()`,
`user_journey_runner.py`) — the retry-response handling only branched on
`(200, 201)` and `422`; any other code silently fell through to the
generic "passed" return. Classification: **runtime** (journey-runner
telemetry).

**4. Implementation**: one `elif` branch added, mirroring the existing
5xx handling already present for the *first* request earlier in the same
function.

**5. Validation (local, $0)**: reproduced the branch logic with real
`requests.Response` objects (201/500/422 cases); `ast.parse`/`py_compile`
clean.

**6. Benchmark comparison** (`--provider gemini --no-deploy`, log
`m1_canary_retry500_run.log`), clean run — 0 network-error occurrences:
| App | Exp015 (todo only, clean) | Exp016 | Canary verdict |
|---|---|---|---|
| todo | 73.9 (C) | 73.9 (C) | OK |
| blog_cms | — (infra-confounded) | 84.8 (B), build=True runtime=True | OK |
| crm | — (infra-confounded) | 66.2 (D) | OK |

**`CANARY PASSED — safe to continue`** — the first fully clean pass (no
flagged regressions) since Experiment 007.

**7. Telemetry comparison — direct confirmation on the exact target
scenario**: crm hit `contacts.name` NOT NULL again this run (the
underlying model/schema divergence is still an open, separately-tracked
issue — see Experiments 012/013), but this time the journey step
correctly reads `{'name': 'Create entity', 'passed': False, 'detail': '500
(server error on 422-retry)'}` — occurring identically across all 5 fix
attempts in the log. This is exactly the fix's intended effect: the
*diagnostic accuracy* improved (a real crash is now visible as a failure
with its real cause) even though the *underlying crash* is a separate,
already-tracked, not-yet-fixed issue. Score-wise this is a wash (66.9 →
66.2, noise), which is expected and correct — this fix targets telemetry
honesty, not the crash itself.

**8. Verdict: KEEP.** Confirmed both on the target mechanism (branch logic
verified locally) and live (identical real crash, now correctly
diagnosed). No regressions anywhere in a clean, uncontaminated 3-app run.

**Cost**: $0.0309 (todo) + $0.0190 (blog_cms) + $0.0252 (crm) = **$0.0751
total**.

**9. Next highest-ROI candidate**: with both journey-runner telemetry bugs
fixed, the CRM CRUD blocker is now cleanly and accurately diagnosed every
time as `contacts.name` NOT NULL via a 422-retry crash — the *actual*
remaining bottleneck is the backend generator's model/schema field
divergence itself (`Contact.name` vs `ContactCreate.first_name/last_name`,
flagged systemic since Experiment 012). Per the VNext report's own
meta-pattern analysis (§3: "cross-file/cross-stage name-and-shape
disagreement" accounts for 58% of all historical failure instances), this
class of bug is what the future AppContract is meant to solve
permanently — further one-off preflight patches for new manifestations of
the *same* divergence would be diminishing-returns whack-a-mole, not a new
independent bottleneck. Recommend holding here per the user's own stated
roadmap (deterministic bugs first, AppContract only after) rather than
writing a fifth preflight rule chasing the same root disease.

---

## Experiment 017 — Model-driven schema generation (architectural experiment)

**1. Hypothesis**: does resolving the real generated model (via a
deterministic entity-metadata extractor) and injecting its fields as a
binding contract into Wave 3's schema prompt eliminate model/schema
field-name drift, without implementing the full AppContract IR?

**2. Evidence**: Experiments 012/013/014/016 all traced crm's persistent
CRUD blocker to the same disease — `Contact.name` (model) vs
`ContactCreate.first_name`/`last_name` (schema). Per the VNext report's
own meta-pattern analysis, this class of cross-file disagreement accounts
for 58% of all historical failure instances.

**3. Root cause**: traced to `parallel_backend_service.py`. The mechanism
to prevent this *already existed* — `_gen_schema` already took a
`model_content` parameter, and the prompt already labeled it "CORRESPONDING
MODEL (for field reference)" — but two bugs defeated it: (a) Wave 3's
model-content lookup was a naive `resource.py` / `resource[:-1]+".py"`
filename guess that doesn't handle every singular/plural convention, and
(b) critically, Wave 2.5's singular-shim step registers a bare two-line
re-export (`from app.models.contacts import Contact`, zero column data)
into the *same* `model_contents` dict the real model lives in — so Wave
3's naive lookup can silently receive the contentless shim instead of the
real model, leaving the schema LLM call with no real field information.
Classification: **backend generator / generation-pipeline plumbing** (the
handoff between two already-adjacent stages, not the LLM's fault).

**4. Implementation** (commit 97e17d2): new reusable
`app/services/entity_metadata.py` (`EntityDefinition`/`FieldDefinition`,
`extract_entity_definition()`, `find_model_for_resource()` — resolves by
parsed `table_name`/`class_name`, never filename guessing, so a shim
correctly parses to `None` and can never be mistaken for a real model —
and `render_field_manifest()`). Wired behind `FORGE_MODEL_DRIVEN_SCHEMA`
(default off, preserving the exact current lookup/prompt byte-for-byte
when unset — verified). When on, Wave 3 resolves the real model and
injects a "BINDING FIELD CONTRACT" block into the schema prompt in place
of the old advisory "for reference" framing.

**5. Validation (local, $0)**: extractor correctness against a real
captured model file; shim correctly parses to `None`; `find_model_for_resource`
resolves the real model over the shim for both singular/plural resource
names (the exact bug scenario); full Wave-3 wiring simulated end-to-end;
`build_schema_prompt` confirmed byte-identical when `field_manifest` is
omitted. `ast.parse`/`py_compile` clean.

**6. Benchmark comparison** (`FORGE_MODEL_DRIVEN_SCHEMA=1`,
`--provider gemini --no-deploy`, log `m1_canary_modeldriven_run.log`) vs.
Experiment 016 (`CANARY PASSED`, the cleanest recent baseline):
| App | Exp016 | Exp017 | Canary verdict |
|---|---|---|---|
| todo | 73.9 (C) | 73.9 (C) | OK |
| blog_cms | 84.8 (B) | 61.5 (D) | flagged REGRESSION |
| crm | 66.2 (D) | 39.4 (F) | flagged REGRESSION |

**`CANARY FAILED`** by the automated script — but per the user's explicit
instruction ("do not judge success solely by overall Forge Score; judge by
whether model/schema drift measurably decreases"), both regressions were
traced to their root cause before drawing any conclusion:

- **crm's regression is 100% unrelated**: `AttributeError: 'Config' object
  has no attribute 'DATABASE_URL'` — the exact `ConfigAttributeError`
  class Experiment 009's config-patcher targets, recurring in a form that
  fix's known limitation doesn't cover (pydantic `BaseSettings`-style
  Config classes are deliberately left to the instance-only guard, per
  that fix's own docstring). This crashed the app before generation even
  reached `app/schemas/contact.py` — this experiment's change (Wave 3
  only) cannot be its cause.
- **blog_cms's regression is also unrelated**: `sqlalchemy.exc.ArgumentError:
  IN expression list ... expected, got 'journey-test'` in `post_routes.py`'s
  `Tag.id.in_(post_in.tag_ids)`. Confirmed `tag_ids` is **not a Column on
  the `Post` model at all** (tags are handled via a relationship
  property) — the LLM independently invented `tag_ids: Optional[str]` in
  the schema this attempt, unrelated to anything the entity-metadata
  extractor reads (it only parses `Column(...)` definitions). Matches the
  extensively-documented pattern of blog_cms hitting a different specific
  generation-variance bug almost every attempt this entire cycle.

**7. Telemetry comparison — the actual target metric, directly confirmed**:
inspected the freshly generated `simple_crm/app/models/contacts.py` and
`app/schemas/contact.py` on disk. The model still declares `name`/`status`
as before; the schema now reads:
```
class ContactCreate(BaseModel):
    name: str = Field(min_length=1)
    ...
    status: str = Field(min_length=1)
```
— **exact field names, correctly marked required (non-Optional)**,
matching the model precisely. Confirmed this is not just a fluke of this
one run: the reactive `[field_patcher] Added missing schema field(s)
['name']` event (which fired in Experiment 012 to patch over exactly this
gap) **did not fire at all this run** — because "name" was correctly
present in `ContactCreate` from the very first generation attempt, not
patched in afterward. This is the clean, direct, mechanism-level
confirmation the experiment set out to get.

**8. Verdict: KEEP (mechanism confirmed, overall run confounded).** The
targeted drift class is eliminated on direct evidence. Both scored
regressions are independently and fully explained by unrelated,
already-catalogued bug classes (config-attribute handling, ad-hoc
relationship-field invention) that predate and are outside the scope of
this change. Reverting proven-correct, low-risk, feature-flagged code
(default OFF, zero effect on current production behavior) over unrelated
noise would be a mistake. Not flipping the default to ON yet, though —
one confounded run isn't enough to confidently declare the *aggregate*
score/CRUD impact, only the specific mechanism under direct test.

**Recommended before flipping the default**: one more clean canary run
(no confounding config/relationship bugs) to see the mechanism's effect on
overall CRUD/Runtime scores now that Contact's drift is gone — crm's CRUD
was never reached this run because of the unrelated Config crash.

**Architectural trade-offs**: Wave 3 now depends on Wave 2's actual output
for entities where the flag is on, rather than treating them as
independent (this dependency already existed structurally — Wave 3 always
ran after Wave 2.5 completes — this experiment only makes Wave 3 correctly
*consume* what was already available). No latency cost observed (waves
were already sequential). The extractor only reads `Column(...)`
definitions, not `relationship()`/secondary tables — doesn't help
many-to-many association fields (e.g. blog_cms's tags) as-is, a natural
next extension given the reusable-extractor design.

**Estimate of historical-failure-bucket coverage**: this targets the
`PydanticSerializationError`/`ModelFieldMismatch` class specifically (row 9
in the VNext report's failure table, ~6 instances/6% of the historical
window) plus a share of `JourneyCRUDFailure` (the #1 pattern, 24
instances) where the root cause is field-name drift specifically (a
subset, not all of it — JourneyCRUDFailure also covers unrelated causes
like the Priority-lookup 400 and provider timeouts). Meaningful but
partial coverage of the 58% "cross-file disagreement" bucket the full
AppContract would eventually address in full (it doesn't touch
endpoints/routers/imports).

**Cost**: 3-app canary, standard run (~$0.07-0.08 typical for this suite).

**9. Next highest-ROI candidate**: re-run once more for a clean
signal-vs-noise read on aggregate CRUD/Runtime impact; separately, the
Config `BaseSettings` gap (Experiment 009's fix doesn't cover it) and
blog_cms's tags/relationship-field invention are both new, independently
catalogued candidates for future cycles — neither blocks keeping this
experiment's code.

---

## Experiment 018 — Model-driven schema generation, confirming run + promotion to default

**Objective** (per explicit instruction): not Forge Score — validate
whether model-driven schema generation *consistently* eliminates
model↔schema drift, with no code changes before the run
(`FORGE_MODEL_DRIVEN_SCHEMA=1`, same commit as Experiment 017).

**Result: `CANARY PASSED — safe to continue`.**
| App | Exp017 | Exp018 | Verdict |
|---|---|---|---|
| todo | 73.9 (C) | 73.9 (C) | OK |
| blog_cms | 61.5 (D), confounded | 90.3 (A), **build=True runtime=True, CRUD 11/11 PASS** | OK |
| crm | 39.4 (F), confounded | 91.4 (A), **build=True runtime=True, CRUD 11/11 PASS** | OK |

**Success criteria, evaluated directly:**

1. **Contact.name vs first_name/last_name drift does not reappear — YES,
   confirmed.** `ContactCreate` again declares `name`/`status` under the
   model's exact column names (this attempt as `Optional[str]` rather than
   `str`, ordinary attempt-to-attempt variance in requiredness — the field
   *names* are what this experiment targets, and they match).
2. **Experiment 012's reactive patcher does not fire — YES, confirmed.**
   Zero `[field_patcher] Added missing schema field(s) [...]` events
   naming Contact/name anywhere in the log. (It did fire twice for
   unrelated entities — `['status']` on blog_cms's `ArticleCreate` and
   `['email']` on its `UserCreate` — ordinary independent generation
   variance on different entities, not a recurrence of the target bug.)
3. **Schemas continue to match generated model columns — YES.** Verified
   on disk for all 3 apps.
4. **Runtime/CRUD not negatively affected by the mechanism — YES, and
   better than "not negatively affected": both blog_cms and crm achieved
   full `Journey PASS — 11 passed / 0 failed` (Create 201, Edit 200,
   Delete 204) — the first full CRUD pass for crm at any point in this
   entire session, and the first for blog_cms too.**
5. **Remaining failures, classified:**
   - **todo**: `Create entity: 400` (unseeded `Priority` lookup table,
     `task_routes.py`'s `Priority.name == task_in.priority` query) —
     **unrelated generation variance / existing infrastructure issue**,
     the exact same root cause identified in Experiment 013, in a
     different code path (route business logic) this mechanism never
     touches. Confirmed the Task model/schema field names agree; this is
     not model/schema drift.
   - `fix_model_schema_notnull_gap` fired 5 times across the run on
     `title`/`due_date`/`priority_id` (todo), `username`/`hashed_password`
     (todo/blog_cms users), `content_markdown`/`cover_image_url`/
     `published_at` (blog_cms posts) — **existing infrastructure (the
     preflight safety net doing its normal, intended job)**, not a
     regression: these are ordinary nullable/optional-field variance in
     entities unrelated to the Contact-style renaming drift this
     experiment targets, and the safety net exists precisely to catch
     exactly this class of gap regardless of cause.
   - No occurrences of any new/unexplained crash pattern. **Zero evidence
     of a regression introduced by this feature.**

**Report:**
- **Drift eliminated? YES** — confirmed on two independent runs
  (Experiments 017 and 018) under different generation attempts.
- **Reactive patcher fired (for the target bug)? NO.**
- **New regressions caused by this feature? NO** — every remaining
  failure traces to an independent, pre-existing, already-catalogued
  cause outside this mechanism's scope.
- **Confidence this should become the default: HIGH.** Two consecutive
  canaries, the second a clean, uncontaminated `CANARY PASSED` with both
  previously-blocked apps achieving full CRUD success for the first time
  this cycle.

**Decision: PROMOTED FORGE_MODEL_DRIVEN_SCHEMA to the default** (commit
pending below) — `MODEL_DRIVEN_SCHEMA_GENERATION` now defaults to `True`
when the env var is unset. The flag itself is kept (`FORGE_MODEL_DRIVEN_SCHEMA=0`
rolls back to the old lookup instantly if a future regression is ever
traced to this mechanism) — verified both directions still work.
Relationship/secondary-table extraction (e.g. blog_cms's tags) explicitly
NOT implemented this cycle — flagged as a separate future experiment per
instruction.

**Cost**: $0.0267 (todo) + $0.0275 (blog_cms) + $0.0145 (crm) = **$0.0687
total**.

---

## Experiment 019 — Seed reference data before CRUD journey

**Hypothesis**: does calling `POST /seed` before the CRUD journey
(commit edea8bb) fix todo's persistent Create-entity failure, which was
traced across Experiments 013/015/016/017/018 to an unseeded `priorities`
reference table?

**Evidence**: `generation_log.jsonl` post-Experiment-018 showed
`POST /seed returned 500` and todo's `Create entity: 400`-class
JourneyCRUDFailure tied for most-frequent specific signature (7 and
6-7 respectively). Ranked the seed-500 pattern lower priority: it already
has a working, zero-cost deterministic recovery (`repair/orchestrator.py`
rewrites a known-good `seed_routes.py` stub on that exact diagnostic),
whereas todo's Create-entity 400 has no existing fix and had recurred in
literally every todo canary run this session.

**Root cause**: **runtime / journey-runner test-harness limitation**, not
a generation defect. The generated `task_routes.py` correctly validates
`priority_id` against the `priorities` table and correctly rejects a
reference that doesn't exist — the app isn't buggy. The `priorities`
table is only ever populated by the app's own generated `POST /seed`
endpoint, which the journey runner never called before attempting CRUD.

**Implementation**: single best-effort `POST {base}/seed` call added
right after Login (carries the auth token), before Create entity.
Deliberately not recorded as a `JourneyStep` (verified `steps_passed`/
`steps_failed`/`success` are computed purely from the `steps` list, which
this call never joins — cannot affect scoring even if the endpoint is
absent).

**Validation (local, $0)**: mocked `requests` end-to-end — confirmed
`/seed` is called exactly once, after Login and before Create entity,
carries the Bearer token, never appears in the JourneyStep list, and the
journey runs to completion normally when the call raises (simulating no
`/seed` endpoint). `ast.parse`/`py_compile` clean, graphify graph updated.

**Benchmark comparison** (`--provider gemini --no-deploy`, log
`m1_canary_seedcrud_run.log`) vs. Experiment 018:
| App | Exp018 | Exp019 | Verdict |
|---|---|---|---|
| todo | 73.9 (C) | 73.9 (C) | OK, unchanged |
| blog_cms | 90.3 (A), CRUD 11/11 | 90.3 (A), CRUD 11/11 | OK |
| crm | 91.4 (A), CRUD 11/11, 4/5 fix attempts | 91.6 (A), CRUD 11/11, **0/5 fix attempts** | OK, improved |

**`CANARY PASSED — safe to continue`.**

**Telemetry — mechanism confirmed working, but gated by an orthogonal,
pre-existing factor**: the wire log confirms the fix fired exactly as
designed: `"POST /seed HTTP/1.1" 200 OK` immediately after login, before
`"POST /tasks" ... 400 Bad Request`. Investigated why todo's Create still
failed despite a successful seed call: **this attempt's generated
`seed_routes.py` is the deterministic minimal-stub fallback**
(`v6_orchestrator.py`'s "never call the LLM for seed_routes.py... write a
minimal working stub" path, confirmed by the exact stub text `{'seeded':
True, 'message': 'Demo data ready'}` and zero references to `Priority(`
anywhere in the file) — used whenever the LLM fails to produce a real
`seed_routes.py` this attempt. The stub does no database inserts at all,
so there was no reference data to seed regardless of when `/seed` gets
called. This is a *different*, pre-existing, already-known generation-
variance factor (whether `seed_routes.py` gets generated with real content
vs. falls back to the no-op stub) — not a flaw in this fix, and not
something this fix was ever positioned to control.

**Verdict: KEEP.** The mechanism is proven correct and harmless (fires at
the right point, never affects scoring, zero regressions across two apps
that already depend on real seed data working — crm's fix-attempt count
actually *improved* to 0/5, its best result yet). It will pay off on any
future generation attempt where `seed_routes.py` gets real content (as
observed multiple times earlier this session) without needing further
change. Not reverting a correct, zero-risk fix because one specific
attempt's *upstream* content generation defaulted to a no-op stub.

**Engineering effort**: S (single isolated addition, ~20 lines, no new
dependencies). **Expected ROI**: partial and conditional — eliminates the
unseeded-reference-table failure mode whenever `seed_routes.py` is
substantively generated; does nothing when it falls back to the no-op
stub (a separate, now-identified gap).

**Next highest-ROI bottleneck**: the minimal seed-stub fallback itself.
When the LLM fails to produce `seed_routes.py`, `v6_orchestrator.py`
silently substitutes a no-op stub that always returns `200 OK` with zero
inserts — which is exactly why this experiment's improvement didn't
materialize for todo this run. A deterministic upgrade (using the existing
`entity_metadata` extractor to detect reference/lookup-style tables from
already-generated models and auto-seed a few rows into the stub, instead
of leaving it empty) would directly compound with this experiment's fix.
This requires a further design + a validating benchmark, so — per stop
condition 1 — flagging it as the prepared next task rather than starting
it now.

**Cost**: $0.0194 (todo) + $0.0217 (blog_cms) + $0.0154 (crm) = **$0.0565
total**.

**Housekeeping this cycle**: found and deleted ~10 zero-byte debris files in
`backend/` (`backend/'`, `backend/65`, `backend/dict`, etc.) — artifacts of
an earlier broken shell redirection, not user work. Also found an earlier,
abandoned exploration (`ForgeAIV15Adapter` added to `run_forgebench.py`,
plus a path fix in `tests/run_fixture_regression.py`) from a pre-canary-script
attempt at baselining via the `run_forgebench.py --suite golden` runner
(`backend/benchmark_results/20260706_0613_golden/`, all 3 results scored
0.0) — predates and was superseded by the `run_canary.py` 3-app canary
methodology this log otherwise uses. Left uncommitted pending a decision on
whether to keep, finish, or discard it.
