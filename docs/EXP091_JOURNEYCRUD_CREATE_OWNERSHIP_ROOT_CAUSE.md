# Experiment 091 — Root Cause Investigation of JourneyCRUDFailure (Create Ownership/FK)

2026-07-13. Investigation only, $0, zero Cerebras calls — reconstruction
via real, already-on-disk generated projects proved entirely sufficient;
no live reproduction needed.

## 1. Collected occurrences

`generation_log.jsonl`: 23 total JourneyCRUDFailure entries, **17
Create-path**, 6 Edit-path (out of scope per this experiment's own
constraint). All 17 Create-path instances span 2026-07-06 through
2026-07-12, across 3 independent app categories (todo, blog CMS, CRM,
inventory) — a general, recurring pattern, not app-specific.

## 2. Grouped by exact symptom (Task 2)

All 17 Create-path instances resolve to **one dominant shape**: the
handler accepts an authenticated-user dependency but never assigns the
corresponding ownership foreign key before `db.add()`/`db.commit()`.

| Symptom | Count | Evidence |
|---|---|---|
| **Missing owner_id/ownership FK entirely unassigned** | **17/17** | Confirmed directly (§3) |
| Wrong foreign key (drift) | 0 | A *different*, already-separately-patched issue (`_patch_ownership_fk_attribute_drift` — a query/filter naming-drift bug, not a create-assignment omission; see §6) |
| Missing relationship | 0 | Not observed — the gap is at field-assignment, not ORM relationship declaration |
| Incorrect authenticated-user assignment | 0 | Not observed — when assignment *does* happen, it's always the correct `current_user.id`; the failure mode is omission, never a wrong value |
| Other | 0 | — |

## 3. Representative trace, end-to-end (Task 3)

Live, currently-on-disk, **un-repaired** example —
`generated_projects/inventory_manager/app/routes/product_routes.py`:

```python
@product_router.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(
    product_in: ProductCreate,
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user),   # accepted...
):
    product = Product(**{k: v for k, v in product_in.dict().items()
                          if k in Product.__table__.columns.keys()})
    db.add(product)          # ...but current_user is never referenced again
    db.commit()
    db.refresh(product)
    return ProductResponse.model_validate(product, from_attributes=True)
```

- **Planner**: describes "users manage products" as a feature — doesn't
  specify per-field ownership-assignment mechanics (not its job).
- **Architecture**: correctly specifies the entity's FK columns
  (`Product.category_id`, `Transaction.product_id`, etc., all NOT NULL
  where appropriate) — the *data model* is right.
- **Backend generation**: **this is where it originates.** The route
  handler is generated with the auth dependency wired in (matching the
  contract's own auth-endpoint requirements) but the ownership-field
  assignment step is dropped.
- **Repair pipeline**: per Exp090's own direct measurement, 0% same-run
  self-heal across all 23 tracked JourneyCRUDFailure instances — this
  gap is *not* reliably caught or fixed by the existing LLM-driven
  repair loop.
- **Runtime**: when the entity's ownership FK is NOT NULL (confirmed
  live in earlier canary runs — `IntegrityError: NOT NULL constraint
  failed: posts.author_id`, `tasks.user_id`), `db.commit()` raises and
  the Create step of the CRUD journey fails with a 500.

`inventory_manager`'s own `Product`/`Transaction` models happen to have
**no** ownership FK column at all (verified directly — no `user_id`/
`owner_id` anywhere on either model), so this exact instance doesn't
crash; it's still direct, live, on-disk proof of the *generation-time
habit* (auth dependency accepted, never used) that crashes precisely
when the target model *does* have such a column.

## 4. Where ownership information is first lost (Task 4)

**Backend generation (Wave 4, route-writing)** — traced to the exact
governing instruction, `app/prompts/shared_contract.py:185-187`:

```
- Routes that CREATE/UPDATE user-owned resources (model with `user_id` NOT NULL FK) MUST:
  1. Include `current_user: User = Depends(get_current_user)` in the signature
  2. Set `obj.user_id = current_user.id` before `db.add(obj)`
```

This rule exists and is explicit — but it's **scoped to the literal
string `user_id`**. It does not mention `owner_id`, `author_id`,
`creator_id`, or `created_by` — all four of which are common,
architecturally-legitimate ownership-FK names this same codebase already
recognizes elsewhere (`_OWNERSHIP_FK_SYNONYMS`, §6). A model whose
ownership column is named `author_id` (confirmed on `forge_blog_cms`'s
`Post`) has no textual match to this rule at all when the LLM reads the
contract, making it materially less likely to be followed. **This alone
doesn't fully explain every instance** — `todo_list_app`'s `Task.user_id`
*does* literally match the rule's own trigger string, yet also
historically crashed with this exact bug (confirmed via prior live canary
tracebacks) before being fixed by a later repair pass. So two factors
compound: (a) the rule's naming scope is too narrow to reliably trigger
for non-`user_id` conventions, and (b) even an exact match isn't
followed with 100% reliability — ordinary LLM instruction-following
variance, not something a prompt reword alone fully closes.

## 5. Comparison against successful CRUD generation (Task 5)

