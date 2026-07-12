# Experiment 075 — NOT NULL on PUT Extension

2026-07-12. Offline, $0. Fixes the update-path NOT NULL gap Experiment
074 found live in `inventory`'s `PUT /products/{id}` — explicitly **not**
the same bug Exp012/13 fixed (that pair targets CREATE only).

## 1. Root cause

The backend route-generation LLM call (Wave 4 of the V6 generation
pipeline — one completion per route file) sometimes writes an
**unconditional field copy** in a `PUT`/`PATCH` handler:

```python
product.sku = product_in.sku
product.name = product_in.name
...
```

`product_in` is a `{Model}Update` Pydantic schema instance where every
field is `Optional[...] = None` by design (correct schema design — a
partial-update DTO should make every field optional). Pydantic gives an
omitted field the value `None`. When the route copies it straight across
with no guard, a client that PATCHes only `name` (never touching `sku`)
silently sets `sku = None` in the ORM session. On `nullable=False`
columns this crashes loudly (`IntegrityError: NOT NULL constraint
failed`); on nullable columns it **silently corrupts the row** — no
crash, no signal, objectively worse.

Confirmed via the exact live SQL from Exp074's `inventory` incident:

```sql
UPDATE products SET sku=?, name=?, category_id=?, unit_cost=?, reorder_threshold=? WHERE products.id = ?
-- params: (None, 'Journey Test Item EDITED', None, None, None, 1)
```

Five columns overwritten unconditionally from one `ProductUpdate`
instance — not just `sku`, which is simply the one that happened to be
`nullable=False` and crash loudly enough to be caught.

## 2. Earliest divergence — tracing Planner → Runtime

| Stage | Field seen as | Divergence? |
|---|---|---|
| Planner / Architecture | Abstract entity spec (`Product` has a `sku` field) | No — the spec doesn't dictate route-body implementation details |
| Model generation (Wave 2) | `sku = Column(String(50), nullable=False, unique=True)` | No — correct, standard domain modeling; SKU genuinely should never be null |
| Schema generation (Wave 3) | `ProductUpdate.sku: Optional[str] = None` | No — correct, standard partial-update DTO design; every PATCH schema should make its fields optional |
| **Route generation (Wave 4)** | `product.sku = product_in.sku` (no guard) | **Yes — this is the earliest and only divergence.** The route-generation LLM call, in this specific completion, did not implement partial-update semantics for the assignment it wrote, despite the schema it was handed already correctly signaling "this field may be absent." |
| Repair / runtime | Crash surfaces only here, on the FIRST client request that omits the field | Symptom, not cause — by the time this fires, the wrong code has already been written and committed to disk |

**Answering the mission's explicit question — where does the bug
originate?** **CRUD/route generation**, not schema generation, not model
generation, not repair, not runtime. The schema is right. The model is
right. The one LLM completion that writes the route handler's *body
logic* is where partial-update semantics get lost — confirmed by reading
the real `product.py` schema and `products.py` model from the Exp074
artifact directly: both are exactly correct; only `product_routes.py`'s
`replace_product()` function (as originally generated) has the bug.

## 3. Implementation

Per this experiment's own explicit rules — **extend Exp012/13, don't
build a parallel repair, reuse existing mechanisms** — and per the
finding above that the fix must live in the ROUTE (relaxing the model
here would be actively wrong: it would hide silent data corruption
instead of preventing it), the new fix is:

- **`app/services/deterministic_patcher.py`**: added
  `_model_notnull_no_default_columns()` — structurally identical to (and
  directly reusing the same Column-classification logic as)
  `preflight.py::_fix_model_schema_notnull_gap`'s own regex, exposed as a
  standalone helper so both the CREATE-path and UPDATE-path fixes share
  one column-classification source of truth instead of drifting.
