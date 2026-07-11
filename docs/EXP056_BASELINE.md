# Experiment 056 — Post-Hardening Reliability Baseline

2026-07-12. Measurement only — no fixes implemented, no production code
changed. 2 canary rounds run (5 of a possible 30 app-generations), stopped
early per the experiment's own budget clause once a dominant, root-caused
failure class emerged and was confirmed across two independent rounds.
Round 3 was started but externally stopped after completing 2 of 3 apps;
those 2 completed runs are included below (5 total data points), the
in-flight `crm` run was not restarted.

**Cost:** ~532,675 tokens, ~$0.32 estimated, ~31.5 min wall-clock across 5
generations. Routed through the Cerebras-first auto chain (this session's
earlier change) — Gemini hit `429 RESOURCE_EXHAUSTED` (prepayment credits
depleted) on every attempted call across all 5 runs, confirming that
change is actively reducing exposure to a dead quota, not just theoretical.

---

## 1. Runs

| Round | App | Crashed | First score | Final score | Fix attempts | Build | Runtime | CRUD | Browser | Visual Judge | Elapsed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | todo | No | 71.61 | 74.4 (C) | 3 | ✅ | ❌ | ❌ | ✅ | 45.4 | 282.1s |
| 1 | blog_cms | No | 69.23 | 72.06 (C) | 4 | ✅ | ❌ | ❌ | ✅ | 34.9 | 593.8s |
| 1 | crm | No | 89.87 | 89.87 (B) | 0 | ✅ | ✅ | ✅ | ✅ | 65.2 | 93.1s |
| 2 | todo | No | 70.68 | 74.4 (C) | 4 | ✅ | ❌ | ❌ | ✅ | 45.4 | 308.9s |
| 2 | blog_cms | No | 69.2 | 70.91 (C) | 5 | ✅ | ❌ | ❌ | ✅ | 34.9 | 611.9s |

Repair strategies used (outer `FixOrchestrator`, per run):
- r1/todo: `patch_file, patch_file, regenerate_module`
- r1/blog_cms: `patch_file, patch_file, regenerate_arch, regenerate_arch`
- r1/crm: none (0 fix attempts)
- r2/todo: `patch_file, switch_model, regenerate_module, switch_model`
- r2/blog_cms: `patch_file, patch_file, regenerate_arch, regenerate_arch, regenerate_arch`

