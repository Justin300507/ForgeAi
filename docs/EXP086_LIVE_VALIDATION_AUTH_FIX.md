# Experiment 086 — Live Validation of Cross-File Auth Field Validation

2026-07-12. Live, one `todo` canary (label `exp086-validation-r1`,
provider `cerebras`, `--no-deploy`), $0.0675 / 112,544 tokens. Idea =
`benchmarks/golden/01_todo.txt`, the exact idea text behind all 9 of
Exp083's recorded `SignupRequest.username` failures, per this
experiment's own preference. New script `backend/scripts/exp086_canary.py`,
reusing `run_canary.py`'s internals unmodified — same methodology as
Exp079/082.

## 1. Instrumentation

Wraps `app.services.v6_orchestrator.ensure_auth_completeness` (the name
as bound in `v6_orchestrator`'s own module namespace — the actual call
site inside both Architecture Repair blocks) to log every invocation:
the before/after `AuthCompletenessResult` (`complete`, `reason`,
`field_mismatches`) and repair status. Production code runs for real and
unmodified; only observed.

## 2. Result: Architecture Repair did not fire this run

`ensure_auth_completeness() invocations: 0`. Unlike Exp079/082's
`_regenerate_module` situation, this isn't blocked by anything — the
retry log shows the escalation ladder reached **attempt 3/5
(`REGENERATE_MODULE`)** this run (confirming Exp081's fix continues to
hold: the strategy is reachable), but no diagnostic this run matched
`ARCHITECTURE_ERROR_MARKERS`, so the specific Architecture Repair block
that hosts Exp085's fix was never entered. This means **direct live
observation of the detection+repair mechanism firing is still pending**
— not because of any blocker, but because this run's specific validation
errors didn't happen to be architecture-level ones.

## 3. What this run does confirm

- **The historical bug class is genuinely absent**: `✓ Register: 200 @
  register` and `✓ Login: 200 @ login` — both succeed cleanly. The
  historical failure crashed exactly here with a 500
  (`AttributeError: 'SignupRequest' object has no attribute
  'username'`); a clean 200 on both is the observable signature of its
  absence.
- **Final auth handlers reference valid schema fields** (Task 4),
  confirmed by reading the actual generated file directly
  (`generated_projects/todo_list_app/app/routes/auth_routes.py`):
  ```python
  user = _make_user(req.email, req.password, req.display_name)
  ...
  identifier = _identifier_value(login_field, req.email)
  ```
  Every access is `req.email` / `req.password` / `req.display_name` —
  matching `SignupRequest`'s actually-declared fields. No `req.username`
  anywhere in the final file.
- **Endpoint inventory unchanged**: `planned=14, actual=17, missing=0`
  (extra endpoints are harmless, same pattern as every prior cycle).
- **No regression**: canary status `BASELINE` (no prior `todo`-keyed
  entry in canary history to compare against under this label lineage),
  score 76.9/100 (C).

## 4. A different, unrelated bug capped this run's score — not in scope

Journey: **10/11 steps passed** — the one failure is `List entities: 500`
(`GET /tasks`), root-caused via the pipeline's own diagnostic parser:
`PydanticSerializationError: Unable to serialize unknown type: <class
'app.models.tasks.Task'>` — a response model missing
`ConfigDict(from_attributes=True)`, an already-cataloged failure class
(Exp083's taxonomy: `PydanticSerializationError`, 5 all-time instances)
entirely unrelated to auth or Exp085's fix. Confirmed no new root cause
here (per this experiment's own constraint) — this is a known, separate
class, not something to fix in this cycle.

## 5. Comparison against Exp083's historical failure

| | Exp083 historical (9 occurrences) | This run |
|---|---|---|
| Register/signup | 500, `AttributeError` | **200, clean** |
| `fix_count` | 3–5 (never resolved) | Reached attempt 3/5 for an unrelated reason, unrelated to auth |
| Final score | 70.7–74.4, always `succeeded=false` | 76.9, higher than every historical occurrence |
| Auth handler fields | `.username` (invalid) | `.email`/`.password`/`.display_name` (all valid) |

Score is not directly comparable (different blocking bug), but every
auth-specific signal that previously failed now passes cleanly.

## 6. Observatory update

- **Detection activation**: not exercised this run (Architecture Repair
  didn't fire) — 0 live activations to report yet.
- **Repair activation**: same — 0 this run.
- **Runtime outcome**: PASS on all auth-related steps (register, login,
  logout, re-login), FAIL on one unrelated step (list entities, a
  pre-existing serialization bug). Forge score 76.9/C.
- **Remaining failure taxonomy**: the `PydanticSerializationError`
  class observed this run is already cataloged (Exp083) and unrelated to
  this experiment's scope — worth a future cycle's attention on its own
  merits, not folded into this one.

No permanent dashboard counter added — same reasoning as every prior
cycle in this thread: no real activation data exists yet to justify one.

## 7. Recommendation for Exp087

Two independent, legitimate options, not mutually exclusive:

1. **Keep trying to observe live activation** of Exp085's specific
   mechanism — reuse `exp086_canary.py` unmodified across a few more
   `todo`/`blog_cms` attempts until one happens to hit an
   `ARCHITECTURE_ERROR_MARKERS` diagnostic. Low cost per attempt
   (~$0.05–0.08), but success is gated on LLM output variance, not
   anything under this project's control — matches Exp082's own
   precedent of not chasing this indefinitely once the mechanism is
   already offline-proven (Exp085) and the specific historical bug shape
   is confirmed absent live (this cycle).
2. **Pivot to the newly-surfaced `PydanticSerializationError` on
   list-response endpoints** — observed live this exact run, matches an
   already-cataloged class, and (unverified this cycle, worth checking
   first) may have a similarly narrow, deterministic root cause given
   it's a well-understood FastAPI/Pydantic ORM-serialization shape.

**Recommended: option 2.** The auth-field-validation thread (Exp083→086)
has already produced a fully-diagnosed, implemented, and offline-verified
fix, with this cycle's live run adding confirming (if not activating)
evidence and finding zero regressions. Continuing to spend Cerebras
budget hunting for one specific code path to fire live has diminishing
returns (per Exp082's own established reasoning); a fresh, already-
surfaced bug is the better next target.

**Deliverables**: this doc, `experiments.md` entry,
`backend/scripts/exp086_canary.py`,
`backend/benchmark_results/exp086_auth_completeness_invocations.json`,
canary history entry (`exp086-validation-r1`, BASELINE, 76.9). **Cost:
$0.0675, one live generation.**
