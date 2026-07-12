# Experiment 079 — Live Validation of Runtime Endpoint Preservation

2026-07-12. Live, one `blog_cms` canary run (label `exp079-validation-r1`,
provider `cerebras`, `--no-deploy`), $0.0515 total across both the V6
generation phase and the V15 pipeline's own repair loop (85,823 tokens).
Same methodology as Exp074/076: instrumented, unmodified production code,
one app only.

## 1. Method

New script `backend/scripts/exp079_canary.py`, reusing `run_canary.py`'s
internals unmodified (`_check_result`, `_load_history`, `_save_history`,
`_acquire_lock`, `_release_lock`, `CANARY_APPS`'s `blog_cms` entry — same
idea file Exp077 traced its confirmed-live failure in). Instrumentation:

- Wraps `app.repair.orchestrator._regenerate_module` in place. The wrapper
  calls the real (unpatched) `_required_endpoints_map_for_files()` once per
  invocation to log every call (files requested → map returned), snapshots
  every affected backend route file immediately before and after the real
  call whenever that map is non-empty, and AST-extracts (METHOD, path)
  tuples from each snapshot to check every required endpoint's presence
  before and after.
- Wraps `run_canary.generate_project_v15`'s bound name (the actual call
  site inside `_check_result`) to capture the raw pipeline result
  (`project_path` isn't in `_check_result`'s filtered return dict, but this
  canary needs it) without changing `_check_result`'s contract.
- Post-run: reads the final project's `metadata.json` for the
  Architect-planned `api_endpoints` and calls
  `endpoint_validator.extract_actual_backend_routes()` (unmodified, real
  function) against the actual delivered project — same ground-truth diff
  Exp077 used.

All production code paths ran for real and unmodified; only observed.

## 2. Result: no activation occurred this run — and a clear reason why

```
_required_endpoints_map_for_files() calls: 0 total, 0 activated
_regenerate_module() calls with active preservation: 0
blog_cms: planned=15 actual=21 missing=0
EXP079 CANARY: no regression, no endpoint loss detected
```

`_regenerate_module()` was never invoked this run, at all — not "invoked
with an empty map," literally zero calls. Tracing the retry log explains
why:

```
[retry] Attempt 1/5: Patch affected files with current model         (patch_file)
[retry] Attempt 2/5: Patch with enriched prompt                       (patch_file)
[retry] Skipping regenerate_module for pattern 'contract' -- proven ineffective in past runs, escalating
[retry] Skipping switch_model for pattern 'contract' -- proven ineffective in past runs, escalating
[retry] Attempt 3/5: Redesign architecture from scratch (nuclear option)
```

`app/retry/manager.py`'s `RetryManager.next_strategy()` consults
`strategy_memory.should_skip(pattern_id, strategy)` before using the
escalation ladder's default strategy for an attempt: if a (pattern,
strategy) pair has been tried ≥3 times across *all past runs* with zero
successes, it's treated as "proven ineffective" and skipped straight past.
Checked `backend/failure_memory/strategy_outcomes.json` directly:

```
AttributeError  regenerate_module  {successes: 0, tries: 3}
SyntaxError     regenerate_module  {successes: 0, tries: 2}
api             regenerate_module  {successes: 0, tries: 3}
contract        regenerate_module  {successes: 0, tries: 3}
```

`contract` — the exact pattern this run hit, and the same general failure
category Exp077's confirmed-live incident traced through — has a
permanent 0/3 record for `regenerate_module`, so `RetryManager` now always
skips it for that pattern. These records were almost certainly generated
**before** Exp078's fix existed, when `_regenerate_module()`'s calls to
`generate_architecture_fix()` had no endpoint-preservation context at all
(Exp077's Bug 2) — a strategy that structurally couldn't do its job would
naturally never "improve the score," poisoning its own future eligibility
regardless of whether the underlying bug later gets fixed.

Separately, and independently of the strategy-memory question: this
specific run's static-validation loop (inside the V6 generation phase,
*before* the V15 repair loop that Exp078 touches even starts) already
recovered all 8 `post_routes.py` endpoints correctly on its own
(`Post-fix 3: PASS`, confirmed by inspecting the delivered
`post_routes.py` directly — all 8 original endpoints plus 2
frontend-invented `/posts/{id}/comments` ones are present). So even
without the strategy-memory blacklist, this particular run's error
trajectory never produced a "recovered-then-threatened-by-a-runtime-rewrite"
scenario for the preservation mechanism to intervene in.

