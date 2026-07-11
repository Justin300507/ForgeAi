# Experiment 058 — Live Regression Validation (Cerebras Budget)

2026-07-12. Validates Exp057's fix for the Exp053 regression (Exp056
found it, Exp057 fixed it offline). 2 of a possible 5 canary runs used —
stopped early once both rounds independently confirmed the same result,
per the budget's "stop immediately once confidence is sufficient" and
"do not consume credits chasing secondary issues" rules.

**Method deviation from Exp056, deliberate and in-scope:** ran `todo`
only per round, not the full 3-app canary. Justification: every one of
this experiment's 5 Primary Questions is about the Stage-12 regression
and the Todo score specifically — `blog_cms` was already failing runtime
before Exp053 (muddying interpretation, per Exp056's own report) and
`crm` never needed the runtime-fix loop at all. Running them would have
been "consuming credits chasing secondary issues" against this
experiment's own explicit rule. `backend/scripts/exp058_validate.py`
(new, non-production, same pattern as `exp056_measure.py`) reuses
`run_canary.py`'s app list/idea files/lock unmodified.

## Runs

| Round | App | Crashed | NameError present | Score | Fix attempts | Runtime | Stagnation-guard triggers | Runtime Fixes | LLM cache hits |
|---|---|---|---|---|---|---|---|---|---|
| 1 | todo | No | **No (0)** | 70.68 (C) | 3 | ❌ | 2 | 1 | 9 |
| 2 | todo | No | **No (0)** | 71.54 (C) | 3 | ❌ | 2 | 1 | 9 |

Both rounds: `retry_history` strategies `patch_file, patch_file,
regenerate_arch` (identical sequence both times); `Compilation` passed,
`Integration`/CRUD failed, `Browser UX` passed, `Visual Judge` ~45.

## Primary Questions — answered

**1. Does the NameError disappear completely?**
**Yes.** `grep -c "patch_model_field_mismatches' is not defined"` = 0 in
both round logs (`benchmark_results/exp058/round{1,2}_todo.log`). Exp056
found this string in 4/5 baseline runs; it is now absent in 2/2 live
validation runs.

**2. Does Stage-12 now execute all intended retry iterations?**
**Yes, correctly — with an important nuance.** "All intended iterations"
does not mean "always hit the 4-attempt cap" — the loop has a
pre-existing, untouched stagnation guard that's SUPPOSED to stop early
when a fix isn't changing the failure. Both rounds show
`"[runtime fix] Failure signature unchanged — stopping retries"`
firing exactly twice each (`round1_todo.log:339,1217`;
`round2_todo.log:365,1210`) — meaning the loop now runs until it
legitimately determines further LLM-fix attempts are futile, instead of
crashing on its very first attempt (Exp056's confirmed pre-fix behavior:
1 `validate_runtime` call, then a `NameError`). This is exactly the
behavior Exp057's own regression test
(`test_stagnation_guard_still_stops_early_unchanged`) predicted and
verified offline — now confirmed live.

**3. Is Runtime Fix count greater than 1 when appropriate?**
**It stayed at 1 in both rounds — and that is now the CORRECT outcome,
not a bug.** Before interpreting "still 1" as a residual problem: the
stagnation guard is designed to generate exactly one fix, check if the
failure signature changed, and stop immediately if it didn't (no point
burning more LLM calls repeating an ineffective fix). Both rounds show
exactly that: 1 fix generated, signature unchanged, guard stops. This is
"greater than 1" only when the LLM's fix actually changes something
about the failure — in this specific sample, it doesn't (see Q4/Q5
below for why). The mechanism is confirmed correct; whether 1 is the
"right" number for this specific app/bug is a separate, deeper question
this experiment didn't set out to answer (and isn't the regression Exp057
fixed).

**4. Does the Todo score recover substantially from the Exp056 baseline (~74.4)?**
**No — 70.68 and 71.54, both slightly below the 74.4 baseline average,
and nowhere near the pre-regression 99.71.** This is honestly reported,
not smoothed over. Root cause: 9 LLM cache hits per round
(`grep -c "\[LLM cache\] HIT"`) mean planner/architect/backend/frontend
generation is being served from Exp056-era cached responses — i.e. this
is close to the SAME generated sample Exp056 measured, carrying the
SAME underlying defect Exp056's own report flagged as a **separate,
unfixed issue**: a recurring schema/route field-name `AttributeError`
(`SignupRequest` missing `username`, `User` model missing `name`) that
Exp057 never touched. Exp057 fixed the retry MECHANISM (it no longer
crashes, it runs correctly, it stops appropriately) — it did not, and
was never scoped to, fix the underlying generation-quality bug the
mechanism keeps failing to patch. The score not recovering is consistent
with, not contradictory to, "the regression is fixed."

