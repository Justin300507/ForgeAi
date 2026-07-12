# Experiment 088 — Repair Nested ORM Serialization in Generic Dict Responses

2026-07-13. Offline, $0, zero Cerebras calls. Implements Exp087's
recommended correction, scoped entirely to
`_patch_orm_response_model()` (`app/services/deterministic_patcher.py`)
— no parallel patcher, no removed `response_model` annotations.

## 1. Code diff

All changes inside `app/services/deterministic_patcher.py`:

- **New**: `_inject_orm_dict_response_conversion(content, orm_classes,
  schema_map)` — for a route whose `response_model` is a bare
  `dict`/`Dict`, finds a `return {"items": <var>, ...}` statement
  (single- or multi-line) and, if the same function body queries a known
  ORM class (`db.query(ClassName)`) with a matching entry in
  `schema_map`, injects one line immediately **before** the `return`
  statement: `<var> = [<SchemaCls>.model_validate(x,
  from_attributes=True) for x in <var>]`. Wired into
  `_patch_orm_response_model()` right after the existing
  response_model/return-type substitution passes, reusing the same
  `orm_classes`/`schema_map` it already builds.
- **Fixed** (two pre-existing bugs in the *existing* `schema_map`
  construction, found via offline replay against real projects, not
  introduced by this experiment but directly exposed by it):
  - Schema-class scanning switched from a bare regex
    (`class \w+\(.*BaseModel.*\)`, which only matches a class that
    *directly* names `BaseModel` as a base) to
    `fix_writer_service._collect_basemodel_classes()` (Exp064's own
    fixed-point local-inheritance resolver, reused as-is) — the old
    regex made an entire real schema file invisible whenever its classes
    inherited through a local `BaseSchema(BaseModel)` base, which is a
    common generated-code shape.
  - When more than one schema class matches an ORM class name, prefer
    the conventionally-named `<Base>Response` class over whatever
    `Path.glob()`'s alphabetical file order happened to surface first —
    confirmed live to matter: a duplicate-model-cleanup shim class
    (`TaskRead`, declaring only `id`) otherwise won over the real,
    complete `TaskResponse`.
  - Import-duplication check switched from an exact-string match to a
    regex that recognizes `schema_cls` already present in a *combined*
    `from module import A, B, schema_cls` statement.

## 2. Offline replay using the four confirmed projects

All four real, already-on-disk projects from Exp087's investigation,
patched and diffed directly (not synthetic reconstructions):

| Project / route | Before | After |
|---|---|---|
| `todo_list_app/task_routes.py` | `response_model=dict`, raw `Task` objects returned | conversion injected: `TaskResponse.model_validate(...)`, import added |
| `recipe_share/rating_routes.py` | `response_model=dict`, raw `Rating` objects, **multi-line** return (4-key pagination dict) | conversion injected before the multi-line `return {`, metadata keys (`total`/`limit`/`offset`) untouched |
| `recipe_share/recipe_routes.py` | already converts inline (`[RecipeResponse.model_validate(r, ...) for r in recipes]`) | **unchanged** — inline expression isn't a bare variable, correctly not touched |
| `simple_notes_app/note_routes.py` | queries ORM then runs a **deliberate custom field-rename shim** (`_note_to_dict`, maps `content`→`description`) before returning | **unchanged** — items_var's last assignment isn't a direct `.all()` call, correctly not re-wrapped |
| `simple_notes_app/user_routes.py` | `response_model=Dict[str, Any]`, raw `User` objects under key `"items": users` | conversion injected on the correct variable name (`users`, not literally "items") |
| `forge_blog_cms/tag_routes.py` | already converts per-item inline before the return | **unchanged** — already-converted guard fires correctly |

Every patched file re-verified idempotent (running the patcher a second
time produces byte-identical output). Two of these six (`recipe_routes.py`,
`note_routes.py`, `tag_routes.py` — three, not two) were *correctly left
untouched* by design, not by omission — each represents a distinct "don't
second-guess existing correct behavior" case this experiment's own
constraints required (`note_routes.py`'s custom shim was found and
excluded only after a first implementation pass would have wrapped it,
which offline replay caught before it could ship).

## 3. Regression results

New test file
`backend/tests/reliability/test_exp088_orm_dict_response_conversion.py`
(12/12 pass): paginated lists (single- and multi-line), empty-list
syntax validity, mixed pagination metadata preserved verbatim, genuine
dict responses left untouched, an `"items"` key with no ORM query
untouched, missing-schema case left untouched, the duplicate-shim
schema-preference fix, the combined-import duplication fix, and the two
"don't re-convert" cases (custom dict-mapping helper, already-inline
conversion) found via the real-project replay.

Existing `_patch_orm_response_model` suite
(`test_inline_chain_repairs.py`): 58/58 pass, unchanged — confirms the
schema_map refinements don't alter behavior for any single-candidate
case (every existing test fixture's shape).

Full `backend/tests/reliability/` suite (51 files, one new): **48/51
pass** — same 3 pre-existing, unrelated failures as prior cycles
(`test_exp066_write_pipeline_hardening.py`,
`test_exp070_security_phase0.py` — missing `jose` module, an environment
gap — `test_semantic_write_validation.py`'s 2 known failures). No new
failures.

## 4. Estimated reliability improvement

Per Exp087's measurement: this bug reproduced identically across 4/4
examined, independent app categories and was the sole cause of both
recorded `generation_log.jsonl` failures (scores 65.9 and 76.9, both
capped below deploy-ready). Unlike a narrow edge case, `response_model=dict`
for pagination is a general LLM habit likely to recur in any app with a
list endpoint — a very common architecture shape. With this fix, any
generation hitting this pattern should have it corrected the moment
`run_deterministic_patches()` runs (before the app ever reaches runtime
verification), rather than surfacing as a runtime crash discovered only
during the CRUD journey check. Expected effect: eliminates this failure
class at generation time for the common case (a matching schema exists,
which was true in all 4 confirmed instances), converting what was
previously a runtime crash into zero cost, zero LLM calls.

## 5. Recommendation for Exp089

**Live-validate next**, targeting `benchmarks/golden/01_todo.txt` (the
exact idea behind Exp086/087's own `todo_list_app` instance) or a
recipe/notes-shaped idea if available, instrumenting
`run_deterministic_patches`/`_patch_orm_response_model` similarly to how
Exp086 wrapped `ensure_auth_completeness`. Confirm: (a) the conversion
fires on a fresh generation exhibiting this exact shape, (b) the
previously-crashing list endpoint now returns 200 with correctly-shaped
JSON, (c) no regression to endpoints that don't match this pattern.

**Deliverables**: this doc, `experiments.md` entry, code diff in
`backend/app/services/deterministic_patcher.py`, new test file
`backend/tests/reliability/test_exp088_orm_dict_response_conversion.py`.
**Cost: $0, zero Cerebras calls.**
