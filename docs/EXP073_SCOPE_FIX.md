# Experiment 073 — Deterministic Attribute Rewrite Scope Fix

2026-07-12. Offline, $0. Fixes the dominant bottleneck Experiment 072
identified live: `deterministic_patcher.py::_patch_attr_access_mismatches()`
applied its fix via a file-wide `re.sub()`, corrupting correctly-generated
code unrelated to the detected mismatch. **This experiment fixes only this
confirmed bug** — detection logic is unchanged.

## 1. Root cause confirmation

Read directly from `app/services/deterministic_patcher.py` (pre-fix,
lines 3267–3335):

```python
for cls_name, valid_cols in model_cols.items():
    if cls_name not in content:          # only checks the class NAME appears
        continue                          # somewhere, anywhere, in the file
    for bad_attr, candidates in _FIELD_SYNONYMS_PATCHER.items():
        ...
        content = re.sub(
            r'\.' + re.escape(bad_attr) + r'\b',
            '.' + good_attr,
            content,                      # <-- whole FILE content, every match
        )
```

Detection (does model class X lack column `bad_attr` but have a synonym
`good_attr`) is correct and class-scoped. The **fix application** is not:
once a mismatch is detected for class X, `re.sub` rewrites *every*
`.bad_attr` occurrence in the entire file, regardless of which object
(variable) it is actually attached to. Because `_FIELD_SYNONYMS_PATCHER`'s
keys are common English words (`display_name`, `status`, `title`,
`description`, `priority`, ...), any *other*, genuinely correct object in
the same route file that happens to use the same attribute name is
silently rewritten too.

**Exact live reproduction (Exp072, 2026-07-12 canary, `exp072-validation-r1`,
Cerebras, `todo` + `blog_cms`):** the injected `auth_routes.py` template
(`_build_auth_routes_template`) contains, unconditionally:

```python
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    ...
    user = _make_user(req.email, req.password, req.display_name)
    ...
```