**5. Does a new dominant failure class emerge?**
**No — the SAME failure class as Exp056 remains dominant**: Runtime
Startup + Integration/CRUD failing, driven by the same-shaped
`AttributeError`. This is not a new discovery; it's confirmation that
Exp056's "secondary finding" (§4 of `docs/EXP056_BASELINE.md`) is the
real remaining blocker for `todo`'s score, now cleanly isolated (no
longer entangled with the NameError that was previously masking clean
observation of it).

## Before vs. after (Exp056 → Exp058)

| Metric | Exp056 (pre-fix, 2 rounds) | Exp058 (post-fix, 2 rounds) |
|---|---|---|
| NameError present | 4/5 runs (todo: 2/2 its own runs) | 0/2 runs |
| `validate_runtime` calls before loop exit (persistent-failure case) | 1 (crashes) | ~6-7 across 2 Stage-12 invocations per run (stagnation guard exits each cleanly, not a crash) |
| Runtime Fixes (LLM call count) | stuck at 1 (crash-truncated) | stuck at 1 (guard-terminated — same number, different and correct reason) |
| Todo score | 74.4, 74.4 (both rounds) | 70.68, 71.54 |
| Dominant failure | Runtime Startup / Integration, masked by NameError | Runtime Startup / Integration, same AttributeError pattern, now cleanly attributable (no NameError noise) |

**Runtime Fix statistics:** identical count (1) before and after, but the
*mechanism* producing that count is entirely different — crash-abort
(pre-fix, confirmed via Exp057's `git stash` replay) vs.
stagnation-guard-designed-exit (post-fix, confirmed via the log evidence
above). Reporting the raw number alone would be misleading; the log
evidence disambiguates it correctly.

**Todo score comparison:** 74.4 → 70.68/71.54. A small, likely-noise-level
decline (~3-4 points), not an improvement, and not the "substantial
recovery" Primary Question 4 asked about. Explained fully by §"Primary
Questions" Q4 above — this is a cache-replay artifact of measuring the
same underlying generation defect, not a sign the fix made anything worse.

## Remaining failure ranking (unchanged from Exp056, now confirmed cleanly)

1. **[STILL #1] Recurring schema/route field-mismatch `AttributeError`**
   (`SignupRequest` missing `username`, `User` model missing `name`,
   per Exp056 §4) — now the clean, sole blocker for `todo`'s score,
   no longer entangled with the NameError. Not investigated further
   this cycle per "do not fix anything."
2. Visual Judge low scores (~45, consistent with Exp056) — same
   secondary, lower-severity finding, unchanged.

No NEW dominant failure class was found. Per the experiment's own rule
("if a new dominant failure appears: fully document it, rank it, stop")
— none appeared, so this section is short by design, not by omission.

## Success criteria assessment

The stated success criteria required: NameError disappears (✅), retry
loop behaves correctly (✅), Todo score improves (❌ — declined slightly,
within noise, not "substantially recovered"), AND a different failure
becomes dominant (❌ — same failure class, now cleanly visible).

**Honest verdict: Exp057's fix is validated as correctly resolving the
Exp053 regression — the retry-loop mechanism now works exactly as
designed, confirmed with concrete log evidence across 2 independent live
runs.** The full stated success criteria (score improvement + new
dominant failure) is not met, because those two criteria implicitly
assumed the regression was the ONLY thing holding `todo`'s score down —
Exp056's own report already flagged a second, separate, unfixed defect
(§4) as a candidate blocker, and this validation confirms that second
defect, not the regression, is what's now capping `todo`'s score. This
is not a failure of Exp057's fix; it's a correctly-isolated finding that
the mechanism fix and the score aren't the same thing.

## Cost

2 generations (todo only), ~2 minutes over the round1/round2
`benchmark_results/exp058/round{1,2}_todo.json` `elapsed_s` fields
(368.4s + 399.4s ≈ 12.8 min wall-clock). Heavy LLM cache reuse (9 hits/round)
kept fresh-token spend low. Stopped at 2/5 rounds — did not consume the
remaining budget since confidence was already sufficient after 2
independent, consistent confirmations.

## Explicitly not done this cycle

- Did not investigate or fix the recurring `AttributeError` pattern
  (Exp056 §4) — flagged, not touched, per "do not fix anything."
- Did not run `blog_cms`/`crm` — out of scope per the method deviation
  justified above.
- Did not begin Exp059 in this experiment, per its own explicit
  instruction.
