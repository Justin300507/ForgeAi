# Experiment 092 — Deterministic Repair of Missing Ownership Assignment

2026-07-13. Offline, $0, zero Cerebras calls. Implements Exp091's
recommended correction: a new, narrowly-scoped patcher in
`app/services/deterministic_patcher.py`, reusing `_model_fk_columns()`
and `_OWNERSHIP_FK_SYNONYMS` verbatim.

## 1. Decision: new patcher, not an extension (Task 1/2)

Reviewed `_patch_ownership_fk_attribute_drift()` in full. It fixes a
**different** bug shape: existing `ClassName.wrong_attr` query/filter
expressions referencing the wrong ownership-FK name (a read-side,
data-isolation bug). This experiment's target is a **write-side**
omission — a CREATE handler never assigning the ownership FK at all.
Conflating the two into one function would blur two independently
understood, independently confirmed-live bugs. New function
`_patch_missing_ownership_assignment()`, reusing the sibling's exact two
building blocks (`_model_fk_columns`, `_OWNERSHIP_FK_SYNONYMS`) rather
than duplicating either.

## 2. Code diff

`app/services/deterministic_patcher.py`:

- New `_patch_missing_ownership_assignment(project_path)`: for each POST
  handler that accepts a `Depends(get_current_user)` parameter and
  constructs an instance of a model with a recognized ownership FK,
  checks whether that field is already assigned (constructor kwarg,
  post-construction attribute, or dict-mutation-then-`**`unpack — three
  forms, all confirmed live, see §4) and, if not, injects
  `<var>.<fk_col> = <current_user_param>.id` immediately before the
  matching `db.add(<var>)` call.
- Three small AST helpers: `_has_post_decorator`,
  `_current_user_param_name`, `_assigns_attribute`,
  `_assigns_via_dict_unpack`, `_find_db_add_lineno` — each doing one
  narrow, single-purpose check.
- Wired into `run_deterministic_patches()` immediately after
  `_patch_ownership_fk_attribute_drift`'s own call site, same
  `_run_patch_isolated` wrapper (per-patcher failure isolation, matching
  every other entry in that sequence).

## 3. Detection logic (Task 3)

1. POST decorator (`@x_router.post(...)`).
2. A parameter whose default is `Depends(get_current_user)` (checked
   across both positional-with-default and keyword-only parameters).
3. A `var = ClassName(...)` assignment where `ClassName` has an
   ownership FK per `_model_fk_columns`/`_OWNERSHIP_FK_SYNONYMS`.
4. Not already assigned (§4).
5. A same-function `db.add(var)` call, anchoring the insertion point.

Any one of these missing — no auth dependency, no matching model
construction, no reachable `db.add()` — leaves the route completely
untouched, matching this file's own established "if nothing matches this
shape, no-op" convention.

## 4. Preservation (Task 5) — three forms, all confirmed necessary by replay

- **Constructor kwarg**: `Cls(user_id=current_user.id, ...)` — checked
  directly against `call.keywords`.
- **Post-construction attribute**: `obj.user_id = current_user.id` (or
  any other value — custom ownership logic is preserved even when it
  computes something other than `current_user.id` directly, per Task 5's
  explicit "preserve custom ownership logic").
- **Dict-mutation-then-unpack**: `d["user_id"] = current_user.id` then
  `Cls(**d)` — found necessary during offline replay (§6), not
  anticipated up front; without it, `recipe_forge`'s already-correct
  handler would have gotten a redundant (harmless but wrong per Task 5)
  second assignment.

An early version of the dict-mutation check had a real bug of its own,
caught before shipping: unwrapping `ast.Index` (a pre-3.9 AST
compatibility shim) with a blind `getattr(key, "value", key)` also
unwraps `ast.Constant` nodes (which have their own `.value` attribute
holding the literal), making the subsequent `isinstance(key, ast.Constant)`
check always false. Fixed by checking `isinstance(key, ast.Index)`
explicitly before unwrapping.

## 5. Offline replay against the 17 confirmed failures (Task 6)

Exact historical broken snapshots aren't preserved on disk (projects get
regenerated/repaired between runs — the same limitation noted in
Exp088/091). Two complementary replay strategies used instead:

**Reconstructed fixtures**, matching the exact confirmed shapes from
Exp091's own trace (`inventory_manager`'s dict-unpack constructor
pattern, adapted with a real ownership FK): fix correctly injects
`task.user_id = current_user.id` / `post.author_id = current_user.id`
before the `db.add()` call, idempotent on a second pass.

**Full-corpus scan**: ran the real, unmodified function against
**temp copies** of all 55 currently-on-disk generated projects with a
`routes` directory (originals never touched). Found **2 genuine, real
live hits**:

- `lean_sales_crm/deal_routes.py`: `Deal` has both a real FK `owner_id`
  (never assigned) and an unrelated, non-FK `user_id` column (the exact
  scenario `_patch_ownership_fk_attribute_drift`'s own docstring
  independently documented for this same app) — correctly injects
  `new_deal.owner_id = current_user.id`.
- `support_ticket_system/message_routes.py`: `TicketMessages` has a real
  FK `author_id` (never assigned); the handler instead passes
  `user_id=current_user.id` as a constructor kwarg — but `TicketMessages`
  has **no `user_id` column at all**, so that kwarg is *already* a
  separate, pre-existing bug (would raise `TypeError: 'user_id' is an
  invalid keyword argument` at runtime, independent of this fix). This
  patch correctly adds the missing `author_id` assignment but does
  **not** address the separate invalid-kwarg defect — flagged honestly
  here, not silently claimed as a full fix for this one instance, and
  named as a candidate for a future cycle (a "wrong-kwarg-name in a
  constructor call" bug shape, distinct from both this fix and the
  sibling drift-patcher, since neither touches constructor keyword
  argument names).
- `recipe_forge` initially also flagged (before the dict-unpack fix,
  §4) — confirmed, after the fix, correctly left untouched (ownership
  already assigned via dict mutation).

## 6. Regression results

New test file
`backend/tests/reliability/test_exp092_missing_ownership_assignment.py`
(12/12 pass): injects for missing `user_id`, injects for the `author_id`
naming gap (the exact Exp091-confirmed prompt-scope issue), idempotent
on a second pass, preserves all three already-assigned forms (kwarg,
attribute, dict-unpack), preserves custom ownership logic with a
different value, leaves models without an ownership FK untouched,
leaves handlers with no `current_user` dependency untouched, leaves
handlers with no reachable `db.add()` untouched, and doesn't fire on
GET handlers.

Existing sibling suite (`test_ownership_fk_drift.py`): 7/7 pass,
unchanged. Full `backend/tests/reliability/` suite (52 files, one new):
**49/52 pass** — same 3 pre-existing, unrelated failures as every prior
cycle (`test_exp066_write_pipeline_hardening.py`,
`test_exp070_security_phase0.py` — missing `jose` module, an environment
gap — `test_semantic_write_validation.py`'s 2 known failures). No new
failures.

## 7. Estimated reliability improvement

Targets 17/23 (74%) of JourneyCRUDFailure — Exp090's #1 identified
remaining active class — plus the overlapping `NotNullViolationError`/
`TimestampNotNullError` taxonomy entries (Exp091's own prediction).
Applies at generation time, before runtime verification ever runs,
converting what was previously a 0%-self-heal runtime crash (Exp090's
own direct measurement) into a $0, zero-LLM-call correction — the same
category of gain Exp088 delivered for `PydanticSerializationError`. The
corpus scan's 2/55 hit rate is a lower bound on *current* prevalence in
already-repaired projects on disk, not a ceiling on the bug's original
generation-time frequency (most on-disk projects reflect post-repair
state, per the same caveat every prior cycle in this series has noted).

## 8. Recommendation for Exp093

Live-validate against `benchmarks/golden/01_todo.txt` (todo, matching
`Task.user_id`) and/or a CRM-shaped idea (matching the `owner_id`/
`user_id`-collision shape confirmed in `lean_sales_crm`), instrumenting
`_patch_missing_ownership_assignment` similarly to Exp089's wrapper
around `_patch_orm_response_model`. Confirm: (a) the injection fires on
a fresh generation exhibiting this shape, (b) the previously-crashing
Create step now returns 2xx with correct persistence, (c) no regression
on handlers that already assign ownership correctly.

**Deliverables**: this doc, `experiments.md` entry, code diff in
`backend/app/services/deterministic_patcher.py`, new test file
`backend/tests/reliability/test_exp092_missing_ownership_assignment.py`.
**Cost: $0, zero Cerebras calls.**