Per-role LLM call counts (all 5 runs, from each run's own summary table):
Planner=1, Architect=1, Tech Lead=1, Frontend=1 in **every single run** —
generation-team stages 1-4 never needed a retry in this sample. Backend
generation: 28 (todo), 30 (blog_cms), 28 (crm) — consistent, not the
differentiator. The pipeline does not record separate timeline stages for
planner/architect/backend/frontend individually (only a single collapsed
`"generation"` stage — confirmed by reading `app/core/pipeline.py`'s
`begin_stage` call sites directly); the LLM-call-count table above is the
closest real per-role signal available without changing production code
to add finer-grained instrumentation, which is out of scope for a
measurement-only experiment.

**Timeline stages that DO exist** (`generation`, `deterministic-patch`,
`verification`, `static-validation`, `runtime-validation`,
`parallel-checks`) were captured in full for every run
(`benchmark_results/exp056/round{N}_{app}.json`, `result.timeline`).
`runtime-validation` failed on 5/6 attempts for todo (round 1) and 6/8 for
blog_cms (round 1) — i.e. it almost never passed on any single attempt
within a run, even though the run eventually reported SOME score
improvement via other channels (deterministic patches, LLM patch_file
fixes to non-runtime issues).

Full per-run artifacts: `backend/benchmark_results/exp056/round{1,2}_{app}.{json,log}`
— includes full `result` dict (dimensions, score_history, retry_history,
confidence, timeline) and complete captured stdout per app. Not
reproduced in full here per the task's own request to collect but not
dump everything into the summary doc.

---

## 2. Reliability summary

Using the operational bar this codebase's own canary already uses
(`build_ok AND runtime_ok AND crud_ok`, deploy excluded since all 5 runs
used `--no-deploy`):

- **First-pass success** (no fix attempts needed at all): **1/5 (20%)** — only `crm` (round 1).
- **Final success** (after the full repair loop, up to 3-5 attempts): **1/5 (20%)** — same one run.
- **Success delta (first-pass → final): 0 percentage points.** In this
  sample, the repair loop never converted a single initially-failing app
  into a fully passing one — every app that started broken (Runtime
  Startup + CRUD failing) ended the same way, despite 3-5 repair attempts
  and 5-10 minutes of repair time each. Forge score crept up slightly in
  every failing run (+2.8, +2.8, +3.7, +1.7 points) via non-runtime fixes
  (validation errors, patch_file edits), but the dimension that actually
  gates "does this app work" never recovered.
- **Most common failure:** Runtime Startup failing at final state — **4/5 runs (80%)**, always paired with Integration/CRUD also failing (the CRUD journey can't complete against a backend that isn't healthy).
- **Second most common failure:** Visual Judge scoring low — **5/5 runs (100%)**, including the one operationally-successful run (crm: 65.2/100). Lower severity/weight than Runtime Startup in the scoring model (doesn't block build_ok/runtime_ok/crud_ok), but it is the single most universal issue in this sample by raw frequency.
- **Third:** Gemini `429 RESOURCE_EXHAUSTED` (prepayment credits depleted) on every attempted Gemini call, all 5 runs — already known/documented (`CLAUDE.md`), not new, but confirms it's still live and validates routing Cerebras first.

---

## 3. Root cause of the dominant failure (confirmed, not inferred)

The Runtime Startup / CRUD failures above are not one undifferentiated
"generation quality" problem — a specific, exact-line-number regression
was identified and confirmed as a major contributor:

**`app/services/v6_orchestrator.py:823`** — inside `generate_project_v6`'s
runtime-fix retry loop (`for r_attempt in range(max_runtime_attempts + 1)`,
`max_runtime_attempts = 3`, so up to 4 validate→fix→re-clean cycles are
supposed to run), the line `patch_model_field_mismatches(project_path)`
raises `NameError: name 'patch_model_field_mismatches' is not defined`.

**Root cause, verified via `git show f7d4dca`:** Experiment 053 extracted
this function's original inline Stage-1 code (which included a *local*
`from app.services.database_patcher import patch_database_py,
patch_model_field_mismatches, ...` import) into a new helper function,
`_run_initial_deterministic_patches()`. That import is scoped to the new
helper only. But the runtime-fix loop ~500 lines later, still inside the
*same* `generate_project_v6` function body, bare-calls
`patch_model_field_mismatches` (plus 4 more now-unbound names right after
it: `patch_add_missing_model_columns`, `patch_add_missing_schema_fields`,
`patch_missing_required_constructor_kwargs`,
`patch_filter_dict_unpack_constructor_kwargs`) — relying on the import
still being in the enclosing function's scope, which Exp053 silently
removed. This is a genuine regression Exp053 introduced while preserving
behavior everywhere it checked — it did not check this call site, ~500
lines away from the one it edited, in the same function.

**Confirmed blast radius by reading the surrounding code, not assumed:**
the `try/except` wrapping this loop (line 788/844) catches the
`NameError` — so the pipeline doesn't crash — but the exception aborts
the `for r_attempt` loop entirely on whichever iteration first needs a
fix. Concretely: `validate_runtime()` runs, fails, an LLM-generated fix
gets written to disk (`write_fix`, line 819) — **then the loop crashes
before ever re-running `validate_runtime()` to check whether that fix
worked**, and before running the 4 cleanup patchers or the stale-SQLite-db
removal that were supposed to follow. `runtime_result` is left as
`{"success": False, "error": "name 'patch_model_field_mismatches' is not
defined"}` regardless of whether the LLM's fix actually fixed anything.

**Confirmed present in every failing run, from two independent evidence
sources:**
1. The literal error string `"patch_model_field_mismatches' is not
   defined"` appears in the captured logs: round1/todo (1x),
   round1/blog_cms (3x), round2/todo (1x), round2/blog_cms (4x).
   round1/crm: 0x (crm never needed the runtime-fix loop at all — passed
   `validate_runtime()` on its first attempt).
2. Independently, every run's own LLM-call summary table shows
   `Runtime Fixes` stuck at exactly `1` for todo and blog_cms in all 4
   failing runs, despite `max_runtime_attempts = 3` allowing up to 4.
   `crm` (the one clean run): `Runtime Fixes = 0`. This is the same
   conclusion reached by a completely different data source (aggregate
   LLM call counts vs. raw log text), which is why this is reported as
   **confirmed**, not a hypothesis.

**Scope check:** `repair_project()` (the other function Exp053 extracted
Stage 1 for) does **not** contain this runtime-fix loop pattern at all —
grep confirms `patch_model_field_mismatches(project_path)` appears bare
only inside `generate_project_v6` (line 823), nowhere in `repair_project`
(which starts at line 1014). The regression is scoped to exactly one
function, one code path — but it is the live path both the canary and
the public `/project/v15` API use for every generation that needs a
runtime fix.

---

## 4. Secondary finding: recurring schema/route field-mismatch pattern

Independent of the NameError above, both todo and blog_cms hit the same
*shape* of application bug repeatedly across retries: an `AttributeError`
from a route or seed script referencing a field the Pydantic
schema/SQLAlchemy model doesn't actually have —
`AttributeError: 'SignupRequest' object has no attribute 'username'`
(`auth_routes.py:96`) and `AttributeError: type object 'User' has no
attribute 'name'` (`seed_routes.py:61`) in round1/todo; blog_cms showed
the same `AttributeError`-class "Runtime crash" 4 times across its
retries per the fix-orchestrator's own grouping
(`[fix] Group [1] Runtime crash: [AttributeError]`).

This is plausibly made *worse* by the §3 regression (the cleanup patchers
meant to catch exactly this class of drift — `patch_model_field_mismatches`
is literally named for it — never get to run after the first fix
attempt), but the underlying generation-quality issue (route/seed code
drifting from the schema/model's real field names) would need its own
investigation even after §3 is fixed. Not root-caused further this cycle
— flagged as the natural Exp057 follow-up if §3's fix doesn't fully
resolve it.

---

## 5. Comparison against previous Observatory / canary metrics

**Historical context from the confidence engine's own baseline** (each
run's `confidence.historical_rate`, computed over n=75-79 prior
generations): ~35-36% historical success rate. This sample's 20% (1/5) is
below that, but n=5 is too small to call a statistically meaningful
regression on its own — the §3 root cause is what makes this
actionable, not the raw percentage.

**Direct before/after for the same apps** (from `canary_history.json`,
pre-existing entries, not re-derived):

| App | Last recorded (pre-Exp053) | This baseline (post-Exp053/054/055) | Delta |
|---|---|---|---|
| todo | 99.71 (`exp048-regen-cache-bypass`, 2026-07-11, runtime=True) | 74.4 / 74.4 (both rounds, runtime=False) | **-25.3** |
| blog_cms | 65.86 (`exp048-regen-cache-bypass`, runtime=False already) | 72.06 / 70.91 (runtime=False) | +6.2 / +5.1 (already broken before) |
| crm | 0.0 (`exp048-regen-cache-bypass` — anomalous run, `build_ok=None`, looks like it never really executed) | 89.87 (runtime=True) | not comparable (prior data point looks invalid) |

**todo's -25.3 point drop, specifically from a previously-passing
`runtime_ok=True` to `runtime_ok=False` in both of this baseline's
rounds, is the strongest single piece of evidence that something
regressed between the last recorded canary (2026-07-11, pre-Exp053) and
now** — consistent with, and best explained by, the §3 finding. blog_cms
was already failing runtime in the last recorded run, so its issue may be
partly pre-existing rather than newly introduced by Exp053 — the §3
regression would still be actively preventing its repair loop from
recovering, whatever the original cause was.

**Broader Observatory snapshot** (`compute_observatory`, 30-entry window
over `generation_log.jsonl`, includes non-canary runs from this session
and prior dev/test activity, not just these 5): `first_try_success_rate
= 40.0%` (trending +16.7), `generation_success_rate = 53.3%`,
`top_failure_now = FrontendBuildError`, `avg_fix_iterations = 1.63`,
`canary_health = Healthy` (reflects only build/runtime/CRUD/browser
regression checks, not the deeper root cause above),
`prevention_by_category` totals 210 prevented issues in-window (Schema
validator 96, Other 71, Syntax validator 17, Pydantic patcher 12, Entity
validator 9, Frontend patcher 5). Note this broader window's
`top_failure_now = FrontendBuildError` differs from this experiment's
own canary-specific finding (Runtime Startup) — these are two different
lenses (a 30-entry rolling window across all generation activity vs. 5
canary-only, no-deploy, same-3-apps runs) and should not be conflated;
the canary-specific finding is the one with a confirmed root cause.

`canary_history.json` and `MEMORY.md`-adjacent Observatory data updated
with both rounds (labels `exp056-baseline-r1`, `exp056-baseline-r2`) —
verified by calling `compute_observatory`, `compute_reliability_timeline`,
and `compute_experiment_attribution` directly against the updated history
and confirming they process the new entries without error (not just that
the JSON was appended).

---

## 6. Ranked failure classes (evidence-backed, for Exp057)

1. **[HIGHEST ROI] `patch_model_field_mismatches` NameError regression**
   (`app/services/v6_orchestrator.py:823`, introduced by Exp053/`f7d4dca`).
   Confirmed root cause of the runtime-fix retry loop silently losing
   3 of its 4 intended attempts on every app that needs it. Evidence:
   exact commit diff, exact line, literal error string in 4/5 run logs,
   independently corroborated by the `Runtime Fixes` call-count staying
   at 1 across every failing run. **Trivial, low-risk, well-scoped
   one-function fix**: re-import the 5 names inside (or just before)
   the runtime-fix loop, restoring pre-Exp053 behavior exactly — this is
   a regression fix, not a new heuristic, so it doesn't need corpus
   prevalence justification the way a new validator would.
2. **Recurring schema/route field-name AttributeErrors** (§4) — real,
   but currently entangled with #1 (the very patcher meant to catch this
   never gets to run). Re-measure after #1 ships before deciding whether
   this needs its own fix.
