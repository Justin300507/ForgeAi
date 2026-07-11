# Frontend Repair Failure Isolation (Exp055)

Experiment 055, 2026-07-12. Offline, $0, no generation, no LLM calls, no
prompt changes. Direct continuation of Exp053
(`docs/REPAIR_ARCHITECTURE.md` §6, "Remaining limitations"), which
isolated `run_deterministic_patches`'s ~40-call backend sequence but
explicitly left `run_frontend_patches`'s 14-call frontend sequence
untouched, flagging it as the same gap for a future cycle.

## 1. Audit — previous behavior

**Source:** `deterministic_patcher.py::run_frontend_patches` (pre-fix
line ~6238), called from two places:
- `run_deterministic_patches` (its own last step, itself already wrapped
  in Exp053's `_run_patch_isolated` — see §4 below for why that doesn't
  cover this gap)
- `main.py::_resync_frontend` (line ~451), standalone, for the "Check &
  Fix deployed app" frontend-only resync flow

**Repair order:** 14 calls, always in this fixed order (confirmed
against Exp051's `docs/REPAIR_GRAPH.md` §3, which independently audited
the same function and found **zero ordering-dependency comments among
any of the 14 calls**):

```
1.  _patch_frontend_package_json          (returns bool, not int)
2.  _patch_disallowed_icon_packages
3.  _patch_invalid_lucide_icons
4.  _patch_missing_icon_imports
5.  _patch_frontend_auth_field_names
6.  _patch_frontend_signup_password_key
7.  _patch_stale_status_on_error
8.  _patch_unsafe_optional_chain_before_array_method
9.  _patch_response_data_used_as_bare_array
10. _patch_response_data_assumed_wrapped
11. _patch_hidden_loading_status
12. _patch_pagination_component
13. _patch_broken_template_literal_classnames
14. patch_ensure_auth_pages
```

The one ordering comment that exists anywhere near `patch_ensure_auth_pages`
("must run BEFORE the generic orphan route wirer") governs a *different*
call site — `run_deterministic_patches`'s own direct call to
`patch_ensure_auth_pages` at its step 29, relative to its step 31. It does
not constrain anything inside `run_frontend_patches`'s own list. This was
verified by reading `docs/REPAIR_GRAPH.md` §3 directly, not re-derived —
Exp051 already did this audit; Exp055 confirms it's still accurate and
acts on it.

**Exception propagation (before this experiment):** none caught. A raised
exception from any one of the 14 calls propagated straight out of
`run_frontend_patches`, meaning:
- Every call after the one that raised never ran.
- The function's `patched` accumulator (already-counted work from earlier
  calls in the same invocation) was discarded — not returned, not logged.
- The exception reached `run_frontend_patches`'s own two callers
  uncaught. For `run_deterministic_patches`, Exp053's `_run_patch_isolated`
  wrapper around the *entire call* to `run_frontend_patches` catches it —
  but that only tells you "run_frontend_patches failed," with no way to
  know which of the 14 sub-patchers was responsible or whether any of the
  other 13 would have succeeded. For `main.py::_resync_frontend`, there
  was **no exception handling at all** around its direct
  `patched = run_frontend_patches(root)` call — confirmed by reading the
  function body (`main.py:425-459`) directly. A single bad frontend
  patcher could 500 the entire "Check & Fix deployed app" resync
  endpoint, silently discarding whatever the other 13 patchers would
  have fixed.

**Cleanup behavior:** none exists or was needed — every individual
patcher function is self-contained (reads its own files, writes its own
files) with no shared resources, locks, or state across calls that would
need explicit cleanup on a partial failure.

**Return values (before):** a single `int` — the sum of all patch counts
(`bool(...)` for `_patch_frontend_package_json`, raw `int` for the other
13). No structured breakdown of which patcher contributed how much,
whether any failed, or how long each took.

**Logging (before):** none inside `run_frontend_patches` itself. Some of
the 14 individual patcher functions print their own messages internally
on success; there was no failure-path logging at all, since a failure
just propagated as an unhandled Python traceback.

## 2. New behavior

Two new pieces, both in `deterministic_patcher.py`:

**`FrontendPatchResult`** (dataclass): `name`, `success`, `count`,
`duration_ms`, `skipped` (always `False` today — see §3), `exception`
(`None` on success, `"{ExceptionType}: {message}"` on failure).

**`_run_frontend_patch_isolated(results, name, fn, project_path, *, as_bool=False)`**:
runs one patcher inside its own `try/except`, times it, appends a
`FrontendPatchResult` to the shared `results` list, and returns the same
`int` the call site would have gotten before (via the `or 0` /
`bool(...)` conventions, preserved exactly per patcher). On exception:
records `success=False`, logs
`"  [frontend_patcher] {name} raised {Type}: {msg} -- skipping, continuing with remaining frontend patches"`
(same message shape as Exp053's `_run_patch_isolated`, distinguished by
the `[frontend_patcher]` tag), and returns `0` — the exception never
leaves this function.

**`_run_frontend_patches_detailed(project_path) -> tuple[int, list[FrontendPatchResult]]`**:
the actual 14-call sequence, unchanged order, each call now routed
through `_run_frontend_patch_isolated`. Returns both the total count and
the full per-patcher breakdown.

