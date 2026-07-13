# Experiment 097 — Root Cause Investigation of the Current Top AttributeError Class

2026-07-13. Investigation only, $0, zero Cerebras calls, zero code
changes. Follows the established methodology: root-cause via real
telemetry, real generated projects, and the actual patcher source —
no reconstruction, no speculation.

## 1. Collected failures, deduplicated (Tasks 1, 3)

`failure_memory/generation_log.jsonl` has 16 `AttributeError`-tagged
records (of 105 total). Grouping and excluding already-closed classes
per this task's explicit instruction:

| Class | Count | Status |
|---|---|---|
| `SignupRequest.username` | 9 | **Already closed** (Exp083-086, fix commit `0d2c74d` @ 2026-07-12 23:36 IST — all 9 log entries timestamped 2026-07-11 20:41–23:24, hours *before* the fix; none post-date it) |
| `ConfigAttributeError` (`Config` object missing `DATABASE_URL`/`CORS_ORIGINS`/etc.) | 3 | **Already closed** (fixed by commits `f2721c5` @ 2026-07-06 12:34 IST and `85514e5` @ 2026-07-07 — the 3 log entries are timestamped within minutes of/just before these exact fixes) |
| `User.created_at` missing (2026-07-06) / `'str' object is not callable` (2026-07-07, mislabeled — actually a `TypeError`) | 2 | Historical, pre-dates the current 30-record telemetry window entirely; not investigated further (out of scope: neither recurs recently) |
| **`User.name` missing** (2026-07-11T23:00:32Z) | 1 | **Active, unclosed** |
| **`UserCreate.password` missing** (2026-07-12T09:02:39Z) | 1 | **Active, unclosed** |

**Deduplication (Task 3)**: the two active records share the same
`architecture_hash` (`1c3ab9664c1e`) and identical idea text (the
canary suite's `01_todo.txt`) — consistent with this project's LLM
response cache making architecture-generation deterministic for a
repeated identical prompt. But they are **not** retry-bundle duplicates
of one run: they are ~12 hours apart, far longer than a single
repair-loop's internal retry cadence (seconds/minutes, confirmed by
every prior bundle-timestamp analysis in this series, e.g. Exp094's
inventory_manager cluster spanning 76 seconds). They are two genuinely
distinct generation incidents of the same recurring idea, each
independently reproducing a *different* specific instance of the same
underlying bug class (see §4).

Cross-checked against `failure_memory/patterns.json` (all-time,
467 runs): a separately-tracked `AttributeError` bucket shows **22
lifetime occurrences** (vs. `ConfigAttributeError`'s own separately-
tracked 13, confirming Config errors were always a distinct, now-closed
class) — this generic bucket has been recurring throughout the
project's history without ever being specifically root-caused until
now. `gym_tracker` appears 3× in its sampled examples — its current
on-disk model (`app/models/users.py`) has accumulated **three separate,
overlapping credential columns** (`password`, `password_hash`,
`hashed_password`) — direct evidence of this same bug class having been
"resolved" in the past by *adding* a missing column rather than fixing
the code that referenced the wrong name, leaving scar tissue rather
than a clean fix.

## 2. Taxonomy (Task 2)

- **Missing attribute (ORM model)**: `User.name` — bare/typed model
  class attribute access where the model doesn't declare that column.
- **Wrong schema field (Pydantic)**: `UserCreate.password` — instance
  attribute access on a Pydantic schema parameter where the schema
  doesn't declare that field.
- **ORM attribute**: same mechanism as "missing attribute" above (this
  project's own patcher, see §3, doesn't distinguish these as separate
  cases — both are `ast.Attribute` access on a name resolved to a known
  class).
- **Service attribute**: none found — every confirmed active instance
  is route/seed-code-level, not a service-layer file.
- **Other**: the already-closed classes in §1's table.

## 3. Representative trace through the pipeline (Task 4)

Traced the `UserCreate.password` incident (2026-07-12T09:02:39Z,
`fix_count: 3`, `succeeded: false`) using the actual generation
pipeline's own wave structure (confirmed directly from a live log,
Exp096's `sports_league_manager` run, which shows the identical wave
sequence):

1. **Planner**: produces user stories ("register and log in") — no
   literal field-name commitment.
2. **Architect**: declares `db_entities` including `User` and
   `api_endpoints` for auth — again, no column/field-level naming
   commitment; that's deferred to backend generation.
3. **Backend generation, Wave 2 (Models, parallel)**: one LLM call
   writes `app/models/users.py`, choosing its own credential column
   name (`password`, `hashed_password`, `password_hash` — LLM
   variance, confirmed inconsistent across the corpus).
4. **Backend generation, Wave 3 (Schemas, parallel)**: a *separate,
   concurrent* LLM call writes `app/schemas/user.py`'s `UserCreate`,
   independently choosing its own field name for the plaintext input.
