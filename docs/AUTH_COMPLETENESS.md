# Deterministic Auth Route Completeness (Experiment 071)

2026-07-12. Guarantees every generated backend either contains a
complete authentication surface or is deterministically repaired
before runtime — the highest-ROI reliability item identified across
Experiments 068 and 069.

## Part 1 — Audit: where auth routes can disappear

Traced the full path: Planner → Architecture → Generator → Templates →
Repair → Validation → Runtime.

**Finding #1 — the mechanism to prevent this already existed and works
correctly in the common case.** `app/services/deterministic_patcher.py::_patch_auth_routes()`
(line 2181) already injects a known-good, role-aware `auth_routes.py`
(`_build_auth_routes_template()`, defining `POST /auth/signup`,
`POST /auth/register`, `POST /auth/login`, `GET /auth/me`,
`POST /auth/logout`) and wires the import + `include_router` call into
`main.py`, whenever a `User`-like model exists (`app/models/user.py`
or `users.py`) and the existing `auth_routes.py` (if any) lacks the
`_read_password` sentinel. This runs as part of
`run_deterministic_patches()` (line 6393), called from `pipeline.py`
Stage 2 ("Deterministic Patch" per this project's own CLAUDE.md) —
**before** any LLM fix call, exactly the point in the pipeline where
this kind of guarantee belongs.

**Finding #2 — the concrete, evidenced gap.** `app/services/v6_orchestrator.py`
calls `run_deterministic_patches(project_path, skip_protected_injections=True)`
at **two points** (confirmed via direct grep, not assumption): line 666
inside `generate_project_v6()` (the primary initial-generation path)
and line 1191 inside `repair_project()` (the retry/resume path) — both
immediately after an LLM call to `generate_architecture_fix()` writes
its own files via `write_fix()`. `skip_protected_injections=True`
deliberately disables the auth-routes/auth-utils injection at exactly
these two points, on the documented rationale that "the repair's
output is authoritative and shouldn't be clobbered." **This rationale
has a gap**: `generate_architecture_fix()`'s prompt has no explicit
instruction to preserve auth wiring — if its output touches `main.py`
or `auth_routes.py` while fixing an unrelated architecture error, the
one safety net that would normally catch a resulting hole is exactly
the one disabled at that moment. This is the most concrete, evidenced
explanation found this cycle for why `POST /auth/register` 404s
recurred in 9 of 14 of Experiment 068's forensic bundles despite
`_patch_auth_routes()` existing and generally working — confirmed
empirically: both `generated_projects/todo_list_app` and
`.../inventory_manager` currently carry a correctly-wired
`auth_routes.py` on disk (the mechanism works when it runs; the gap
is specifically the two call sites where it's told not to).

**Finding #3 — a second, independently-discovered bug in the injection
mechanism itself.** While building this experiment's own regression
tests, `_patch_auth_routes()`'s main.py-wiring logic was found to have
only two anchor patterns for inserting the `include_router` call:
after an existing `app.include_router(\w+_router)` call, or before
`Base.metadata.create_all`. **If a project's `main.py` has neither**
(a minimal main.py with no other routers yet wired and no metadata
line present at that point) **the insertion silently no-ops while the
function still prints "Wired auth_router into main.py"** — a
false-success message. Confirmed via direct execution (not
speculation): a minimal `main.py` containing only
`from fastapi import FastAPI` + `app = FastAPI()` reproduced the gap
exactly. **Fixed this cycle** (in scope: it's a bug in the auth-wiring
mechanism itself, this experiment's own subject) — added two more
escalating fallback anchors (after the `app = FastAPI(...)` line, then
an unconditional append at end-of-file) and made the "changed" report
honest (compares actual content length, not an unconditionally-set
flag).

**Finding #4 — the `has_user_model` gate is narrow.** `_patch_auth_routes()`
only recognizes `app/models/user.py` or `users.py` as evidence a user
model exists; a project whose user model lives under a different
filename would silently never get auth injected at all. **Not
independently fixed this cycle** (would mean modifying
`_patch_auth_routes()`'s own gate, a change with wider blast radius
than this experiment's scope) — instead, this experiment's own
completeness check (Part 2) reports this case honestly as `"failed"`
rather than silently succeeding or silently failing, so the gap is
visible in telemetry rather than invisible.

## Part 2 — Deterministic Completeness Check

New module: `backend/app/repair/auth_completeness.py`.

`check_auth_completeness(project_path)` is a pure, read-only,
AST-based scan (not string/regex matching against decorator literals —
avoids both false negatives from router-prefix combinations and false
positives from routes mentioned only in comments/docstrings, both
verified by dedicated tests):

1. Walks every `.py` file under `app/routes/` (and `app/main.py`
   itself, defensively) looking for `@X.<verb>("...")` decorators.
2. Resolves each router variable's `APIRouter(prefix=...)` value (if
   any) and combines it with the decorator's own path to compute the
   effective route path — `APIRouter(prefix="/auth")` +
   `@auth_router.post("/register")` correctly resolves to
   `/auth/register`.
3. Checks the two hard-required endpoints (`POST /auth/register`,
   `POST /auth/login`) exist somewhere. Checks two recommended-but-not-required
   endpoints (`GET /auth/me`, `POST /auth/logout`) — missing, these
   are reported but do not block a "complete" verdict, per this
   experiment's own "if architecture requires it" instruction and the
   concrete evidence (Exp068's bundles never showed a `/me` or
   `/logout` failure).
4. Confirms whichever router actually defines the required endpoints
   is genuinely reachable: both imported into `main.py` (`from
   app.routes.X import Y`) and passed to `app.include_router(Y)` — a
   route existing as dead code in an unincluded file is reported as
   incomplete, not complete.
5. Flags (non-fatal) duplicate registrations of the same
   `(method, path)` across multiple files — a real drift signal
   (`_patch_forward_role_to_duplicate_registrars`'s own docstring
   documents exactly this shape occurring live), but not itself what
   makes an endpoint unreachable if at least one registrar is wired.

## Part 3 — Template validation

Deliberately does **not** diff the generated `auth_routes.py` against
the canonical template byte-for-byte. Per this experiment's own "do
not overwrite application logic" instruction: a working, non-canonical
implementation (different variable names, a different user-storage
mechanism, whatever) that correctly serves the required endpoints and
is correctly wired is reported as `complete` and is never touched —
confirmed by `test_template_drift_recognized_as_complete_not_overwritten`,
which checks the file's mtime and content are genuinely untouched, not
just that the test doesn't visibly fail.

## Part 4 — Repair integration

`ensure_auth_completeness(project_path, project_name)`:

1. Runs the check. If complete, logs and returns immediately — zero
   writes, confirmed by `test_ensure_auth_completeness_is_noop_on_already_complete_project`.
2. If incomplete, calls the **existing, already-tested** injection
   functions (`_patch_auth_utils`, `_patch_auth_requirements`,
   `_patch_auth_routes` — imported, not reimplemented) — this
   function's value is running that repair unconditionally at the two
   points `skip_protected_injections=True` would otherwise have
   suppressed it, not replacing the template mechanism itself.
3. Re-checks. Reports `"repaired"` if now complete, `"failed"` (with
   the specific reason) if not — **never escalates to an LLM call**,
   per this experiment's explicit rule. A `"failed"` result (e.g. no
   user model found under a recognized filename) is an honest report
   of a deterministic-repair limit, not a silently-swallowed failure.

**Wired in** at exactly the two gaps Finding #2 identified:
`v6_orchestrator.py` lines ~666 and ~1191 (line numbers shifted
slightly by this cycle's own added comments), immediately after each
`skip_protected_injections=True` call.

## Part 5 — Tests

`backend/tests/reliability/test_exp071_auth_completeness.py`, 16
tests, all passing: missing router, missing endpoint, missing
`include_router`, missing import, duplicate registration (non-fatal
when one registrar is wired), partial auth (recommended-only gaps
don't block completeness), template drift (working-but-different
implementation left untouched), 4 false-positive-avoidance tests
(prefixed router, comment-only mention, trailing slash, malformed file
doesn't crash), repair-scenario tests (missing router repaired, no-user-model
correctly reported as failed, already-complete is a true no-op), and
2 forensic-bundle replay tests.

**Bundle replay, precisely defined**: Experiment 068's 14 forensic
bundles (`backend/failure_memory/bundles/*.json`) contain
request/response telemetry, not full generated source — this
experiment has no live server and makes no LLM call (its own explicit
rules), so "replay" means reconstructing the exact *observable
symptom* as a synthetic fixture and confirming
`ensure_auth_completeness()` would have prevented it, not re-running
the original generation byte-for-byte. Verified against the actual
bundle files (read directly this cycle, not from memory): **9 of 14**
(`FR-000002` through `FR-000005`, `FR-000007` through `FR-000011`) are
exactly `POST /auth/register → 404 {"detail": "Not Found"}` against
`todo_list_app`. A second replay test confirms the other 5 bundles'
symptoms (a seed FK error, a test-harness routing bug, 3 unrelated
`PUT /products/{id}` 405s on `inventory_manager`) do **not** false-positive
this check — an already-complete auth surface is correctly reported
complete even when other, unrelated endpoints are broken.

## Part 6 — Observatory

New telemetry: `backend/failure_memory/auth_completeness_log.jsonl`
(append-only JSONL, matching this project's own `generation_log.jsonl`
convention), one record per `ensure_auth_completeness()` call:
`{timestamp, project_name, status, missing_required_before/after, reason_before/after}`.

New `app/memory/reliability_metrics.py::compute_auth_completeness_metrics(window=30)`
reads this log and returns `{complete, repaired, failed, repair_success_rate, recent_failures}` —
mirrors `compute_prevention_rate()`'s existing pattern exactly, reused
not reinvented. Wired into `compute_observatory()`'s return dict
(`"auth_completeness"` key) — since `main.py`'s `/observatory` route
already returns `compute_observatory()`'s output wholesale, this
metric is automatically exposed via the API with zero additional
route changes. Also wired into `scripts/failure_report.py`'s CLI
dashboard (`render_auth_completeness_dashboard()`), verified rendering
correctly (`(no architecture-repair checks recorded yet)` when the log
is empty — honest "no data" reporting, not a misleading default).

**Explicit scope boundary**: the metric is computed, telemetry-logged,
and API-exposed. The `Observatory.jsx` frontend page was **not**
updated to display it — that's a React/UI change outside this
backend-focused, $0, 1-2-hour experiment's evident scope. Stated
here rather than silently left as an assumed-done item.

## Success criteria — verified, not just claimed

"Every generated backend either contains complete authentication, or
is deterministically repaired before runtime" — verified via:
`test_ensure_auth_completeness_repairs_missing_router` (a project with
zero auth surface is fully repaired and re-verified independently),
`test_replay_exp068_bundle_missing_auth_register_404` (the exact,
real, most common failure shape from Exp068 is prevented), and the two
`v6_orchestrator.py` wiring points closing the one concrete gap
(Finding #2) where the pre-existing mechanism was told not to run.
The one honest exception: a project whose user model isn't findable
under `user.py`/`users.py` (Finding #4) reports `"failed"`, not a
false `"complete"` — visible in telemetry for a future, appropriately-scoped
follow-up rather than silently unresolved.
