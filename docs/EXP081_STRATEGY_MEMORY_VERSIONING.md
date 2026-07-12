# Experiment 081 — Version Retry Strategy Memory

2026-07-12. Offline, $0, zero Cerebras calls. Implements Exp080's
recommended correction: a generation-tag mechanism scoped entirely to
`backend/app/retry/strategy_memory.py`, so `regenerate_module`'s
confirmed-stale blacklist (Exp080) invalidates automatically without
discarding any of the genuinely-still-valid history for other strategies.

## 1. Code diff

`backend/app/retry/strategy_memory.py`:

- Added `_STRATEGY_GENERATIONS: dict[str, int] = {"regenerate_module": 2}`
  and `_DEFAULT_GENERATION = 1`. A strategy not listed defaults to
  generation 1 — the same implicit generation every pre-existing entry
  (no `"generation"` field at all) is treated as, so a strategy nobody
  bumps is byte-for-byte unaffected by anything below.
- Added `_migrate(data)`: for every stored `(pattern, strategy)` entry,
  compares `entry.get("generation", _DEFAULT_GENERATION)` against that
  strategy's current generation. If older, resets the entry to
  `{"tries": 0, "successes": 0, "generation": current}` — the *whole*
  entry object, successes included (a success recorded under a since-
  changed implementation is just as confounded as a failure). Entries
  already current are left completely untouched. Returns whether
  anything changed, so the caller only pays for a write when needed.
- `_load()` now calls `_migrate()` on every read and persists the result
  via `_save()` **only if something changed** — so the reset happens
  exactly once (the first load after a generation bump), and every
  subsequent load is a no-op with respect to migration.
- `record_outcome()` now stamps `entry["generation"] = _current_generation(strategy)`
  on every write (new or existing entry) — so a strategy's entries
  progressively carry an explicit generation instead of relying on
  "missing field" to mean generation 1 forever.
- `should_skip()` is **unchanged** — it reads whatever `_load()` returns,
  which is already migrated, so no changes to retry-selection heuristics
  were needed to satisfy this experiment's own constraint.

No changes to `RetryManager` (`app/retry/manager.py`) or
`_regenerate_module` (`app/repair/orchestrator.py`) — scope stayed
exactly inside `strategy_memory.py` and its persistence logic, per this
experiment's constraint.

## 2. Migration behavior

On the first `_load()` call after this change ships:

