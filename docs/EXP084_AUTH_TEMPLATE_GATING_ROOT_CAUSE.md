# Experiment 084 — Root Cause Investigation of Auth Template Gating

2026-07-12. Investigation only, $0, zero Cerebras calls (not required —
the full mechanism was conclusively traced via direct code reading and
process-of-elimination across every code path that touches
`auth_routes.py`, without needing a live reproduction).

## 0. Correction to Exp083's evidence first

Before tracing the real mechanism, an important correction: Exp083's
"100% correlation with `_patch_auth_routes` never firing" was **not
real evidence** — `app/services/deterministic_patcher.py::_run_patch_isolated`
does `counts[key] = fn(*args, **kwargs) or 0`, and `_patch_auth_routes()`
has no `return` statement anywhere in its body (implicitly returns
`None`), so `None or 0` evaluates to `0` **unconditionally, on every
call, whether injection fires or not**. Confirmed directly: queried
every one of the 98 `generation_log.jsonl` entries (successes and
failures alike) — `prevention_counts._patch_auth_routes` is `0` in
**100% of all of them**, with no exceptions. This metric is completely
uninformative and should not be used as evidence again until fixed. The
actual root cause below was traced independently, by reading every call
site of `_patch_auth_routes`/`run_deterministic_patches` directly, not
by relying on this counter.

## 1. Selected failing generation

`2026-07-11T20:59:05`, idea = "A todo list app with user accounts..."
(`benchmarks/golden/01_todo.txt`, verbatim), `dominant_errors =
["AttributeError: 'SignupRequest' object has no attribute 'userna[me]'"]`,
`fix_count=3`, `succeeded=false`, `final_score=74.4`. Representative of
all 9 occurrences of this exact error (fix_count 3–5 across all of them,
scores clustered 70.7–74.4, 100% `succeeded=false`).

## 2. End-to-end trace through every stage

- **Architecture**: the Architect's plan doesn't dictate schema field
  names directly; that's the Backend wave's job.
