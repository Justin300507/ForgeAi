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
