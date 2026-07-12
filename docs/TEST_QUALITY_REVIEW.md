# Test Coverage Quality Review (Experiment 065, Part 7)

2026-07-12. Offline, read-only. Quality, not quantity — there are
already ~50 test files in `backend/tests/reliability/` and
`backend/tests/adr002/`; this Part doesn't re-count them.

## Redundant tests — clean, checked directly

Sampled the largest file, `tests/reliability/test_preflight_fixes.py`
(862 lines, 70 tests). **No genuine redundancy found** — its pattern
(`adds_when_needed`/`noop_when_absent`/`noop_when_already_present`/
`idempotent`/`missing_file_no_crash`, repeated per patcher) looks
superficially repetitive by name but each variant exercises a distinct
code path. Reported as a clean negative result, not forced into a
finding.

## Missing edge cases — the standout finding of this review

Three safety-critical functions checked directly, against exactly what
the task asked (empty/None input, no-op input, adversarial input):

1. **`write_fix()` (`app/services/fix_writer_service.py`) — the highest-value
   gap found this cycle.** Only 3 tests exist total
   (`tests/reliability/test_semantic_write_validation.py:414,423,431`),
   **all three added by Exp064, and all three only exercise the NEW
   semantic-consistency guard.** The function's *other*, pre-existing
   guard clauses have **zero test coverage**: the
   `if not path or not content: return False` early return, the
   `..`/absolute-path traversal block (directly relevant given
   `docs/SECURITY_REVIEW.md`'s Finding #1 about the sibling function
   `write_files` lacking this same guard entirely — `write_fix`'s
   version of this exact protection is itself unverified by any test),
   the `app/database.py` special-cased early-return branch, and the
   flat-file/package-conflict cleanup logic. **This is the standout
   finding of this Part**: the exact function this cycle hardened twice
   (Exp060's context, Exp064's semantic check) has its *other* safety
   properties completely unverified.
2. **`validate_project()`** — 3 tests
   (`tests/reliability/test_validator_contract_unification.py:223,233,244`):
   additive-diagnostics-key, missing-`main.py`, multi-validator
   aggregation. No test for a malformed/adversarial project (an
   unreadable file, a `main.py` that itself has a syntax error).
3. **`_patch_param_order`** — 6 tests
   (`tests/reliability/test_inline_chain_repairs.py:450-576`), genuinely
   thorough: no-op, fast-skip, unrelated-error-skip, idempotency, the
   Exp054 bracket-type-hint regression, and the Exp057 invalid-reorder
   write-guard. **A model example** the other two functions above
   should be brought up to.

## Missing integration tests

`tests/reliability/test_validator_contract_unification.py` is a real
integration test — it exercises `validator_service.validate_project()`
together with `verification.engine._run_static_validators()` in the
same test (confirmed via import cross-reference). This is the **only**
cross-subsystem integration test found this pass. Broader combinations
(e.g. `deterministic_patcher` + `verification.engine` + `app/repair/orchestrator.py`
together) were not found — not exhaustively ruled out given this pass's
scope, so reported as "none found in the areas checked," not "confirmed
absent everywhere."

## Missing property tests — confirmed zero

`grep -rl "hypothesis" tests/` returns nothing. No property-based
testing anywhere in the suite — every test is example-based.

## Missing replay tests — confirmed no formal harness

No `conftest.py` anywhere under `tests/`, no `fixtures/` directory.
Every "replay the exact bug via `git stash`" instance this cycle
(Exp054/055/057/058/064, per this session's own experiments.md history)
is **hand-rolled inline in its own test file** — there's no shared
utility for "extract this real generated-project file, run the check,
compare before/after." Given this pattern has now been used 5 separate
times, a shared fixture library would compound in value with each
future experiment that needs it.

## Missing performance tests — confirmed zero

No test anywhere asserts a call-count or timing budget (grep for
`call_count`/`perf_counter`-style budget assertions across
`tests/reliability/*.py` found nothing). Despite Exp059's own confirmed
finding of ~20 redundant `os.walk` calls in `validate_project()`
(reverified as still accurate in `docs/PERFORMANCE_REVIEW.md`), no
regression test exists to catch that count silently growing further, or
— if a future cycle fixes it — silently regressing back up later.

---

## Ranked top 5 (by value if closed)

1. **`write_fix()`'s pre-existing guard clauses have zero test
   coverage** — the highest-value gap. This is the exact function
   Exp064 just hardened, and its *other* safety checks (path traversal,
   missing-input handling) are completely unverified — directly
   relevant to `docs/SECURITY_REVIEW.md`'s top finding about the sibling
   `write_files` function's missing guard.
2. **No formal replay-test harness** — every experiment this cycle
   re-solved the same "extract real file, run check, assert" problem
   from scratch; a shared fixture library would compound in value.
3. **No performance-budget regression test** — Exp059's confirmed
   ~20-redundant-scan finding has no tripwire; it could silently worsen
   with no test ever failing.
4. **Zero diagrams for 13+ of ~15 major subsystems** (cross-referenced
   from `docs/ARCHITECTURE_REVIEW.md`'s Part 6) — asymmetric
   documentation investment.
5. **No dedicated generator documentation** — the actual live
   generation pipeline has no current-state doc, only an aspirational
   redesign proposal (`docs/FORGEAI_VNEXT_REPORT.md`).
