# RepairRegistry — Design Document

Experiment 053, Task 3. Module: `backend/app/repair/registry.py`. Tests:
`backend/tests/reliability/test_repair_registry_design.py` (10/10 passing).

**Status: designed and tested standalone. Not wired into any live
dispatch mechanism.** See `docs/REPAIR_ARCHITECTURE.md` §5 for exactly
why — in short, migrating a live, load-bearing, comment-documented
call sequence needs a live canary to validate, which this offline-only
experiment cycle has no access to. This document describes the design so
a future cycle can pick up the migration with a tested starting point,
not a bare idea.

## Why this shape

Preflight.py's `PreflightRegistry` already solves this problem correctly
for its own 17 functions: register with a priority number, `.run()`
executes in priority order, each fix gets its own `try/except`. Exp051's
audit called this out explicitly as the pattern the other three dispatch
mechanisms don't share. `RepairRegistry` is that same pattern, generalized
so it isn't preflight-specific:

```python
from app.repair.registry import RepairRegistry

registry = RepairRegistry(label="my_repair_stage")
registry.register("fix_a", fix_a_fn, priority=10)
registry.register("fix_b", fix_b_fn, priority=20)

@registry.register("fix_c", priority=15)
def fix_c_fn(project_path):
    ...

counts = registry.run(project_path)
# {"fix_a": 2, "fix_c": 0, "fix_b": 1}  -- keys present regardless of order,
# values reflect execution order (a, c, b) via priority
```

## What it guarantees (all backed by a passing test)

| Guarantee | Test |
|---|---|
| Runs in priority order, not registration order | `test_runs_in_priority_order_not_registration_order` |
| Equal priorities run in registration order (stable) | `test_ties_broken_by_registration_order_stable` |
| Unspecified priority defaults sensibly (100 — runs after any explicitly-prioritized early entry) | `test_default_priority_when_unspecified` |
| One entry raising does not stop the rest | `test_one_raising_entry_does_not_stop_the_rest` |
| `fn(...) or 0` convention — `None` return recorded as 0 | `test_fn_or_zero_convention_for_none_return` |
| Real non-zero counts pass through unchanged | `test_fn_or_zero_convention_preserves_real_counts` |
| Args/kwargs reach every entry unchanged | `test_args_and_kwargs_pass_through_to_every_entry` |
| Works as a decorator, not just a direct call | `test_register_as_decorator` |
| `.ordered_names()` exposes the exact execution order for pre-migration assertions | `test_ordered_names_exposes_execution_order_for_migration_assertions` |
| Empty registry is a safe no-op | `test_empty_registry_runs_cleanly` |

## What a future migration would look like

Not attempted this cycle — sketched here so it isn't reinvented from
scratch:

1. Pick the **lowest-risk** dispatch mechanism first. Per
   `docs/REPAIR_ARCHITECTURE.md` §5, that's `run_frontend_patches` (14
   calls, only one has a documented cross-function ordering dependency),
   not the ~40-call backend sequence.
2. Build the registry with `priority` values spaced widely apart (e.g.
   10, 20, 30...) so a later-discovered ordering constraint can be
   inserted between two existing entries without renumbering everything.
3. Assert `registry.ordered_names()` matches the exact current call
   order (`docs/REPAIR_GRAPH.md` §3's numbered list) in a test, BEFORE
   switching the real call site over — this is a $0, offline check that
   catches a transcription error in the priority numbers immediately.
4. Switch the call site, keep the old hardcoded version reachable behind
   a feature flag or an env var for one canary cycle (matching this
   project's own established pattern, e.g. `FORGE_MODEL_DRIVEN_SCHEMA`
   in `project_model_driven_schema.md`), and confirm a live canary run
   produces identical Forge Scores before removing the old path.
5. Only then repeat for `run_deterministic_patches`'s larger, more
   ordering-dependency-heavy sequence.

## Explicit non-goals

- Does **not** attempt to unify mechanisms 3-4 (`deployment_fix_service.py`'s
  error-type dict, `deployed_fixer.py`'s if/elif) — those dispatch on a
  string key, not a fixed sequence; a priority-ordered registry doesn't
  obviously improve them and forcing the shape would be exactly the kind
  of "reduce duplication" that isn't actually reducing anything.
- Does **not** change any individual patcher function's signature — every
  existing `_patch_x(project_path) -> int` function is already
  registry-compatible as-is.
