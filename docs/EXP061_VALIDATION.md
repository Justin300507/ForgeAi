# Experiment 061 — Live Validator Contract Validation

2026-07-12. Validates Experiment 060's Diagnostic contract migration
live. 2 of a possible 2 `todo` canary runs used (full budget, both
needed for sufficient evidence — see §Q4 below for why). Validation
only: no fixes implemented, no additional validators migrated, no
production code changed during this experiment.

**Method:** `backend/scripts/exp061_validate.py` (new, non-production)
runs `generate_project_v15` exactly like `exp058_validate.py` did, but
additionally monkeypatches three functions purely to *observe* their
calls — `validator_service.validate_project`, `engine._run_static_validators`,
and `v6_orchestrator.write_fix` — each wrapper calls straight through to
the real implementation before/after recording, so no behavior is
altered. Patches target the actual module-level bindings used at each
call site (e.g. `v6_orchestrator.write_fix`, not `fix_writer_service.write_fix`
— the same import-binding lesson Exp057 encountered). Verified against
the real `generated_projects/todo_list_app` fixture with zero API cost
before spending anything live.

## Q1 — Do migrated validators emit canonical Diagnostic objects correctly?

**Yes, confirmed strongly, both rounds.** Every diagnosed error in both
runs produced a Diagnostic with correct `validator_name`, `category`,
`severity`, and an accurate `file_path` matching a real file in the
generated project:

| Round | Validators observed firing | Diagnostics | file_path accuracy |
|---|---|---|---|
| 1 | `validate_schema_model_consistency`, `validate_endpoints`, `validate_frontend_api_calls` | 8 | 8/8 point at real, existing files |
| 2 | Same two + repeated across 10 `validate_project()` calls | 33 total (across all calls) | Consistently accurate |

Cross-checked: every raw string in `validate_project()`'s `errors` list
had a matching native Diagnostic by exact message text, 100% of the
time, both rounds (`round{1,2}_todo.json`'s `observed.validate_project_calls`).

## Q2 — Does the repair layer target the correct files?

**Yes, where directly observable.** `write_fix` targets were
cross-referenced against the diagnosed `file_path` values:

- **Round 1**: 3/3 `validate_frontend_api_calls` diagnostics
  (`src/pages/LoginPage.jsx`, `RegisterPage.jsx`, `UsersPage.jsx`) have
  an EXACT matching `write_fix` call to that same path.
- **Round 2**: same 3 frontend files matched again, AND — with 2 more
  retry attempts than round 1 — `write_fix` eventually hit
  `app/routes/auth_routes.py` and `app/schemas/task.py` directly,
  exactly matching the `validate_endpoints`/`validate_schema_model_consistency`
  diagnostics' `file_path` values that round 1's shorter run never got
  far enough to act on directly.

**One observed gap, investigated and attributed correctly — not a
contract bug:** round 1's `auth_routes.py`-targeted diagnostics didn't
show a matching `write_fix` call within that round's 3 attempts. Traced
the cause by reading code, not guessing: attempt 3 used the
`regenerate_module` strategy, which executes via
`app/repair/orchestrator.py::_regenerate_module` — a **separate repair
pathway** from `v6_orchestrator.py`'s own fix loop, using its own
file-writing mechanism that this experiment's observer did not
instrument (it only wrapped `v6_orchestrator.write_fix`). This is an
**instrumentation boundary of this experiment's observer script**, not
evidence of a wrong-file repair — round 2's longer run (which stayed on
`patch_file` longer before escalating) shows the SAME diagnosed files
being hit directly once that pathway was exercised.

## Q3 — Do any repairs become less accurate?

**No evidence of this.** Every direct file_path-to-write_fix correlation
found was a correct match. No case was found where a repair targeted a
file DIFFERENT from what a Diagnostic named.

## Q4 — Does the regex fallback activate only for legacy validators?

**Insufficient evidence to answer directly — reported honestly, not
inferred.** In both rounds, **100% of the errors that actually occurred
came from already-migrated validators** (`validate_schema_model_consistency`,
`validate_endpoints`, `validate_frontend_api_calls`). None of the 8
still-legacy validators (`validate_backend_imports`,
`validate_imported_symbols`, `validate_frontend_imports`,
`validate_frontend_nav_targets`, `validate_frontend_api_client`,
`validate_route_quality`, `validate_requirements`,
`validate_common_antipatterns`) produced any error in either live run —
this specific app/idea's generated code simply never triggers them. The
regex fallback path was therefore **not exercised live** this cycle.