- **`app/repair/preflight.py`**: new
  `_fix_update_notnull_field_loss()`, registered at priority 27 (right
  after Exp012/13's `fix_model_schema_notnull_gap` at 24, before
  `fix_missing_env` at 30 — runs automatically via the existing
  `preflight.run()` call sites in `pipeline.py` and
  `orchestrator.py`, zero pipeline wiring changes needed). Reuses, not
  reimplements, Exp073's own AST type-inference
  (`_infer_model_typed_names`, `_query_target_class` from
  `deterministic_patcher.py`) to determine which route-local variable is
  genuinely an instance of which model class — the same mechanism that
  already proved itself live in Exp074.

  Detection: inside every `@router.put(...)`/`@router.patch(...)`
  handler, find `target.field = source.field` assignments where `field`
  is one of the model's NOT NULL/no-default columns, `target` is the
  typed model instance, `source` is a *different* object, and the
  assignment is not already inside an enclosing `if` that tests
  `source.field` in any form (conservative — a false "already guarded"
  only means a genuinely risky line is left for a future pass, never a
  false rewrite).

  Fix: rewrite the one line to
  `if source.field is not None:\n    target.field = source.field`.

- Deliberately does **not** scan `@router.post(...)` handlers (CREATE),
  does **not** touch any model `Column(...)` declaration, and does
  **not** attempt a generic dict-diff/PATCH-style rewrite — only this
  one confirmed, narrowly-scoped statement shape.

## 4. Files changed

- `backend/app/services/deterministic_patcher.py` — new
  `_model_notnull_no_default_columns()` (+ `_NOTNULL_COLUMN_RE`).
- `backend/app/repair/preflight.py` — new `_fix_update_notnull_field_loss()`
  (priority 27) + 3 small helpers (`_build_parent_map`,
  `_guarded_by_ancestor_if`, `_put_patch_handlers`); `import ast` added.
- `backend/tests/reliability/test_exp075_update_notnull_fix.py` (new) —
  11 regression tests.
- `docs/EXP075_UPDATE_NOT_NULL.md` (this file), `experiments.md`.

**CREATE behavior**: zero changes to `_fix_model_schema_notnull_gap` or
any code path it touches. `_fix_update_notnull_field_loss` never scans
`@router.post(...)` handlers and never writes to `app/models/`, verified
by a dedicated test (`test_model_column_definitions_never_touched`) that
asserts the model file is byte-for-byte unchanged after the fix runs.

## 5. Replay results

**Real inventory artifact replay** (reconstructed byte-for-byte from
Exp074's own traceback SQL — the exact pre-repair `replace_product()`
shape): fix correctly guards `sku` and `name` (the model's two NOT
NULL/no-default columns), leaves `category_id`/`unit_cost`/
`reorder_threshold` (nullable) exactly as generated, and leaves
`create_product()` completely untouched. `ast.parse()` on the output
confirms valid Python.

**Runtime replay against a real (in-memory) SQLite database** — the
actual success criterion, not just a static code check:
```
Partial update, sku omitted -> product.sku == "ABC123" (preserved, not NULL)
Complete update, sku="NEW-SKU" provided -> product.sku == "NEW-SKU" (still updates correctly)
Old unguarded shape, same scenario -> confirmed sqlalchemy.exc.IntegrityError
```

**CREATE replay**: `test_create_handler_never_touched` and
`test_model_column_definitions_never_touched` both pass — the CREATE
handler's source text and the model file's source text are verified
byte-for-byte identical before and after the fix runs.

**Full regression suite**: `test_exp075_update_notnull_fix.py`, 11/11
passing (single omitted field, multiple omitted fields, mixed
nullable/non-nullable, already-guarded/idempotent, partial update,
complete update, explicit-null-vs-omitted, real inventory replay — see
§6 below for the explicit-null caveat). Full `preflight.py` suite
(`test_preflight_fixes.py`, 70/70, including
`test_registry_runs_in_priority_order_not_source_order`) and Exp073's
own suite (`test_exp073_attr_scope_fix.py`, 12/12) re-run clean — zero
regressions from the new registration or the new
`deterministic_patcher.py` helper.

## 6. Regression test coverage (mission's explicit list)

| Required case | Test | Result |
|---|---|---|
| Single omitted field | `test_single_notnull_field_gets_guarded` | PASS |
| Multiple omitted fields | `test_multiple_notnull_fields_all_guarded` | PASS |
| Explicit null | `test_explicit_none_and_omitted_are_indistinguishable_and_both_preserved` — documented limitation: Pydantic/FastAPI give "omitted from JSON" and "explicitly sent as `null`" the identical Python value `None` with no distinguishing sentinel at this layer, so this fix (and the route pattern it produces) necessarily treats them the same. Accepted: nulling a NOT NULL column is never a valid client operation regardless of which of the two it was. | PASS (documents the limitation rather than silently assuming it away) |
| Partial update | `test_runtime_partial_update_preserves_omitted_notnull_field` | PASS |
| Complete update | `test_runtime_complete_update_still_applies_all_fields` | PASS |
| Mixed nullable/non-nullable | `test_mixed_nullable_and_notnull_only_notnull_guarded` | PASS |
| Real inventory replay | `test_real_inventory_artifact_replay` | PASS |