`req: SignupRequest` — `SignupRequest` declares `email`/`password`/
`display_name`, so `req.display_name` is valid, correct code. Separately,
in the SAME file, the generated `User` model (built from the app's own
schema) has a `username` column but no `display_name` column. The
detector correctly flags `User` as missing `display_name` and finds
`username` as a synonym candidate present on the model
(`_FIELD_SYNONYMS_PATCHER["display_name"] = ["full_name", "name",
"username"]`). The file-wide `re.sub()` then rewrites **every**
`.display_name` in the file — including `req.display_name`, which has
nothing to do with `User` — to `.username`, producing `req.username`,
which does not exist on `SignupRequest` → `AttributeError` on every
signup request. This is byte-for-byte the shape Exp072 reported ("a
correctly-injected `auth_routes.py` referencing `req.username` where
`SignupRequest` only declares `email`/`password`/`display_name`").

Confirmed: **root cause matches Exp072's finding exactly** — a second,
independent write path (`.write_text()` inside
`run_deterministic_patches()`) that bypasses Exp064's semantic-consistency
guard entirely (that guard only wraps the single-file LLM-repair writer,
`write_fix()`).

## 2. Files changed

- `backend/app/services/deterministic_patcher.py`
  - Removed the unused, dead `_ATTR_ACCESS_RE` regex constant.
  - Rewrote `_patch_attr_access_mismatches()` to scope every rewrite via
    AST instead of a file-wide `re.sub()`. Detection (which class is
    missing which column, and which synonym to use) is byte-for-byte
    unchanged.
  - Added three small helpers used only by the rewrite:
    - `_query_target_class(call, model_cols)` — walks a (possibly
      chained) call expression like `db.query(User).filter(...).first()`
      back through its `.attr(...)` chain to find a `.query(ClassName)`
      leg.
    - `_infer_model_typed_names(fn, model_cols)` — conservative,
      per-function `{variable_name: model_class_name}` map built only
      from provable evidence: a typed parameter annotation
      (`current_user: User = ...`), an `AnnAssign` (`u: User = ...`), a
      direct constructor call (`u = User(...)`), or an ORM query result
      (`u = db.query(User)....first()`, `for u in db.query(User)...`).
      A name absent from the map is never touched.
    - `_iter_top_level_functions(tree)` — module-level functions and one
      level of class methods (not a full recursive `ast.walk`), so a
      nested closure's attribute accesses are swept into its enclosing
      top-level function's scope exactly once, matching this file's own
      existing precedent in `_patch_missing_create_update_fields`
      ("scope per function").
  - The rewrite itself: for each attribute node `obj.attr` where `obj` is
    resolvable to a known model class (via `_infer_model_typed_names`, or
    a bare `ClassName.attr` reference), and `attr` is a detected
    mismatch, replace **only that exact source span**
    (`node.value.end_col_offset` to `node.end_col_offset` on that line) —
    never a pattern-wide substitution. Multi-line attribute expressions
    are skipped entirely (rare, and unsafe to column-splice).

- `backend/tests/reliability/test_sql_constructor_and_auth_repairs.py`
  - Updated `test_attr_access_mismatch_ambiguous_file_wide_not_class_qualified`
    (which explicitly *documented* the bug as known, unfixed behavior) to
    `test_attr_access_mismatch_scoped_to_typed_object_not_file_wide`,
    asserting the fixed behavior instead.

- `backend/tests/reliability/test_exp073_attr_scope_fix.py` (new) — 12
  regression tests, see §3.

## 3. Replay / test results

All run via `venv/Scripts/python.exe <file>` (no pytest installed in this
environment) and independently via `python -m py_compile` for a syntax
gate.

**New dedicated suite** — `tests/reliability/test_exp073_attr_scope_fix.py`:
**12/12 passed**, covering every category the mission specified:

| # | Test | Category |
|---|------|----------|
| 1 | `test_correct_mismatch_via_typed_param_is_still_fixed` | Correct mismatch |
| 2 | `test_correct_mismatch_via_orm_query_result_is_fixed` | Correct mismatch (ORM path) |
| 3 | `test_correct_mismatch_via_constructor_call_is_fixed` | Correct mismatch (ctor path) |
| 4 | `test_unrelated_untyped_object_not_touched` | Unrelated object |
| 5 | `test_unrelated_object_typed_as_a_different_model_not_touched` | Unrelated object (Exp072 shape) |
| 6 | `test_multiple_classes_each_evaluated_independently` | Multiple classes |
| 7 | `test_repeated_attribute_name_on_two_instances_of_same_class_both_fixed` | Repeated attribute names |
| 8 | `test_nested_function_own_typed_variable_is_fixed` | Nested functions |
| 9 | `test_auth_template_regression_req_display_name_survives_username_mismatch` | Auth template (exact Exp072 replay) |
| 10 | `test_idempotent_on_already_fixed_file` | Regression replay |
| 11 | `test_noop_when_attribute_already_valid_on_model` | Regression replay |
| 12 | `test_untyped_variable_with_no_evidence_is_never_rewritten` | Regression replay |

Test 9 instantiates the **real** `_build_auth_routes_template()` (the
exact function that produced Exp072's `auth_routes.py`) paired with a
`User` model that has `username` but no `display_name` — reproducing
Exp063/Exp072's corruption precisely. Before this fix: `req.display_name`
→ `req.username` (broken). After: `req.display_name` survives untouched,
`AttributeError`-free.

**Full `backend/tests/reliability/` suite** (44 files, run individually):
every file's pass rate is unchanged from baseline except the one
deliberately-updated test above (now correctly `30/30` instead of
`29/30`) and the new `12/12` file. Two pre-existing, unrelated failures
(`test_database_patcher_and_relationships.py`,
`test_inline_chain_repairs.py`) were confirmed via `git stash` to exist
identically on the unmodified baseline — not caused by this change.

**CRM replay** (Contact model missing `description`, has `notes`; Deal
model in the same file genuinely has `description`):
```
contact.description → contact.notes   (correctly rewritten)
deal.description     → deal.description (left untouched -- correct)
```
Under the old blanket regex, `deal.description` would ALSO have been
rewritten to `deal.notes`, corrupting a field that genuinely exists on
`Deal`. **Confirmed: old implementation would corrupt; new implementation
does not.**

**Inventory replay** (Product model missing `title`, has `name`; a
`ReportRequest` object in the same file genuinely has `.title`):
```
item.title   → item.name    (correctly rewritten)
report.title → report.title (left untouched -- correct)
```
Same result: old implementation would have corrupted `report.title`;
new implementation does not.

**Exp063 auth corruption replay**: covered by dedicated test #9 above —
passes.

## 4. False-positive analysis

`generated_projects/` is empty in this environment (git-ignored, cleared
between runs) — no live project artifacts to sweep directly. Swept
`backend/llm_cache/` instead (6,176 cached LLM responses; individual
`backend_file_*.json` entries of the form `{"path": ..., "content":
...}`, not grouped by originating project/run, so an exact per-project
model+route replay across the whole corpus isn't reconstructable from
this cache alone).

**Proxy measure**: parsed every cached `app/routes/*.py` file (1,085
found) via AST and counted files where **two or more distinct object
names** share the same attribute word from `_FIELD_SYNONYMS_PATCHER`'s
key set (the same-file, same-attribute-name, different-object shape that
is a precondition for corruption under the old blanket regex):

```
route files scanned:                                          1085
files with the collision-prone shape (>=2 distinct objects
  sharing a risky attribute name in the same file):             374
rate:                                                          34.5%
```

Example hits (file, attribute, colliding object names):
- `auth_routes.py`: `display_name` → `{user_in, user, db_user}`,
  `username` → `{User, user_in}`
- `task_routes.py`: `title`/`description`/`priority`/`due_date` →
  `{Task, task, task_in}` (3-4 distinct names sharing risky words)
- `user_routes.py` (×2 separate cache entries): `username` →
  `{User, user, user_in}` / `{user, user_in, UserModel}`

**Caveat, stated plainly**: this 34.5% is an *upper-bound exposure
measure*, not a corruption-rate measure — it only proves the attribute
name is used on multiple distinct objects in the same file, not that one
of those objects is a real SQLAlchemy model actually missing that column
(the second condition required for the old code to fire at all). The
true corruption rate is narrower. But it demonstrates the exposure
surface was broad and structurally common (roughly 1 in 3 route files),
consistent with Exp072's finding that this bug shape occurred
**twice, independently, in a single 4-app canary run** (`todo` and
`blog_cms`) — i.e., this was not a rare edge case.

## 5. Recommendation: extend Exp064-style semantic validation to deterministic patchers?

**Yes, but as a second layer, not a replacement for this fix.** Exp072
already found that Exp064's semantic-consistency guard (which wraps
`write_fix()`, the single-file LLM-repair writer) does **not** cover this
patcher's own `.write_text()` call inside `run_deterministic_patches()`
— a second, independent write path. This experiment closes the
*specific, confirmed* bug at its source (the rewrite is no longer
capable of touching an unrelated object, by construction — not merely
detected-and-rejected after the fact). That is strictly better where it
applies: zero risk instead of catch-and-reject risk, and it costs nothing
per generation (AST parse of files already being read).

However, Exp064-style semantic validation still has independent value as
a *defense-in-depth* backstop for the deterministic-patcher family as a
whole (`_patch_attr_access_mismatches` was not the only function
performing a project-wide `.write_text()` outside `write_fix()`'s
protection — `_patch_ownership_fk_attribute_drift`,
`_patch_missing_create_update_fields`, and others in the same file share
that path). A future experiment should scope *specifically* whether
wrapping `run_deterministic_patches()`'s writes in the same
semantic-consistency check Exp064 already built for `write_fix()` is
cheap enough to do unconditionally — not proposed or implemented here,
per this experiment's explicit "fix only this confirmed bug" rule.
