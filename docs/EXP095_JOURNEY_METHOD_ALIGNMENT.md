# Experiment 095 — Align CRUD Journey Runner with Architecture HTTP Methods

2026-07-13. Offline implementation, $0, zero Cerebras calls. Implements
Exp094's recommended fix: the journey runner's `do_edit()` step now uses
whichever HTTP method (PUT or PATCH) the architecture actually declares
for the detected entity's update route, instead of a hardcoded PUT.

## 1. Code diff (Task 1-3)

`backend/app/runtime/user_journey_runner.py`:

- New `_detect_update_method(architecture, api_prefix, resource)`:
  scans `architecture["api_endpoints"]` for a PUT or PATCH declared on
  the exact `<resource>/{id}` shape (deliberately excluding nested
  action sub-paths like `/posts/{id}/publish`, which may legitimately
  use PATCH for an unrelated state transition without that being the
  resource's canonical update verb). Prefers PUT if both are somehow
  declared for the same shape; falls back to `"PUT"` if neither is
  declared, preserving prior behavior (Task 4).
- `_detect_crud_entity()` now returns `tuple[str, str] | None`
  (`(resource, update_method)`) instead of `str | None` — every existing
  return path funnels through a new `_resolved(resource)` helper that
  calls `_detect_update_method` for the selected resource. Only one real
  call site existed (`run_user_journey`, line 390); updated to unpack
  the tuple, defaulting to `entity="items"`, `update_method="PUT"` when
  no entity is detected at all (Task 4).
- `do_edit()`: `edit_fn = requests.patch if update_method == "PATCH"
  else requests.put`, then calls `edit_fn(...)` instead of the hardcoded
  `requests.put(...)`. Deliberately **not** `requests.request(method,
  ...)` — the journey runner's `requests` is actually
  `_ExchangeRecorder`, which only wraps `post/get/put/delete/patch`
  explicitly for forensic bundle capture; `.request(...)` would silently
  bypass that wrapper and break evidence capture for a failed Edit step.

No generated-code changes, no prompt changes, no new
`deterministic_patcher.py` function — exactly per constraints.

## 2. Offline replay against the two confirmed failing architectures (Task 5)

Ran the actual (now-modified) `_detect_crud_entity()` directly against
both architectures Exp094 confirmed would 405:

```
sports_league_manager: prefix='' -> ('leagues', 'PATCH')
volunteer_management_system: prefix='' -> ('events', 'PUT')
```

- `sports_league_manager` now correctly resolves to `PATCH` — this is
  the fix working exactly as intended.
- `volunteer_management_system` still resolves to `PUT` (unchanged) —
  correct and expected: its selected entity (`events`) has **no update
  endpoint at all** (not even PATCH), so `_detect_update_method` falls
  through to its documented default. This preserves prior behavior
  (still 405s, same as before) rather than inventing a new outcome for
  a defect shape this experiment explicitly didn't target (Exp094 §8
  flagged this as a distinct, smaller sub-case).

**End-to-end confirmation, not just method detection**: built a minimal
stdlib HTTP server reproducing `sports_league_manager`'s exact shape
(`PATCH /leagues/{id}`, **no** `PUT` handler at all — `do_PUT` absent,
so an old-code PUT request gets a generic 501, functionally identical to
FastAPI's 405 for this purpose) and ran the real `run_user_journey()`
against it end to end:

```
Entity detected: leagues
  Register: passed=True detail=200 @ register
  Login: passed=True detail=200 @ login
  Create entity: passed=True detail=201 id=1
  List entities: passed=True detail=200 count=1
  Edit entity: passed=True detail=200
  Delete entity: passed=True detail=204
```

**Git-stash-verified** (this session's established practice — confirm
the fix actually matters, not just that new code runs): stashed
`user_journey_runner.py` back to its pre-fix state and re-ran the
identical replay against the identical fake server — `Edit entity`
failed with `501` (the pre-fix hardcoded PUT), confirming this is a
real fix for a real, reproduced failure, not a no-op. Popped the stash
to restore the fix before continuing.

## 3. Regression check on previously-passing PUT applications (Task 6)

- Full-corpus re-scan of all 49 currently-saved architectures with the
  modified `_detect_crud_entity()`: 46 resolve to `PUT` exactly as
  before (unchanged), 1 (`sports_league_manager`) now correctly
  resolves to `PATCH`, 2 (`todomaster`, `user_management_system`) still
  detect no entity at all (unchanged, unrelated — pre-existing empty
  saved-architecture gap noted in Exp094).
- `tests/reliability/test_role_aware_journey.py` (an existing test using
  a real architecture with `PUT /menu/{id}` against a real minimal HTTP
  server) — reran directly: 2/2 pass, unchanged.

## 4. Full regression suite (Task 7)

Added `backend/tests/reliability/test_exp095_journey_method_alignment.py`
(9 new tests: `_detect_update_method` unit coverage — PUT-declared,
PATCH-only, action-subpath exclusion, no-method default, API-prefix
handling — `_detect_crud_entity`'s new tuple return shape, and the
end-to-end PATCH-only fake-server journey). All 9 pass.

Full `backend/tests/reliability/` suite: **50/53 pass** (52 pre-existing
files + 1 new). The 3 failures are the same pre-existing, unrelated
failures this series has repeatedly confirmed and cited
(`test_exp066_write_pipeline_hardening.py` — stale fixture directory
from a prior local run, `FileNotFoundError`; `test_exp070_security_phase0.py`
— missing `jose` package in this environment, `ModuleNotFoundError`;
`test_semantic_write_validation.py` — 2 write-corruption-replay subtests
unrelated to HTTP method handling). None touch
`user_journey_runner.py`. Zero new regressions.

## 5. Estimated reduction in false JourneyCRUDFailures

Directly eliminates the confirmed 1/49 (2.0%) current-snapshot false-405
(`sports_league_manager`) and removes the latent risk represented by
Exp094's broader 11/49 (22.4%) PATCH-containing-architecture pool for
every future regeneration of those ideas — any future run where the
architect's verb choice lands PATCH on the journey-selected entity now
resolves correctly instead of false-failing. Converts a confirmed
0%-self-heal false failure (Exp094: both historically-bundled runs
retried 3x with zero effect, since the repair loop cannot fix a
test-harness bug by editing generated code) into a $0, deterministic
non-issue — same category of gain as Exp088's
`PydanticSerializationError` fix and Exp092's ownership-assignment
patch. Does not address `volunteer_management_system`'s distinct
"no update endpoint at all" sub-case (1/49 confirmed) — unchanged,
as intended.

## 6. Recommendation for Exp096

Live-validate with 1-2 Cerebras canaries targeting ideas historically
prone to this architect verb choice — a sports/league-management or
task-management idea (both previously observed choosing PATCH) is more
likely to exercise this path than todo/blog_cms/crm, which have
recently resolved to PUT. Confirm: (a) no regression on the overall
Forge Score / journey pass rate, (b) if the architect happens to choose
PATCH for the test-selected entity this run, `Edit entity` passes
instead of false-failing with 405. A null result (architect again
chooses PUT, as `inventory_manager`/`forge_blog_cms`'s current snapshots
did) would be uninformative but not concerning — this is a
low-probability-per-run, high-value-when-triggered condition, consistent
with how Exp093's live validation played out for the Create-path fix.

**Deliverables**: `docs/EXP095_JOURNEY_METHOD_ALIGNMENT.md`, this entry,
code diff in `backend/app/runtime/user_journey_runner.py`, new test file
`backend/tests/reliability/test_exp095_journey_method_alignment.py`.
**Cost: $0, zero Cerebras calls.**