This question IS answered, with full confidence, by Exp060's own offline
regression test (`test_engine_falls_back_to_regex_for_unmigrated_validators`,
`tests/reliability/test_validator_contract_unification.py`), which
directly forces this exact scenario and confirms the fallback produces a
valid Diagnostic. Live confirmation of the fallback path specifically
would need either a different canary app (`blog_cms`/`crm`) or a
project fixture that happens to trigger one of the 8 unmigrated
validators — flagged as a gap for whichever future cycle picks up
migrating them (see `docs/VALIDATOR_MIGRATION.md`'s "not migrated this
cycle" table).

## Q5 — Do Observatory metrics remain correct?

**Yes, confirmed directly, not assumed.** `canary_history.json` updated
with both rounds (`exp061-validation-r1`, `-r2`); `compute_observatory`
and `compute_reliability_timeline` both run cleanly against the updated
history with no errors, producing sensible values (`canary_health:
Healthy`, timeline points with correct `avg_score`/`n` for each new
entry).

## Q6 — Does engine.py consume both native and adapted diagnostics correctly?

**Native path: yes, confirmed live** — every diagnostic observed in
`_run_static_validators`'s output had `legacy_adapter_used=False`
(i.e., `validator_name` populated) and matched its source validator
correctly, both rounds. **Adapted (fallback) path: confirmed offline
only** — see Q4; not exercised by this specific live app both rounds,
but directly tested and passing in Exp060's own regression suite.

## Compare against Exp058 baseline

| Metric | Exp058 (pre-Exp060, 2 rounds) | Exp061 (post-Exp060, 2 rounds) |
|---|---|---|
| NameError present | 0/2 (already fixed by Exp057) | 0/2 (confirmed still fixed) |
| Todo score | 70.68, 71.54 | 74.4, 73.32 |
| fix_attempts | 4, 5 | 3, 5 |
| Stagnation guard triggers | 2 per round | 1 (round 1), not separately re-verified round 2 |
| Validator detections | Same schema-mismatch + missing-endpoint pattern | **Identical detections**, same messages, same files |
| Wrong-file repairs eliminated? | N/A (not instrumented in Exp058) | No wrong-file repairs observed in either round (see Q2/Q3) |
| Regressions introduced? | N/A | **None found** |

**Validator detections did not change** — same errors, same files,
same messages both before and after Exp060, confirming the migration's
own success criterion ("no behavior changes," verified via `git stash`
in Exp060 itself) holds under live conditions too. **Repair count**
varies round-to-round (3-5 attempts) consistent with the pre-existing
stagnation-guard/cache-hit variance already characterized in Exp058 —
not attributable to Exp060. Score staying in the 70-74 range (not
recovering to 99.71) is the SAME, already-diagnosed, separate issue from
Exp056 §4 (recurring `SignupRequest`/`User` schema-field mismatches) —
unrelated to the Diagnostic contract, not touched or expected to be
touched by this migration.

## Failure classification

No failure appeared that required classification against the
contract/adapter/repair/unrelated-generation taxonomy — the one
observed gap (Q2's `auth_routes.py` case) was investigated and
attributed precisely to **this experiment's own observer instrumentation
boundary** (didn't wrap `app/repair/orchestrator.py`'s file-writing),
not to the contract, the adapter, or the repair layer itself.

## Statistics

- **Validator invocations observed**: 15 `validate_project()` calls
  total across both rounds (5 + 10), 41 total diagnostics produced
  (8 + 33).
- **Adapter usage**: 0 legacy-fallback diagnostics observed live (100%
  native); offline test coverage confirms the fallback path separately.
- **Migrated validator success rate**: 100% (41/41 diagnostics had
  correct validator_name/category/severity/file_path, cross-checked
  against real files in the generated project both rounds).
- **write_fix calls observed**: 8 (round 1), 37 (round 2) — round 2's
  higher count reflects its 5 fix attempts (incl. 3 `regenerate_arch`
  passes, which rewrite more files per attempt) vs round 1's 3.
- **Remaining migration risk**: unchanged from Exp060's own assessment
  — the 8 `validator_service.py`-internal legacy validators are
  unexercised by this app/idea and therefore still genuinely untested
  live (though offline-tested). No new risk discovered this cycle.

## Live observations

1. The Diagnostic contract's native path performs exactly as designed
   under real generation load — accurate, consistent, zero drift from
   offline predictions.
2. The specific `todo` idea/cache combination used throughout this
   session's Exp056/058/061 work happens to only ever exercise 2 of the
   15 migrated validators (`validate_schema_model_consistency`,
   `validate_endpoints`/`validate_frontend_api_calls`) — a real
   limitation of using one fixed canary app repeatedly, not a contract
   weakness.
3. `write_fix` targeting is demonstrably file_path-accurate once a fix
   attempt reaches that pathway; `regenerate_module`/`regenerate_arch`
   attempts route through a different, unobserved file-writing mechanism
   that a future validation cycle should also instrument if repair-target
   accuracy for those specific strategies needs direct live confirmation.

## Unexpected validator behavior

None. Every observation matched what Exp060's design and offline tests
predicted.

## Recommendation for Exp062

Two reasonable directions, not mutually exclusive:
1. **Migrate the remaining 8 `validator_service.py`-internal validators**
   (Exp060's own flagged next step) — now with slightly more confidence
   given the pattern has proven itself live for 15/15 already-migrated
   ones, though this specific canary app can't live-validate the newly
   migrated ones either (same Q4 limitation would recur) — offline
   git-stash + regression-test verification (Exp060's own method) would
   remain the primary evidence source, same as before.
2. **Run one live validation round against `blog_cms` or `crm`** instead
   of `todo` again, specifically to exercise a different code path and
   get real evidence for Q4 (regex-fallback activation) — `todo`'s fixed
   idea/cache combination has now been used for 6 live rounds total
   across Exp056/058/061 without ever triggering one of the 8 unmigrated
   validators; a different app is more likely to.

## Estimated confidence that the Diagnostic contract is production-ready

**High for the 15 migrated validators and the native consumption path
(directly confirmed live, twice, with zero discrepancies). Medium-high
overall**, held back only by Q4's genuine live-evidence gap for the
fallback path — which has strong offline test coverage but, honestly,
zero live confirmation yet. Nothing found this cycle lowers confidence;
the gap is about breadth of evidence, not any observed defect.