## 3. What this run does confirm

- **No regression**: forge score 88.2/B, deploy-ready per the pipeline's
  own gate, build/runtime dimensions both `True`, CRUD journey `11
  passed / 0 failed` on the final attempt, endpoint smoke test 100%
  (15/15).
- **No endpoint loss**: final ground-truth diff (architecture vs.
  delivered app) — 15 planned, 21 actual (extra frontend-invented
  endpoints, harmless), **0 missing**.
- **Exp078's fix itself introduces no regression** — the code path is
  correctly wired and inert when not invoked, consistent with Exp078's own
  offline test (`test_regenerate_module_unrelated_repair_still_works_with_no_architecture`).
- **Cannot yet claim "activates when required" was demonstrated live** —
  the one thing this cycle's success criteria most wanted direct evidence
  of. Not from any flaw in Exp078, but because a separate, pre-existing
  mechanism (the strategy-memory blacklist) currently prevents
  `_regenerate_module` from ever being tried for the `contract`,
  `AttributeError`, `api`, and `SyntaxError` patterns.

## 4. Why not expand to a second canary run

Constraint was "minimal Cerebras usage, one blog_cms-shaped canary unless
evidence requires expansion." A second run of the same idea would almost
certainly reproduce the same `contract` pattern and the same
blacklist-driven skip — burning more budget for no new evidence, since the
blocker is structural (persisted in `strategy_outcomes.json` across *all*
runs), not run-to-run LLM variance. Stopping here and reporting the root
cause is the higher-value use of the remaining budget than a second,
predictably-identical attempt.

## 5. Observatory update

- **Activation count (this run)**: 0
- **Preserved endpoint count (this run)**: 0 (mechanism never invoked)
- **Runtime outcome**: PASS — 88.2/100 (B), deploy-ready, 0/15 planned
  endpoints missing from the delivered app
- **Remaining failure taxonomy item (new, discovered this cycle)**:
  strategy-memory blacklist (`app/retry/strategy_memory.py`'s
  `should_skip()`) has `regenerate_module` permanently skip-listed for 4
  patterns (`contract`, `AttributeError`, `api` at 0/3 tries;
  `SyntaxError` at 0/2), almost certainly poisoned by pre-Exp078 history
  when the strategy's endpoint-preservation wiring was dead code. No
  permanent dashboard counter added to `reliability_metrics.py` this cycle
  — same reasoning as Exp078: there is no real activation data to show yet
  (still 0), and adding a counter ahead of any evidence would be
  speculative. This doc + `experiments.md` is the record until a run
  produces real activation data.

## 6. Recommendation for the next experiment

**Exp080: resolve the strategy-memory blacklist poisoning
`regenerate_module`.** This is now better-evidenced and higher-impact than
attempting another live-validation-only cycle for Exp078: even a
perfectly-working preservation mechanism cannot matter in production while
the strategy that hosts it is structurally skipped for 4 of its most
common failure patterns. Two independent, scoped options worth
considering (root-cause first, pick one, don't do both blind):
  a. Time-box `should_skip()`'s lookback (or reset the specific
     pre-Exp078 entries) so records predating a relevant code fix don't
     permanently disqualify a since-repaired strategy.
  b. Confirm via `strategy_outcomes.json` timestamps (if available) or
     generation_log correlation whether these specific 0/3 records
     actually predate Exp078, before changing any skip logic — don't
     assume without checking.

Only after that's resolved (or deliberately deferred) would a further live
validation cycle for endpoint preservation specifically be worth the
Cerebras spend — right now it would very likely reproduce today's same
non-result for the same structural reason.

**Deliverables**: this doc, `experiments.md` entry,
`backend/scripts/exp079_canary.py`,
`backend/benchmark_results/exp079_endpoint_preservation_invocations.json`
(raw run data), canary history entry (`exp079-validation-r1`, BASELINE,
score 88.2). **Cost: $0.0515, one live generation.**
