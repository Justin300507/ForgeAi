# Experiment 082 — Live Validation of Regenerate Module Reactivation

2026-07-12. Live, one `blog_cms` canary (label `exp082-validation-r1`,
provider `cerebras`, `--no-deploy`), $0.0464 / 77,348 tokens. Same
methodology and instrumentation as Exp079 (`scripts/exp079_canary.py`,
unmodified), reused verbatim per this experiment's own constraint.

## 1. Migration confirmed live, exactly once

Backed up the real `backend/failure_memory/strategy_outcomes.json`
before the run — confirmed pristine (no `"generation"` field anywhere,
i.e. never touched since Exp081 shipped). After the run:

```
AttributeError/regenerate_module: {successes: 0, tries: 3} -> {generation: 2, successes: 0, tries: 0}
ImportError/regenerate_module:    {successes: 1, tries: 1} -> {generation: 2, successes: 0, tries: 0}
SyntaxError/regenerate_module:    {successes: 0, tries: 2} -> {generation: 2, successes: 0, tries: 0}
api/regenerate_module:            {successes: 0, tries: 3} -> {generation: 2, successes: 0, tries: 0}
contract/regenerate_module:       {successes: 0, tries: 3} -> {generation: 2, successes: 0, tries: 0}
contract/patch_file:              {successes: 50, tries: 126} -> {generation: 1, successes: 51, tries: 127}
```

Exactly matches Exp081's offline replay prediction: all 5
`regenerate_module` entries reset; `contract/patch_file` gained one real
try + one success (the actual attempt-1 fix this run made) and was
stamped `generation: 1` for the first time, live confirmation of
`record_outcome()`'s stamp-on-write behavior for a previously-untagged
entry. No other entry changed.

**Confirmed exactly-once**: called `_load()` three more times against the
post-run file — file hash and mtime both unchanged, proving no further
migration/write occurs once entries are current.

## 2. RetryManager no longer skips for stale reasons — confirmed live

Ran `should_skip()` directly against the real, post-migration production
file:

```
contract/regenerate_module:       False  (was permanently True before Exp081)
api/regenerate_module:            False
AttributeError/regenerate_module: False
SyntaxError/regenerate_module:    False
contract/switch_model (untouched): True  (genuinely-valid blacklist preserved)
```

## 3. `_regenerate_module()` did not execute this run — and why (task 6)

Unlike Exp079 (where the blocker was the structural stale blacklist),
this run's fix loop resolved on **attempt 1/5 via `patch_file`**: score
went 79.7 → 91.0 (A, deploy-ready) in a single attempt, so
`RetryManager.next_strategy()` was only ever called once — the ladder
never reached attempt 3, where `regenerate_module` lives, because there
was no need to escalate further. This is healthy, expected fix-loop
behavior, not a bug, and not a new root cause requiring any implementation
change (per this experiment's own constraint).

**Deterministic offline proof, since live escalation depends on
non-deterministic LLM output and isn't worth gambling further Cerebras
spend on**: new test file
`backend/tests/reliability/test_exp082_retrymanager_reactivation.py`
drives `RetryManager` through a simulated two-attempt
non-improving-`patch_file` sequence for a `contract` diagnostic, using
the exact real pre-migration `strategy_outcomes.json` shape. Result:
attempt 3 selects `FixStrategy.REGENERATE_MODULE` — the exact selection
Exp079 found permanently skipped. A negative control confirms the fix
isn't overbroad: an entry already on the *current* generation with a
genuine 0/3 record is still correctly skipped (staleness invalidation
doesn't grant permanent immunity to a strategy that's currently, validly
proven ineffective). 3/3 pass.

## 4. Confirmed: CRUD health, endpoint inventory, no regressions

- CRUD journey: **PASS, 11/11**, including create/edit/delete/verify-persistence.
- Endpoint smoke test: **100% (15/15)**.
- Endpoint inventory (architecture vs. delivered app): **planned=15,
  actual=21, missing=0** (same clean result pattern as Exp078/079).
- Canary regression check vs. Exp079's baseline (88.2): this run scored
  **91.0**, status `OK` (an improvement, not a regression).
- Endpoint-preservation activation this run: **0** (consistent with §3 —
  never invoked, nothing to preserve or lose). The contract-violation
  fixes that DID land this run (schema mismatches on `PostCreate`,
  `UserCreate`, `CommentCreate`, `TagCreate`) were resolved via
  `patch_file`'s targeted, cache-hit patches — a different, already-
  working code path, unaffected by any of Exp078-081's changes.

## 5. Observatory update

- **Migration event**: fired once, live, this run (§1) — first real
  trigger since Exp081 shipped.
- **Strategy selection**: `patch_file` (attempt 1, succeeded).
  `regenerate_module` was eligible (no longer skipped) but not reached.
- **`regenerate_module` activation**: 0 calls (ladder didn't escalate
  that far) — but proven *reachable* via the offline `RetryManager` test
  in §3, closing the gap live evidence alone couldn't.
- **Endpoint-preservation activation**: 0 (unchanged from Exp079, same
  underlying reason: the code path hosting it wasn't invoked).
- **Final runtime result**: PASS, 91.0/100 (A), deploy-ready, 0 missing
  endpoints, 0 regressions.

Still no permanent dashboard counter added to `reliability_metrics.py` —
same reasoning as Exp078/079: the live activation count for this specific
mechanism remains 0, and a counter built ahead of real activation data
would be speculative.

## 6. Recommendation for the next reliability experiment

The strategy-memory staleness thread (Exp078→081) is now fully closed:
root-caused, fixed, offline-verified, and live-confirmed both for the
skip-check itself (§2) and the full `RetryManager` selection path (§3).
Two options considered for continuing this specific thread further:

- Keep running blog_cms canaries hoping one needs 2+ patch_file rounds
  before regenerate_module gets reached live — low value per dollar,
  since whether that happens is LLM output variance, not something this
  project's fixes control, and the selection logic itself is already
  deterministically proven correct (§3).
- Pivot back to the broader failure taxonomy. This project's own "reduce
  variance" phase framing (and four consecutive cycles narrowly focused
  on one specific, apparently low-frequency middle-rung strategy) suggests
  it's time to zoom back out: check `backend/failure_memory/patterns.json`
  / `generation_log.jsonl` for the current #1 prevalence failure class
  and target that next, rather than a fifth cycle on `regenerate_module`
  specifically.

**Recommended: pivot.** Exp083 should re-run the failure-taxonomy /
prevalence check (same method as the original reliability-pivot audit)
against current telemetry, and pick its target by measured prevalence ×
severity, not by continuing to chase this thread's diminishing returns.

**Deliverables**: this doc, `experiments.md` entry,
`backend/tests/reliability/test_exp082_retrymanager_reactivation.py`,
canary history entry (`exp082-validation-r1`, OK, 91.0). **Cost:
$0.0464, one live generation.**