- **Wave 2 / Wave 2.5** (`app/services/parallel_backend_service.py`):
  model class normalization/shimming happens entirely **in-memory** on
  the `FileResult` list (confirmed by reading the code — no disk I/O in
  this section), so by the time `write_files()` runs, any `user.py`/
  `users.py` shim already exists in the file set. This rules out a
  Wave-2.5-timing race (Exp083's original hypothesis) — the ordering is
  actually fine.
- **Stage 6 (write files)**: `write_files()` writes everything to disk,
  including Wave 2.5's shims.
- **Auth patch, first pass** (`_run_initial_deterministic_patches`,
  called right after Stage 6, `app/services/v6_orchestrator.py:298`):
  runs `run_deterministic_patches(project_path)` with **no** skip flag —
  `_patch_auth_routes()` correctly injects the known-good template here
  in the normal case.
- **Validation loop / fix attempts**: targeted `patch_file`-style fixes,
  none of which touch the auth-injection gate.
- **Architecture Repair** (triggered when a validation error matches
  `ARCHITECTURE_ERROR_MARKERS` — "Missing symbol", "Missing APIRouter",
  "No endpoints found", "Undefined symbol" — for **any** file, not
  specifically auth-related): calls `generate_architecture_fix()` (an
  LLM call) with **`required_exports={}`, `required_endpoints={}`,
  `existing_symbols={}`** — all three protective context params empty —
  then writes back whatever files the LLM decided to return via
  `write_fix()`. **This is the exact mechanism.**
- **Repair**: same pattern duplicated in `repair_project()`'s own
  Architecture Repair block (`v6_orchestrator.py:1187-1211`) for the
  repair-loop path.
- **Runtime**: the app crashes with `AttributeError` on the first
  `POST /auth/register`/`/signup` call, exactly matching the recorded
  dominant error.

## 3. Which of the 7 failure shapes applies

**"Patched then overwritten"**, more precisely: **the correct template
gets overwritten by Architecture Repair's LLM output, and the mechanism
that would normally detect and re-fix this is deliberately disabled at
exactly this call site — three independent, individually-confirmed
gaps stack to make it permanent:**

1. **The re-injection gate is explicitly turned off here.**
   `run_deterministic_patches(project_path, skip_protected_injections=True)`
   is called immediately after Architecture Repair's `write_fix()` calls
   (`v6_orchestrator.py:667` and its `repair_project()` twin at `:1202`).
   The function's own docstring: *"skip_protected_injections=True: skip
   auth_routes.py and auth_utils.py injection. Pass True when calling
   after Architecture Repair so the repair's output is not overwritten
   by the static template."* This is the **only** two call sites in the
   entire codebase that pass this flag — every other call
   (`v6_orchestrator.py:102/850/1234`, `pipeline.py:459`,
   `orchestrator.py:1141`) uses the default (`False`), meaning
   `_patch_auth_routes` *would* fire and self-heal on the very next
   cycle **if the flag weren't there**. This single flag is the reason
   the bug is 0%-self-healing instead of being fixed on the next
   ordinary patch pass like almost everything else in the taxonomy.

2. **The intended safety net only checks endpoint existence, never
   field correctness.** Both Architecture Repair call sites immediately
   run `ensure_auth_completeness()` right after the skip — per its own
   comment, *"the independent, unconditional safety net for when that
   trust turns out to be misplaced."* Read `check_auth_completeness()`
   directly (`app/repair/auth_completeness.py:218-288`): it verifies (a)
   `REQUIRED_AUTH_ENDPOINTS` (`POST /auth/register`, `POST /auth/login`)
   exist as route decorators somewhere, (b) the router is actually
   imported and included in `main.py`. **Nothing in this function parses
   or checks the request body schema's field names against what the
   handler code accesses.** A `SignupRequest` missing `.username` is
   invisible to this check as long as the route path itself still
   exists and is wired — which it is, since Architecture Repair's LLM
   output still defines `def signup(req: SignupRequest, ...)` at the
   right path. Confirmed via `AUTH COMPLETENESS` dashboard data itself:
   100% "complete" rate, 0% "repaired," 0% "failed" — this check has
   *never once* found anything wrong, consistent with it structurally
   being unable to see this bug class.

3. **The one existing semantic guard for this EXACT bug shape is scoped
   same-file-only, and the architecture's own natural schema layout is
   cross-file.** `app/services/fix_writer_service.py::_check_request_field_consistency`
   (Exp064) is the write-time gate that inspired this very failure
   string — its own docstring example is literally *"a route handler
   reads `req.username` while the SAME file's `SignupRequest(BaseModel)`
   class never declares a `username` field."* But its own comment is
   explicit: *"Deliberately NOT a generalized semantic analyzer: no
   cross-file resolution... If nothing in the file looks like this
   shape, the check is a no-op."* `_collect_basemodel_classes()` only
   walks the **current file's** AST. The deterministic template
   deliberately defines `SignupRequest` inline in `auth_routes.py`
   (so this guard *would* catch a template-shaped file) — but ordinary
   LLM-authored architecture (and very plausibly Architecture Repair's
   own output, matching the project's existing convention) defines
   request schemas in `app/schemas/*.py` and imports them into routes,
   exactly the cross-file shape this guard was deliberately scoped to
   skip. So even the one check purpose-built for this exact error
   message doesn't fire when the schema lives in a separate file.

Each of these three gaps was confirmed independently by reading the
actual function bodies, not inferred. All three must independently fail
to protect for the bug to become permanent — which is exactly what the
telemetry shows (0% self-heal, 100% terminal).

## 4. Dependency analysis (Task 5)

- **Filename pattern / model names**: **not** the dependency (this
  supersedes Exp083's original hypothesis about `has_user_model`'s
  `user.py`/`users.py` check — that gate is unrelated to this specific
  mechanism, and today's `generated_projects/todo_list_app` already has
  both filenames present).
- **Directory layout**: **yes** — whether `SignupRequest` is defined
  inline (same file, the deterministic template's own shape — safe) or
  in a separate schemas file (the architecture's natural shape, common
  for LLM-authored routes — unsafe, invisible to the Exp064 guard).
- **Backend framework**: no variation observed; always FastAPI in this
  codebase.
- **Auth variant** (role-aware vs. simple): not implicated — the bug is
  a generic field-name mismatch, not role-vocabulary-specific.
- **Retry path**: **yes, this is the primary trigger** — specifically
  whether the generation's validation errors include an
  `ARCHITECTURE_ERROR_MARKERS`-matching diagnostic (any file, not
  necessarily auth-related) that routes through the Architecture Repair
  block. Ordinary `patch_file`-only fix attempts never disable the
  re-injection gate and would self-heal.

## 5. Frequency estimate (Task 6)

All 9 recorded `SignupRequest.username` failures share:
`fix_count` ∈ {3,4,5} (multiple attempts made — consistent with reaching
Architecture Repair, which only triggers when earlier simpler fixes
haven't already resolved validation), 100% `succeeded=false`, scores
tightly clustered 70.7–74.4 (never approaching deploy-ready, consistent
with the bug never getting fixed for the rest of that run). By
elimination: **no other code path in the entire pipeline can produce
this specific "permanent, 0%-self-heal" signature**, since every other
call site would have re-injected and fixed it on the next cycle. High
confidence (via code-path elimination) that **all 9 (100%)** share this
exact mechanism — not independently confirmed by replaying each of
their original console logs, since those aren't retained (the on-disk
project state gets overwritten by later regenerations of the same idea,
confirmed directly: today's `todo_list_app` already shows the *correct*
template).

## 6. Candidate implementation for Exp085

**Smallest deterministic correction**: extend `check_auth_completeness()`'s
definition of "complete" to also run a field-consistency check between
the signup/login handler and whichever `SignupRequest`/`LoginRequest`
class it resolves to — **reusing** `fix_writer_service.py`'s existing
`_check_request_field_consistency`/`_collect_basemodel_classes` AST
logic, extended with the one piece of cross-file resolution it explicitly
scoped out (resolve a same-directory or imported schema file when the
request class isn't defined locally in the route file). If the check
fails, `ensure_auth_completeness()` already has the exact trigger-repair
mechanism needed — it already calls `_patch_auth_routes()` unconditionally
when `check_auth_completeness()` reports incomplete; this only needs
"incomplete" to include the new field-consistency signal.

This is deliberately **not**: removing `skip_protected_injections=True`
(risks re-introducing the original problem that flag was added to solve —
clobbering a legitimately-different, correct architecture repair), and
**not** a new semantic analyzer (reuses Exp064's existing, already-tested
AST machinery almost verbatim). Scoped entirely to
`app/repair/auth_completeness.py` (+ possibly importing/adapting, not
duplicating, `fix_writer_service.py`'s existing helper functions) —
`RetryManager`, `_patch_auth_routes`'s own gate, and
`skip_protected_injections`'s existing call sites are untouched.

Recommend Exp085: (a) fix the `_run_patch_isolated`/`_patch_auth_routes`
return-value bug from §0 first (cheap, unrelated but currently masking
real signal for future investigations), (b) implement the extended
`check_auth_completeness()` field check, (c) offline-test against a
reconstructed project fixture matching this exact bug shape (a
cross-file `SignupRequest` missing `.username`'s actual field) before any
live validation.

**Deliverables**: this doc, `experiments.md` entry. No code changes, no
Cerebras calls. **Cost: $0.**