**`run_frontend_patches(project_path) -> int`**: unchanged signature and
return type — calls `_run_frontend_patches_detailed` and returns just the
total, so both existing call sites (`run_deterministic_patches` and
`main.py::_resync_frontend`) work identically to before on the happy
path, with zero code changes required at either call site. The only
observable difference is on the *failure* path: a single bad patcher no
longer aborts the other 13, and `main.py::_resync_frontend`'s
`run_frontend_patches(root)` call can no longer 500 the whole "Check &
Fix" resync because of one frontend patcher bug.

## 3. Observability captured

Every `FrontendPatchResult` carries: `name` (which patcher), `duration_ms`
(wall-clock time for that one call), `success`/`exception` (outcome, and
full type+message on failure), and `count` (files/occurrences changed,
same semantics as the pre-Exp055 return value). `skipped` exists on every
result but is `False` for all 14 calls today — none of them have a
gating condition (confirmed by the §1 audit finding zero ordering/skip
comments among the 14). The field is present now so a future patcher with
a real gating condition (e.g. "only if X file exists") doesn't require a
schema change later.

`_run_frontend_patches_detailed` is the hook a future cycle can wire into
the reliability dashboard (`app/memory/reliability_metrics.py`) or
Observatory for a per-patcher frontend breakdown — not done this cycle
(would be a new integration, not failure isolation; kept out of scope
per "no new repair heuristics").

## 4. Why this is a different layer than Exp053's fix

Exp053 already wrapped the *single call* to `run_frontend_patches` inside
`run_deterministic_patches`'s own ~40-call sequence:
```python
_run_patch_isolated(counts, "run_frontend_patches", run_frontend_patches, root)
```
That protects the *rest of the backend sequence* from a
`run_frontend_patches` failure — if the whole function raised, the ~40-call
backend sequence would still finish. It does nothing for the 14 calls
*inside* `run_frontend_patches` — before this experiment, one of those 14
raising still meant `run_frontend_patches` itself returned nothing
(exception, not a value) and lost all 14 sub-results, even though
Exp053's outer wrapper caught the exception one layer up. Exp055 adds the
isolation one layer deeper, where it was still missing.

## 5. Why execution order remains unchanged

Per the audit in §1, there are no confirmed ordering dependencies among
the 14 calls — moving to per-call isolation carries zero risk of
"continuing would demonstrably corrupt later repairs" (the task's own
carve-out for NOT isolating), so all 14 got the same treatment
unconditionally. The calls still run in the exact original list order —
`_run_frontend_patches_detailed` is a straight-line sequence of 14 calls,
identical order to the pre-fix version, just each one now wrapped.
Verified by a regression test (`test_one_raising_patcher_does_not_stop_the_rest`)
that asserts the exact name-order list, not just the count.

## 6. Limitations

- **`patch_ensure_auth_pages` still runs twice per full generation**
  (once directly in `run_deterministic_patches`'s own sequence, once
  again here as step 14) — a pre-existing fact documented in Exp051's
  `docs/REPAIR_GRAPH.md` §3, not addressed by this experiment (idempotency
  of that double-call was flagged there as a separate, still-open
  question; isolating failures doesn't change whether the double-call
  itself is safe).
- **The per-file inline content-transform chain** inside
  `run_deterministic_patches` (11 chained transforms per `.py` file,
  documented as a separate known gap in Exp053's `docs/REPAIR_ARCHITECTURE.md`
  §6) is untouched — different code path, different function, out of
  scope for a frontend-patches-specific experiment.
- **No per-patcher isolation was added *inside* any of the 14 individual
  patcher functions themselves** — e.g. if `_patch_pagination_component`
  loops over multiple files internally and the 3rd file's regex blows up,
  that's still one failure for that whole patcher (recorded as
  `success=False`, `count=0`), not a partial per-file count. This
  experiment isolates at the patcher-call granularity (matching Exp053's
  precedent exactly), not file-by-file within a single patcher — going
  finer would touch each of the 14 patchers' internal logic individually,
  which is a "new repair heuristic"-adjacent change explicitly out of
  scope this cycle.
- **`_run_frontend_patches_detailed`'s result list isn't wired into any
  telemetry/dashboard yet** — it's available for a future cycle to
  consume, not integrated this one (see §3).

## 7. Verification

13 new tests in `tests/reliability/test_frontend_patch_isolation.py`,
covering: the isolation primitive directly (success, `as_bool` both
directions, `None`-return, exception-catching, never-raises), the real
14-call sequence on a clean project (exact order, all succeed), the
public entry point's return value matching the detailed total exactly,
one patcher raising (other 13 still run, exact order preserved, failure
correctly attributed), the public entry point no longer raising when a
patcher crashes (the concrete `main.py::_resync_frontend` scenario this
fixes), three simultaneous failures all isolated independently, and the
`skipped` field's current always-`False` state plus its presence on every
result.

**Confirmed via `git stash`** (not just asserted): with this experiment's
changes stashed out, the exact same forced-exception scenario used in
`test_public_entry_point_no_longer_raises_when_one_patcher_crashes`
propagates a `RuntimeError` straight out of `run_frontend_patches`,
proving the "before" behavior described in §1 is real, not assumed.

Full existing suite (47 test files as of this experiment, up from 46 —
this experiment's own new file) plus `tests/adr002/test_orchestrator_wiring.py`
re-run and confirmed passing before and after every change.

**Cost: $0.** No generation, no LLM calls, no prompt changes, no new
repair heuristics — only failure isolation and observability around the
existing 14 patchers, whose own logic was not modified.