3. **Visual Judge low scores, 5/5 runs including successes** — universal
   but lower severity (doesn't gate build/runtime/CRUD). Worth a
   dedicated look if it turns out to also gate the deploy threshold in
   practice, not investigated further this cycle.
4. Gemini `429`/quota-exhausted (known, already worked around by routing
   Cerebras-first this session) — no new action needed, just confirms
   the existing mitigation is load-bearing.

**Answer to "what is now the single highest ROI engineering problem?":**
fixing the `patch_model_field_mismatches` NameError at
`v6_orchestrator.py:823`. It is a confirmed, exact-line regression (not a
guess), the fix is a near-zero-risk one-line-scope restoration (not a new
heuristic), and it directly gates the self-healing capacity of the most
commonly needed repair path in the live pipeline — the mechanism that's
supposed to convert a broken first-pass generation into a working one is
currently crashing out after its first attempt, every time, silently.

---

## 7. Explicitly not done this cycle (measurement-only rule)

- The regression in §3 was **not fixed** — diagnosed with full precision
  and left for Exp057, per this experiment's own rule ("Do NOT implement
  any fixes... measurement only").
- Round 3 of the canary (would have been the 3rd `crm` data point) was
  not restarted after being externally stopped — 5 data points across 2
  rounds already showed a clear, reproducible, root-caused dominant
  failure class, satisfying the budget's own "stop early" clause.
- The recurring field-mismatch AttributeError pattern (§4) was observed
  and logged with file/line precision but not further root-caused or
  fixed — flagged as a candidate for re-measurement after §3 ships.