- Every `regenerate_module` entry across every pattern that has one
  (`AttributeError`, `ImportError`, `SyntaxError`, `api`, `contract` — 5
  entries in today's real file) resets to `{tries: 0, successes: 0,
  generation: 2}`, including `ImportError`'s 1/1 *success* — intentional:
  the strategy's implementation changed, so a prior success is equally
  confounded evidence about code that no longer exists.
- Every other entry (`patch_file`, `switch_model`, `regenerate_arch`,
  across all patterns) is untouched, byte-for-byte — no `"generation"`
  field added, no counts changed — until/unless `record_outcome()` is
  next called for that specific pair, at which point it gets stamped
  going forward (never reset, since its generation already matches).
- The reset is written back to disk immediately (`_save()` inside
  `_load()`), so a concurrent or subsequent process reading the file
  independently sees the corrected state too — not just the in-memory
  caller.

## 3. Regression results

New test file `backend/tests/reliability/test_exp081_strategy_memory_versioning.py`
(11/11 pass), covering exactly this experiment's required scenarios:

- **Migration from legacy entries** — `test_migration_resets_only_the_bumped_strategy`,
  `test_migration_reproduces_exact_production_snapshot` (the latter uses
  today's real, frozen data verbatim, not a synthetic simplification).
- **Generation mismatch** — `test_explicit_older_generation_resets`.
- **Generation match** — `test_matching_generation_is_preserved_exactly`,
  plus `test_future_generation_is_not_touched` (an entry recorded under a
  *newer* generation than currently configured — e.g. a rollback scenario
  — must never be reset; only strictly-older entries are).
- **Persistence across reloads** — `test_reset_persists_and_does_not_repeat`
  (confirms the on-disk file reflects the reset, and instruments `_save()`
  itself to prove zero further writes happen on subsequent loads — the
  reset fires exactly once).
- Additional coverage beyond the minimum ask: `record_outcome()` stamping
  behavior (new + existing-untagged entries), `should_skip()` flipping
  from `True` to `False` immediately post-reset, `should_skip()` staying
  `True` for a never-bumped strategy (`switch_model`), and a full
  accumulate-fresh-evidence-after-reset simulation.

Full `backend/tests/reliability/` suite re-run (48 files, one new):
**43/48 pass**. The same 5 pre-existing, unrelated failures from Exp078's
cycle remain (`test_database_patcher_and_relationships.py`,
`test_exp066_write_pipeline_hardening.py`, `test_exp070_security_phase0.py`
— missing `jose` module, an environment gap — `test_inline_chain_repairs.py`,
`test_semantic_write_validation.py`); none reference `strategy_memory.py`
or `RetryManager`. No new failures introduced.

## 4. Offline replay using today's frozen `strategy_outcomes.json`

Replayed `_migrate()` against a **copy** of the real, current
`backend/failure_memory/strategy_outcomes.json` (not the live file — this
replay is non-destructive; the real migration will fire automatically the
next time the running system calls `_load()`, e.g. the next canary run).

**Changed** (5 entries, all `regenerate_module`, all patterns that had one):

```
AttributeError/regenerate_module: {successes: 0, tries: 3} -> {tries: 0, successes: 0, generation: 2}
ImportError/regenerate_module:    {successes: 1, tries: 1} -> {tries: 0, successes: 0, generation: 2}
SyntaxError/regenerate_module:    {successes: 0, tries: 2} -> {tries: 0, successes: 0, generation: 2}
api/regenerate_module:            {successes: 0, tries: 3} -> {tries: 0, successes: 0, generation: 2}
contract/regenerate_module:       {successes: 0, tries: 3} -> {tries: 0, successes: 0, generation: 2}
```

**Unchanged** (every other entry, verified byte-identical): `AttributeError/patch_file`,
`AttributeError/switch_model`, `ConfigAttributeError/patch_file`,
`ImportError/switch_model`, `SyntaxError/patch_file`, `api/patch_file`,
`api/regenerate_arch`, `api/switch_model`, `browser/patch_file`,
`contract/patch_file` (50/126), `contract/regenerate_arch` (9/42),
`contract/switch_model`.

Direct consequence confirmed via `should_skip()`: the exact condition
Exp079 hit live (`should_skip("contract", "regenerate_module")` →
`True`, permanently) now evaluates to `False` the moment this migration
runs — `RetryManager` will no longer skip `regenerate_module` for
`contract` on the next generation that reaches it.

## 5. Estimated impact on runtime reliability

Same estimate as Exp080, now mechanically unblocked rather than merely
diagnosed: `contract` is the highest-volume failure pattern in the system
(126 `patch_file` + 42 `regenerate_arch` historical tries). Exp078's
endpoint-preservation fix can now actually be exercised against it in
production instead of remaining permanently bypassed, and the fix loop
regains a real middle rung between `patch_file` and the expensive,
21%-successful `regenerate_arch` nuclear option for the system's most
common failure category. Still a hypothesis pending live confirmation —
this cycle only proves the mechanism is now reachable, not that it will
score well once reached.

## 6. Recommendation for Exp082

**Live-validate now.** The blocker Exp079 hit is confirmed resolved
offline; the next canary run against a `contract`-pattern-heavy idea
(`blog_cms` again, or `todo`/`crm` if `contract` doesn't reproduce) is the
first one since Exp078 landed where `regenerate_module` can actually be
selected. Concretely: rerun the same `exp079_canary.py` instrumentation
(still valid, unmodified) with `--label exp082-validation-r1`, and this
time expect to see `_regenerate_module()` calls > 0 with a non-empty
`required_endpoints` map when a `contract`-pattern failure escalates past
`patch_file`. Confirm: (a) `regenerate_module` actually gets selected
(retry log no longer shows "Skipping regenerate_module... proven
ineffective"), (b) endpoint preservation activates within that call, (c)
`strategy_outcomes.json`'s `contract/regenerate_module` entry gains its
first real post-migration try, (d) no regression elsewhere.

**Deliverables**: this doc, `experiments.md` entry, code diff in
`backend/app/retry/strategy_memory.py`, new test file
`backend/tests/reliability/test_exp081_strategy_memory_versioning.py`.
**Cost: $0, zero Cerebras calls.**
