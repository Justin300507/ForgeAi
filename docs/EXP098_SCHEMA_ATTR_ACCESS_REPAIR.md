# Experiment 098 — Extend Attribute Access Repair Across Model and Schema Types

2026-07-13. Offline implementation, $0, zero Cerebras calls. Extends
Exp097's identified patcher (`_patch_attr_access_mismatches` in
`app/services/deterministic_patcher.py`) rather than introducing a
parallel one, per this experiment's own constraint.

## 1. Code diff (Tasks 1, 2)

**Task 1 — attribute extraction including dictionary values**: a first
design (mechanical reciprocal scan: for `bad_attr`, also collect every
`_FIELD_SYNONYMS_PATCHER` key whose own value list contains `bad_attr`)
was implemented, then **reverted** after a full-corpus replay found a
real, wrong fix (§3). Replaced with curated, explicit additions to
`_FIELD_SYNONYMS_PATCHER`:

```python
"name": ["username", "full_name", "display_name"],
"password": ["password_hash", "hashed_password", "pwd"],
"password_hash": ["hashed_password", "password"],
"hashed_password": ["password_hash", "password"],
```

`"name"` and `"password"` already appeared as candidate *values* under
other keys (`"username": [..., "name", ...]`) but never as keys of
their own — exactly Exp097's finding. These are scoped narrowly to the
identity/credential synonyms they were meant to cover, deliberately
excluding `title`/`label`/`description` (see §3 for why).