Both currently-fixed versions on disk show the *same* correct shape once
present — `todo_list_app/task_routes.py`: `user_id=current_user.id` in
the constructor; `forge_blog_cms/post_routes.py`:
`author_id=current_user.id`. Both are almost certainly **post-repair**
states (the runtime fix loop's own LLM occasionally does add this
correctly, consistent with JourneyCRUDFailure not being 100% of all
CREATE-with-ownership generations, only the ones the repair loop failed
to catch within its attempt budget) — not evidence the *original*
generation got it right. This matches Exp090's own finding precisely:
the repair loop sometimes recovers this bug outside the tracked
"unresolved" bucket, but has a proven 0% rate specifically among the 23
instances that never got fixed.

## 6. Existing deterministic infrastructure found (Task 6) — directly reusable

`app/services/deterministic_patcher.py` already has the exact building
blocks needed, currently used only by a *sibling*, different-purpose
patcher:

- **`_model_fk_columns(models_dir)`** (line 3742): returns `{class_name:
  {ForeignKey-typed column names}}` for every model — deliberately
  checks for a genuine `ForeignKey(...)` declaration, not just "a column
  with this name," to avoid false-positiving on an unrelated same-named
  column.
- **`_OWNERSHIP_FK_SYNONYMS`** (line 3634): the 5-way equivalence map
  `{owner_id, user_id, creator_id, author_id, created_by}` — already
  encodes exactly the naming-convention generalization §4 found the
  prompt rule is missing.
- Both are consumed today by **`_patch_ownership_fk_attribute_drift()`**
  (line 3643) — but that function fixes a *different* bug: existing
  `ClassName.wrong_attr` query/filter expressions that reference the
  wrong ownership-FK name (a live-confirmed CRM data-isolation bug,
  2026-07-11). It does not touch CREATE handlers or insert-time field
  assignment at all — it's a read/filter-side fix, this investigation's
  target is a write/insert-side gap. Distinct, complementary, not
  overlapping.
- **Auth completeness** (`app/repair/auth_completeness.py`): scoped to
  endpoint existence + (as of Exp085) request-schema field consistency —
  has no concept of ownership-FK assignment at all; not the right home
  for this fix.
- **Endpoint validators**: check path/method existence, not handler-body
  semantics — not applicable.
- **Runtime repair**: the LLM-driven path already tries to fix this and
  has a proven 0% rate for the persistent tail — reinforces that a
  deterministic, pre-runtime fix is the right lever, not more repair-loop
  reliance.

## 7. Quantified frequencies (Task 7)

- **17/23 (74%)** of all tracked JourneyCRUDFailure instances are
  Create-path.
- **17/17 (100%)** of Create-path instances match the single "missing
  ownership-FK assignment" shape — no sub-variants found.
- Cross-references Exp090's separately-tracked `NotNullViolationError`
  (5 all-time) and `TimestampNotNullError` (2 all-time) — both very
  likely the same underlying mechanism recorded under a different
  taxonomy label (the NOT NULL constraint violation vs. the CRUD-journey-
  level symptom), meaning a single fix plausibly closes 3 taxonomy
  entries at once, matching Exp090's own prediction.

## 8. Smallest deterministic implementation candidate

A new patcher, reusing `_model_fk_columns()` and `_OWNERSHIP_FK_SYNONYMS`
verbatim (no duplication):

1. For each route file, find POST (create) handlers that accept a
   `current_user`/`Depends(get_current_user)` parameter.
2. Identify the ORM class being constructed (via the same
   `db.add(<ClassName>(...))`/`<var> = <ClassName>(...)` pattern already
   scanned elsewhere in this file).
3. Look up that class in `_model_fk_columns()`; if it has a column whose
   name is a key or value in `_OWNERSHIP_FK_SYNONYMS`, that's the
   ownership FK.
4. Scan the handler body for an existing assignment of that field from
   `current_user.id` (constructor kwarg or `obj.<fk> = current_user.id`
   line) — if already present, leave untouched (idempotent, no
   double-injection, matching Exp088's own established pattern).
5. If absent, inject `<var>.<fk_col> = current_user.id` immediately
   before `db.add(<var>)`.

Deliberately not a prompt change (this experiment found prompt-following
reliability is part of the problem, not solvable by prompt wording
alone) and not a broadening of the existing drift-patcher (different bug
shape, would conflate two independently-understood, independently-fixed
issues into one function).

## 9. Estimated reliability improvement

Fixing this deterministically (bypassing the repair loop's proven 0%
self-heal rate for this exact pattern) targets 17/23 (74%) of the #1
remaining active reliability class identified in Exp090, plus 2
additional overlapping taxonomy entries. Since the fix applies at
generation time (before runtime verification ever runs), the expected
effect is the same category of gain Exp088 delivered for
`PydanticSerializationError`: converting a previously-common runtime
crash into a $0, zero-LLM-call correction.

## 10. Recommendation for Exp092

Implement the patcher described in §8, scoped to a new function in
`app/services/deterministic_patcher.py` (reusing, not duplicating,
`_model_fk_columns`/`_OWNERSHIP_FK_SYNONYMS`). Offline-test against
reconstructed fixtures matching the two confirmed real shapes
(`inventory_manager`'s exact "current_user accepted, unused" pattern;
`todo_list_app`/`forge_blog_cms`'s historically-confirmed
NOT-NULL-crash shape) before any live validation, following this
project's own established investigate → implement → offline-replay →
live-validate cycle.

**Deliverables**: this doc, `experiments.md` entry. No code changes, no
Cerebras calls. **Cost: $0.**
