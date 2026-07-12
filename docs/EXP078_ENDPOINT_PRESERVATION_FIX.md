# Experiment 078 — Restore Runtime Endpoint Preservation

2026-07-12. Offline, $0, zero Cerebras calls. Fixes the two confirmed gaps
Experiment 077 root-caused: the endpoint-preservation mechanism in
`app/repair/orchestrator.py` was designed to stop runtime-stage full-file
rewrites from silently dropping endpoints the static-validation loop had
already recovered, but had **never activated, for any project, ever**.

## 1. The two bugs (both confirmed by Exp077, fixed here)

**Bug 1 — path separator mismatch.** `_required_endpoints_for_files()`
compared `ep.get("file")` (backslash-separated on this Windows deployment,
e.g. `'app\\routes\\post_routes.py'`) directly against forward-slash
runtime diagnostic paths via `in files`. Always empty. `endpoint_validator.py`'s
`validate_endpoints()` already does the exact `.replace("\\", "/")` this
needs, one function away in the same codebase (line 128).

**Bug 2 — the kwarg was never wired.** `_regenerate_module()`'s backend
path called `generate_architecture_fix(architecture, messages, provider)`
— three positional args only — even though that function's signature
(`app/services/architecture_fix_service.py`) has accepted
`required_endpoints=` since it was written. Even with Bug 1 fixed, nothing
was ever passed through.

## 2. The fix

`app/repair/orchestrator.py`:

- Split `_required_endpoints_for_files()` into a shared
  `_relevant_endpoints_for_files()` (the normalized lookup:
  `(ep.get("file") or "").replace("\\", "/") in file_set`) used by both:
  - `_required_endpoints_for_files()` — unchanged string-block output, now
    fed by the normalized lookup (still used by `_build_fix_prompt`).
  - `_required_endpoints_map_for_files()` — new, returns
    `{file: ["METHOD /path", ...]}`, the dict shape
    `generate_architecture_fix()` already accepts.
- `_regenerate_module()` now calls
  `_required_endpoints_map_for_files(ctx, affected)` and passes it as
  `required_endpoints=` into `generate_architecture_fix()`.
- Added one `print()` at the actual activation point (only when the map is
  non-empty) reporting endpoint count + file count, matching this file's
  existing `[fix] ...` observability convention, so a live run's console/
  generation log makes activation directly visible.

No prompt text changed, no new mechanism invented — both fixes wire up
functionality that already existed and was already the intended design.

## 3. Offline validation

Reconstruction fixture: `forge_blog_cms`'s own
`generated_projects/forge_blog_cms/metadata.json` →
`architecture.api_endpoints` for `post_routes.py` — the exact real, confirmed-
live failure Exp077 traced end-to-end (8 endpoints recovered by static
validation, then dropped again by a runtime-stage rewrite). Confirmed the
architecture-side `file` field is stored backslash-separated
(`'app\\routes\\post_routes.py'`), matching Exp077's claim exactly.

New test file: `backend/tests/reliability/test_exp078_endpoint_preservation.py`
(7 tests, all passing):

- `test_relevant_endpoints_match_despite_backslash_architecture_path` — all
  8 real endpoints now match against the forward-slash runtime path (would
  have returned 0 pre-fix).
- `test_relevant_endpoints_no_match_without_normalization_would_have_failed`
  — characterizes the exact pre-fix bug directly.
- `test_required_endpoints_prompt_block_lists_all_endpoints` /
  `test_required_endpoints_map_shaped_for_generate_architecture_fix` — both
  output shapes verified correct.
- `test_no_endpoints_returned_for_unrelated_file` — no false positives.
- `test_regenerate_module_passes_required_endpoints_to_generate_architecture_fix`
  — the real `_regenerate_module()` function, only the LLM call mocked,
  confirms `required_endpoints` actually arrives non-empty at the call site
  (this is Bug 2, directly exercised).
- `test_regenerate_module_unrelated_repair_still_works_with_no_architecture`
  — an unrelated runtime repair (no architecture context, no matching
  endpoints) is unaffected: `required_endpoints={}`, regen proceeds
  normally, same as before this experiment.

## 4. Regression suite

Full `backend/tests/reliability/` suite run (47 files):

- `test_exp067_regenerate_module_hardening.py` — 21/21 pass. This is the
  test file most directly coupled to `_regenerate_module()`'s signature;
  its mocked `generate_architecture_fix` fakes needed a `required_endpoints=None`
  parameter added (two call sites) to match the new keyword arg — the only
  change required outside `orchestrator.py` itself.
- `test_exp078_endpoint_preservation.py` (new) — 7/7 pass.
- 5 pre-existing failures unrelated to this change (`test_database_patcher_and_relationships.py`,
  `test_exp066_write_pipeline_hardening.py`, `test_exp070_security_phase0.py`
  — fails on `ModuleNotFoundError: No module named 'jose'`, an environment
  dependency gap, not a code regression — `test_inline_chain_repairs.py`,
  `test_semantic_write_validation.py`). None of these five files reference
  `_required_endpoints_for_files`, `_regenerate_module`, `generate_architecture_fix`,
  or `orchestrator` at all (confirmed via grep) — pre-existing, out of this
  experiment's scope, not introduced by this change.
- 42/47 files passed overall, same as the pre-fix baseline for the
  unrelated files.

## 5. Observatory

No dedicated live counter added to `compute_observatory()` /
`reliability_metrics.py` this cycle — deliberately. The existing
`prevention_counts` → `DETERMINISTIC_PREVENTION_CATEGORIES` dashboard slot
is semantically for **pre-runtime** deterministic prevention (caught before
the app ever reaches verification); endpoint preservation is a
**runtime-repair-loop** mechanism, a different pipeline stage, and forcing
it into that slot would mislabel it — exactly the kind of speculative
scope-creep this experiment's constraints rule out.

What *is* now true: every activation is directly observable — the new
`print()` line in `_regenerate_module()` reports endpoint count + file
count whenever the mechanism actually fires, visible in any run's console
output / generation log. Activation count and preserved-endpoint totals
from real runs don't exist yet because no live generation has exercised
this code path since the fix — that requires an actual live run, which
this experiment's own "offline implementation and testing first" scope
explicitly excludes.

## 6. Recommendation for Exp079

**Yes — perform live validation next**, using the same Exp074/076
methodology: run the 3-app canary (or a targeted `blog_cms`-shaped rerun,
since that's the confirmed-live failure shape), grep the generation log
for the new `Endpoint preservation ACTIVE` line, and confirm via
`endpoint_validator.py`'s own before/after diff that `PUT`/`DELETE`/`PATCH
.../publish`/`unpublish` on a route file survive a runtime-stage rewrite
this time. That live run is also the natural point to decide whether a
permanent Observatory counter is worth adding — with real activation data
in hand instead of a speculative one built ahead of any evidence.

**Deliverables**: this doc, `experiments.md` entry, code diff in
`backend/app/repair/orchestrator.py`, new test file
`backend/tests/reliability/test_exp078_endpoint_preservation.py`, minor
signature update in `backend/tests/reliability/test_exp067_regenerate_module_hardening.py`.
**Cost: $0, zero Cerebras calls.**