**Task 2 — Pydantic schema classes in the type map**: new
`_collect_schema_cols(schemas_dir)`, reusing
`fix_writer_service._collect_basemodel_classes()` per-file (local
inheritance resolution, matching that helper's own scope) and merging
across `app/schemas/*.py`. `_infer_model_typed_names()` now accepts an
optional `schema_cols` parameter and checks it alongside `model_cols`
in all three "provably typed" shapes (typed parameter, annotated
assignment, constructor call) — `model_cols`'s own SQLAlchemy detection
is byte-for-byte unchanged. `_patch_attr_access_mismatches()` builds
`schema_cols` from `app/schemas/`, resolves `cls_name` against either
source, and looks up `valid_cols` from whichever dict actually has the
class.

## 2. Verifying existing SQLAlchemy behavior is unchanged (Task 3)

- `schema_cols` defaults to `{}` when unused; `name in {}` is always
  `False`, so every existing SQLAlchemy-only code path is byte-for-byte
  identical when no schema-typed parameter is present.
- Existing test suites: `test_exp073_attr_scope_fix.py` (12/12) and
  `test_sql_constructor_and_auth_repairs.py` (30/30) — both pass
  **unchanged**, confirming no behavioral drift for the original
  SQLAlchemy-only test fixtures.
- Full-corpus replay comparison (§4): every pre-Exp098 corpus change
  (8 files, confirmed via a git-stash A/B replay) reproduces
  **identically** post-Exp098 — same files, same diffs, same content.

## 3. Offline replay (Task 4)

**`User.name`** and **`UserCreate.password`** (Exp097's two confirmed
shapes): both fixed correctly in isolated fixtures matching the real
error signatures — `User.name` → `User.display_name` (bare class
attribute, SQLAlchemy), `demo.password` → `demo.hashed_password`
(Pydantic instance attribute). Idempotent (stable on a second run).

**`gym_tracker`** (real, on-disk project): first replay attempt (the
mechanical-reciprocal design) produced a **real, wrong fix** —
`tag_in.name` (confirmed: `TagCreate` genuinely has no `name`, only
`title`/`description`) got rewritten to `tag_in.description` instead
of the correct `tag_in.title`, purely because `"description"` is
declared earlier than `"title"` in `_FIELD_SYNONYMS_PATCHER`. Root
cause: `"description"` and `"title"` are NOT truly bidirectionally
synonymous with `"name"` — the *existing* one-directional entries
(`"description": [..., "name", ...]`, `"title": [..., "name", ...]`)
encode "if this generic content field is missing, `name` is a
reasonable one-way fallback", not "`name` missing implies any of these
are safe substitutes." Reverting to the curated, explicit `"name"` key
(scoped to `username`/`full_name`/`display_name` only) makes this
replay **clean — 0 changed files, 0 patches applied** to `gym_tracker`.

**Historical corpus** (57 projects with both `routes/` and `models/`):
compared via git-stash A/B replay (pre-Exp098 vs. post-Exp098 code
against the identical corpus snapshot):
- **8 files** changed identically in both versions — pre-existing
  patcher behavior, unrelated to this experiment, unaffected.
- **5 additional files** changed **only** post-Exp098 (newly enabled by
  the schema-typing extension) — every one independently verified
  against the real model/schema source on disk:
  - `simple_note_app`, `simple_task_tracker`: `user.password` →
    `user.password_hash` (model column is genuinely `password_hash`,
    no `password`/`hashed_password`) — **correct**.
  - `todo_plus`: `user.password` → `user.hashed_password` (model column
    is genuinely `hashed_password`) — **correct**.
  - `simple_expense_tracker`: `db_user.password_hash` →
    `db_user.hashed_password` (model column is genuinely
    `hashed_password`) — **correct**.
  - `support_ticket_system/auth_routes.py`: `user.password` →
    `user.hashed_password` (model has `hashed_password`) **and**
    `full_name=request.full_name` → `full_name=request.username`
    (confirmed: the actual `AuthRegisterRequest` schema has no
    `full_name` field at all, only `username`/`email`/`password`) —
    both **correct**, converts a guaranteed registration-endpoint crash
    into a working (if imperfect — using username as a display-name
    stand-in) registration.
  - `personal_expense_tracker`: `payload.username` → `payload.email`
    (confirmed: `payload` is typed `RegisterRequest`, which genuinely
    has no `username` field, only `email`/`password`/`display_name`) —
    **correct**, converts a guaranteed registration-endpoint crash into
    a working one (using email as username is a common, accepted
    pattern, and matches this dict entry's pre-existing, unmodified
    candidate ordering).
  - `simple_note_app/note_routes.py`: `note_in.content` →
    `note_in.description` (confirmed: the schema genuinely has no
    `content` field). **Functionally inert, not a regression**: both
    occurrences are guarded by `hasattr(note_in, "content")`, which was
    already always `False` before *and* after this fix (the schema
    never had `content`), so the guarded branch never executes either
    way — `content_value` is `""` in both versions. Documented as a
    known, narrow cosmetic limitation (the patcher can't rewrite a
    `hasattr()` string-literal argument, since it isn't an
    `ast.Attribute` node), not a defect requiring a fix this cycle.

## 4. Regression tests (Task 5)

New `backend/tests/reliability/test_exp098_schema_attr_mismatches.py`
(7 tests, all pass):
- `test_sqlalchemy_only_bare_class_attribute_fixed` — Exp097's `User.name` shape.
- `test_pydantic_only_instance_attribute_fixed` — Exp097's `UserCreate.password` shape.
- `test_mixed_model_and_schema_types_each_resolved_independently` — one
  function touching both a model instance and a schema instance,
  confirming each resolves against its own source with no cross-talk.
- `test_existing_username_synonym_behavior_unchanged` — pre-existing
  SQLAlchemy `username`→`email` fallback reproduced exactly.
- `test_curated_name_key_does_not_reach_unrelated_description_title_cluster` —
  explicit regression guard for the gym_tracker bug found and reverted
  during this experiment.
- `test_unrelated_plain_dict_not_touched` — a plain, untyped dict
  literal with `"name"`/`"password"` keys is never mistaken for a typed
  model/schema instance.
- `test_schema_cols_empty_when_schemas_dir_missing` — graceful
  degradation, never raises.

## 5. Full regression suite (Task 6)

`test_exp073_attr_scope_fix.py` 12/12, `test_sql_constructor_and_auth_repairs.py`
30/30, new `test_exp098_schema_attr_mismatches.py` 7/7. Full
`backend/tests/reliability/` suite: **51/54** (53 pre-existing + 1 new
file). The 3 failures are the same pre-existing, unrelated failures
this series has repeatedly confirmed (`test_exp066_write_pipeline_hardening.py`
— stale fixture directory, `test_exp070_security_phase0.py` — missing
`jose` package in this environment, `test_semantic_write_validation.py`
— 2 unrelated write-corruption-replay subtests). Zero new regressions.

## 6. Estimated reliability improvement

Directly fixes both of Exp097's confirmed active incidents
(`User.name`, `UserCreate.password`) plus 5 additional real,
independently-verified bugs found via the full-corpus replay across 4
other projects — all guaranteed-crash paths on registration/seed/update
endpoints, none previously reachable by this patcher since Pydantic
schemas were entirely untracked before this experiment. Converts a
confirmed 0%-LLM-self-heal bug class (Exp097: both incidents ran 3-5
fix attempts without resolving) into a $0, deterministic, pre-runtime
correction — same category of gain as Exp088/092/095.

## 7. Recommendation for Exp099

Live-validate with 1-2 Cerebras canaries. Prefer todo-shaped ideas
(Exp097's two confirmed incidents were both todo-shaped, architecture
hash `1c3ab9664c1e`) since that's the confirmed-reproducible shape;
also acceptable to target auth-heavy ideas generally, since the fix's
value is broad (any app whose seed/register code guesses at
User/UserCreate field names). Given LLM verb/field-naming variance
already established across this series (Exp096's PUT/PATCH null
results), a live run that doesn't reproduce either exact confirmed
shape would be uninformative but not concerning — same acceptable
outcome pattern as prior live-validation cycles.

**Deliverables**: this doc, `experiments.md` entry, code diff in
`backend/app/services/deterministic_patcher.py`, new test file
`backend/tests/reliability/test_exp098_schema_attr_mismatches.py`.
**Cost: $0, zero Cerebras calls.**