## 7. Estimated reliability impact / false-positive analysis

`generated_projects/` is populated this cycle (54 complete generated
projects with both `app/models/` and `app/routes/`, left over from prior
canary/benchmark runs — git-ignored, not committed, but present on this
machine). Ran the **actual new detection logic** (not a proxy) against
every one, in detect-only mode (no writes):

```
projects scanned (models/ + routes/ both present): 54
  with >=1 NOT NULL/no-default model column:        50
  with >=1 PUT/PATCH handler at all:                49
  with the CONFIRMED risky unguarded-copy shape:      9  (16.7%)
  total risky assignments found:                     14
```

Confirmed genuine (spot-checked, not just pattern-matched) in, among
others: `simple_todo/todo_routes.py` — `Todo.title` is
`nullable=False`, and `replace`-style PUT does `todo.title =
todo_in.title` with no guard, byte-for-byte the same shape as the
`inventory` incident, in a **different app from a different session**,
confirming this is a recurring LLM-output pattern, not a one-off.

Supplementary, broader-but-weaker signal: swept 1,106 cached route files
in `llm_cache/` (individual `backend_file_*.json` entries, not grouped
by project, so nullability can't be cross-checked against a matching
model — this number is a pure upper bound on "how often does the LLM
write this unguarded-copy shape at all," not a corruption-rate estimate)
— 317 files contain a `PUT`/`PATCH` handler; 150 of those (47.3%) contain
at least one unguarded `obj.attr = other.attr` copy, 362 such
assignments total.

**Historical canaries**: `patterns.json`'s existing taxonomy records only
4 total `NotNullViolationError` instances project-wide and does not
distinguish CREATE-path from UPDATE-path occurrences (both bucket under
the same label) — this experiment's own live incident (Exp074) is
consistent with, but not separately broken out in, that count. Not
re-classified this cycle (out of scope — offline, $0, this experiment's
own rule against parallel/generic rewrites extends to not re-architecting
the taxonomy).

**Estimate**: on real, complete generated projects, roughly **1 in 6**
(16.7%) has at least one genuinely NOT-NULL-column instance of this bug
sitting in a PUT/PATCH handler at generation time — a real, non-trivial,
previously-invisible-to-taxonomy reliability gap, now closed
deterministically at $0 per generation.

## Answers to the mission's explicit questions

1. **Root cause**: route-generation-stage LLM output — an unconditional
   field copy from an Optional Update-schema field, not schema
   generation, not model generation, not repair, not runtime (§1).
2. **Earliest divergence**: Wave 4 (route generation), specifically the
   `PUT`/`PATCH` handler body the LLM writes in that same completion
   (§2).
3. **Files changed**: `deterministic_patcher.py` (+1 helper),
   `preflight.py` (+1 registered fix, +3 small helpers), 1 new test
   file, this doc, `experiments.md` (§4).
4. **Replay results**: real-artifact replay, runtime DB replay, and
   CREATE-path non-regression all pass (§5).
5. **Estimated reliability impact**: ~16.7% of real generated projects
   with matching models+routes carry ≥1 confirmed instance; now fixed
   deterministically, $0 marginal cost per generation, zero LLM calls
   added (§7).
6. **Recommendation for Exp076**: this experiment's own success
   criterion is met offline; the natural next step is a live canary
   validation of THIS fix specifically (same pattern as Exp074 validated
   Exp073) — confirm `_fix_update_notnull_field_loss` actually fires and
   correctly guards a real live generation, not just the reconstructed
   replay. `docs/RUNTIME_KNOWLEDGE_BASE.md`'s still-open `MissingEndpoint`
   general-CRUD-sub-case (Exp074 §3, ranked #1 there, larger scope, no
   existing mechanism to extend) remains the higher-value long-term
   target once this fix is live-confirmed.

**Cost: $0** — no LLM calls, offline AST/regex work and local test
execution only. Per the task's own instruction, **NOT committed**.