5. **Backend generation, Wave 4 (Routes, parallel)**: a *third,
   concurrent* LLM call writes `app/routes/seed_routes.py`, directly
   instructed by `app/prompts/shared_contract.py:77-79` ("MANDATORY...
   POST /seed ... Inserts realistic demo data for **every table**...
   at least 5 records per major entity") — which, taken literally,
   includes the `users` table. This call constructs a demo `User`/
   `UserCreate` instance and accesses `.name`/`.password` on it,
   **guessing** field names with zero visibility into what Waves 2/3
   actually decided in their own concurrent calls.
6. **Repair loop**: ran 3-5 fix attempts (both incidents show
   `fix_count` > 0, `succeeded: false`) — the LLM-driven retry loop did
   not resolve this within its budget, the same "existing deterministic
   infrastructure should catch this before spending LLM retries"
   pattern this series has repeatedly proven for other naming-drift
   bugs (e.g. Exp091's ownership-assignment gap, 0% same-run self-heal).
7. **Runtime**: `POST /seed` raises `AttributeError` → 500. The journey
   runner's own seed call is deliberately best-effort and silently
   swallowed (`user_journey_runner.py`'s seed step is explicitly *not*
   a scored journey step — see its own inline comment), so this alone
   doesn't fail the CRUD journey directly — but it means the mandatory
   `/seed` endpoint is silently broken for any real user who calls it
   post-deploy, independent of journey-runner scoring.

## 4. Earliest deterministic divergence (Task 5)

**The divergence occurs at the Wave 3 / Wave 4 parallelization
boundary.** Schemas and routes (including `seed_routes.py`) are
generated by separate, concurrent LLM calls with zero cross-visibility
into each other's exact field-naming choices for the same conceptual
entity. `shared_contract.py`'s "seed every table" instruction forces
the Wave-4 routes call to write User-seeding code that can only *guess*
at Wave 2/3's naming decisions — sometimes correctly (confirmed:
`gym_tracker`'s current regen correctly matches `email`/`display_name`
against its own model), sometimes not (the two confirmed active
incidents). This is not a backend-generation *quality* defect in the
usual sense — each wave's code is individually reasonable — it is a
**missing cross-wave consistency check** between two independently-
generated files describing the same entity.

## 5. Existing infrastructure suitable for extension (Task 6)

`app/services/deterministic_patcher.py`'s `_patch_attr_access_mismatches()`
already exists **specifically for this exact bug class** — "route files
that access `obj.invalid_attr` where `invalid_attr` doesn't exist on
the SQLAlchemy model" (Exp073's own docstring) — AST-based, correctly
class-scoped (only rewrites when the object is *provably* typed to a
known model class via `_infer_model_typed_names`), safe. It didn't fire
for either confirmed active incident, for two distinct, precisely
identified reasons:

1. **`User.name` (ORM case)**: the patcher's `_FIELD_SYNONYMS_PATCHER`
   dict (line 3429) already lists `"name"` and `"password"`-shaped
   synonyms as *values* under other keys (`"username": [..., "name",
   ...]`, `"full_name": [..., "name", ...]`) — but has **no `"name"` key
   of its own**, and **no `"password"` key at all**. Since the lookup is
   `_FIELD_SYNONYMS_PATCHER.get(bad_attr)` (line 3594), a `bad_attr` of
   `"name"` or `"password"` simply isn't recognized as a mismatch worth
   fixing, regardless of what the model actually declares. Confirmed by
   direct code read, not inference.
2. **`UserCreate.password` (Pydantic schema case)**: structurally
   out of this patcher's reach entirely, independent of the dict gap —
   `model_cols` (the function's only source of "known typed classes")
   is built *exclusively* from `models_dir` (`Column(...)` regex over
   `app/models/*.py`, line 3546-3557); `_infer_model_typed_names` can
   therefore never resolve a parameter annotated `user_in: UserCreate`
   as a "known typed class" — `UserCreate` never appears in `model_cols`
   because it's a Pydantic schema, not a SQLAlchemy model. Confirmed by
   reading `_infer_model_typed_names`'s only two type sources (line
   3479-3483, 3486-3490): both gate exclusively on `name in model_cols`.

## 6. Frequency / reliability impact (Task 7)

- 2 confirmed active, unclosed incidents in the current 30-record
  telemetry window (both co-occurring with `POST /seed returned 500`,
  the pipeline's own mandatory demo-data endpoint).
- 22 lifetime `AttributeError` occurrences in `patterns.json` (467
  total runs) — this class has recurred throughout the project's
  history; `gym_tracker`'s accumulated 3-column credential scar tissue
  is direct evidence some past instances were "fixed" by adding a
  column rather than correcting the reference, a worse outcome than a
  clean `_patch_attr_access_mismatches`-style rename would have produced.
- Both confirmed instances show the LLM-driven repair loop running 3-5
  attempts without resolving the bug (`succeeded: false` both times) —
  this is exactly the class of bug this project's own accumulated
  experience (Exp090/091) says deterministic, generation-time correction
  outperforms LLM retries for.

## 7. Recommendation for Exp098

Two independent, both-small deterministic fixes, in one implementation
cycle (they share the same function and test file):

1. **Trivial, zero new logic**: add `"name"` and `"password"` (with
   sensible synonym candidate lists, e.g. `"name": ["username",
   "full_name", "display_name"]`, `"password": ["password_hash",
   "hashed_password"]`) as keys to `_FIELD_SYNONYMS_PATCHER`. Reuses
   100% of the existing, already-safe class-scoped AST machinery —
   the smallest possible candidate for this whole investigation.
2. **Small, new**: extend `_patch_attr_access_mismatches` (or add a
   parallel `schema_cols` map alongside the existing `model_cols`,
   built by scanning `app/schemas/*.py` for Pydantic class field
   declarations the same way `model_cols` scans `Column(...)`
   declarations) so `_infer_model_typed_names` can also resolve
   Pydantic-schema-typed parameters — closing the `UserCreate.password`
   gap structurally, not just adding another dict entry.

Offline-validate both against reconstructed fixtures matching the two
confirmed shapes (`User.name`, `UserCreate.password`) plus a full-corpus
replay (same methodology as Exp092/094) before any live canary,
including a check that `gym_tracker`'s already-scarred 3-column model
doesn't regress (it shouldn't — the fix targets the *reference*, not
existing valid columns).

**Deliverables**: this doc, `experiments.md` entry.
**Cost: $0, zero Cerebras calls, zero code changes.**
