# Experiment 074 — Live Validation of AST-Scoped Attribute Repair (Exp073)

2026-07-12. Measurement only, per this experiment's own explicit rule —
**no code changes made**. One live canary (of the 3 permitted), Cerebras,
`--no-deploy`, covering Todo / Blog CMS / Inventory (label
`exp074-validation-r1`), run via a new one-off measurement script,
`scripts/exp074_canary.py`, that reuses `run_canary.py`'s internals
without modifying that file — same precedent Exp072's own
`scripts/exp072_canary.py` established. Stopped after 1 of 3 permitted
canaries: the single run produced clear, directly-inspectable evidence
(a live invocation of the exact function Exp073 changed, on the exact
ambiguous-attribute-name shape that caused Exp072's corruption), so a
2nd/3rd canary would not have raised confidence further.

## Answering the headline question

**Has Exp073 eliminated the corruption observed in Exp072? Yes**, on the
one live data point this run produced. `_patch_attr_access_mismatches()`
fired 3 times (all in `blog_cms`'s `post_routes.py`) and every rewrite it
made was correctly scoped to the actual mismatched model instance; every
place the same attribute name appeared on a *different*, untyped, or
differently-typed object was left untouched — including the single
strongest piece of evidence: a line where the ambiguous attribute name
appears **twice on the same line, on two different objects**, and only
one of the two was rewritten.

## 1. Before/after comparison

| | Exp072 (2026-07-12, `exp072-validation-r1`) | Exp074 (2026-07-12, `exp074-validation-r1`) |
|---|---|---|
| Apps | todo, blog_cms, crm, inventory | todo, blog_cms, inventory (this experiment's own app set — CRM not included) |
| `_patch_attr_access_mismatches()` corruption | **Confirmed, 2 of 4 apps** (todo, blog_cms) — `req.display_name` → `req.username`, breaking a correctly-injected auth template | **Zero corruptions.** 3 invocations occurred (blog_cms only); all 3 correctly scoped to the real mismatched instance, zero unrelated objects touched |
| todo | score 73.44, `crud_ok: False` | **score 92.6 (A), `crud_ok: True`**, full 11/11-step journey pass, 0 fix attempts needed |
| blog_cms | score 71.49, `crud_ok: False` | score 70.0 (C), `crud_ok: True` on the final accepted state, but `runtime_ok: False` — blocked by an **unrelated** new-to-this-run failure (§3) |
| inventory | score 75.72, `crud_ok: False` | score 90.9 (A), `crud_ok: True`, journey passed after repair converged — but went through score dips (69→70→32→70→91, see §3) from an **unrelated** NOT-NULL-on-PUT bug |
| CRM | score 89.87, `crud_ok: True` | **Not re-tested this cycle** — outside this experiment's own app set (Todo/Blog CMS/Inventory per its own prompt); last known state is Exp072's, carried forward, not fresh data |
| Auth completeness | 9/14 forensic bundles had `/auth/register` 404 (pre-Exp071); Exp072 confirmed 0/4 apps had that failure post-Exp071, but 2/4 had the *new* `_patch_attr_access_mismatches` corruption instead | **0 `/auth/register`/`/auth/signup`/`/auth/login` 404s across every attempt in every app this run** (18 auth calls total, incl. repair retries — every first-attempt register/login returned 200; the only non-200s were the journey runner's own intentional negative-test 422s) |

Todo's jump from `crud_ok: False` (Exp072) to a clean 92.6/A pass this run
is consistent with — but not proof of — Exp073's fix, since todo never
actually triggered `_patch_attr_access_mismatches()` this run (the LLM's
raw generation this time didn't produce the specific field-name mismatch
that would have exercised it). The safest, directly-supportable claim is
narrower and stronger: **on the one app that DID exercise the patched
function this run (blog_cms), the fix worked exactly as designed.**

## 2. Replay comparison — specific verification

Instrumentation: `scripts/exp074_canary.py` monkeypatches
`app.services.deterministic_patcher._patch_attr_access_mismatches` for
the duration of the run only (the source file itself is never touched)
with a wrapper that snapshots every `app/routes/*.py` file immediately
before and after each real invocation and diffs them. Raw output:
`backend/benchmark_results/exp074_patcher_invocations.json`.

**3 invocations, all in `blog_cms`'s `post_routes.py`** (`update_post()`
handler). The generated `Posts` model (confirmed by reading
`generated_projects/forge_blog_cms/app/models/posts.py` directly) has
`title`/`content_markdown`/`cover_image_url`/`status` — no `description`
column. `_FIELD_SYNONYMS_PATCHER["description"] = [..., "title", ...]`
correctly resolves `title` as the synonym.

| Invocation | Detected mismatch | Before | After | Object type (AST evidence) |
|---|---|---|---|---|
| 1 | `Posts.description` missing, `title` present | `post.description = getattr(post_in, "description", post.description)` | `post.title = getattr(post_in, "description", post.title)` | `post` typed via `AnnAssign`/ORM-query result (`Posts` instance) |
| 2 | same | `post.description = (` | `post.title = (` | same |
| 2 (same line group) | same | `post_in.description if post_in.description is not None else post.description` | `post_in.description if post_in.description is not None else post.title` | **`post_in` (a `PostUpdate` schema instance, confirmed by reading `app/schemas/post.py` — `description` is a real, valid field there) is left completely untouched, twice, on the same line** — only the trailing `post.description` (the `Posts` model instance) was rewritten |
| 3 | same | (fix re-ran identically on a later repair pass; idempotent, 0 net additional change beyond re-confirming the same file state) | | |

This third row (invocation #2/#3's line) is the single most direct,
live confirmation of Exp073's fix: it reproduces the *exact* shape that
corrupted `req.display_name` in Exp072 — the same attribute word
(`description`) appearing on two different objects in the same
expression, one of which is genuinely correct and must survive. It did.

**Verify: no unrelated object modified — confirmed** for every
invocation this run produced. (`req.display_name` itself did not appear
as a live test case this run, since no app generated a `User` model
missing `display_name` this time — see the honest caveat in §1's table.
A dedicated offline regression test replaying that exact shape via the
real `_build_auth_routes_template()` already exists and passes:
`backend/tests/reliability/test_exp073_attr_scope_fix.py::test_auth_template_regression_req_display_name_survives_username_mismatch`.)

## 3. Remaining deterministic-patch / reliability risks (new failures — documented only, not fixed, per this experiment's rule)

Ranked by how directly they blocked this run:

1. **[HIGH] `blog_cms` — PUT/DELETE `/posts/{id}` return 405; no route
   registered.** `grep` of the final `post_routes.py` confirms only
   `GET /posts`, `GET /posts/{post_id}`, `GET /authors/{author_id}/posts`,
   `POST /posts` exist — no `PUT`/`DELETE` handler was ever generated for
   posts. This is the `MissingEndpoint` cluster's **non-auth sub-case**
   (`docs/RUNTIME_KNOWLEDGE_BASE.md`'s existing entry already documents
   the auth sub-case as resolved by Exp071; this is the general CRUD
   sub-case, still open, no dedicated repair path — confirmed still true
   this cycle). This, not any attribute-mismatch issue, is what capped
   `blog_cms` at 70.0/C this run.

2. **[MEDIUM] `inventory` — PUT (full-replace) sets `sku` to `NULL` on
   partial payloads.** `replace_product()`'s handler unconditionally
   writes every model column from the request body; the journey runner's
   PUT payload doesn't include `sku` (a `nullable=False` column), so
   SQLAlchemy raises `IntegrityError: NOT NULL constraint failed:
   products.sku`. Distinct from the already-resolved `NotNullViolationError`
   cluster (Exp012/013's fix targets the **CREATE** path specifically,
   per `docs/RUNTIME_KNOWLEDGE_BASE.md`'s own entry, "wasn't supplied a
   value by the create path") — this is the same symptom shape on the
   **UPDATE/replace** path, which Exp012's preflight patcher does not
   claim to cover. Self-resolved this run via the LLM repair loop (final
   score 90.9/A after 3 fix attempts), so not currently score-blocking,
   but the regression-then-revert cycle (scores dipped to 32.1 twice
   before converging — `[fix] REGRESSION: 2 new error(s)... (69.7 ->
   32.1)`, correctly caught and reverted by the existing safety net) cost
   3 of inventory's 5 permitted fix attempts and ~$0.07 in this run alone.

3. **[LOW, pre-existing, unrelated] `blog_cms` first-pass constructor
   bug**: raw generation's `create_post()` called
   `Posts(title=..., description=..., ...)` — an invalid literal
   constructor kwarg, `TypeError: 'description' is an invalid keyword
   argument for Posts`. Different bug shape from
   `_patch_attr_access_mismatches` (that function only rewrites `.attr`
   *access* expressions, never constructor kwargs) — self-resolved by
   the LLM fix loop within 1 attempt, not a `_patch_attr_access_mismatches`
   gap.

None of these three block or relate to Exp073's fix — logged here per
this experiment's own "if a new failure appears, document only, rank it"
instruction, not investigated further.

## 4. Recommendation for Exp075

Two independently-ranked, roughly-equal-effort candidates surfaced this
cycle, per §3:

- **MissingEndpoint (general CRUD sub-case)**: still the taxonomy's
  single largest unaddressed cluster (48 instances / 24.7% historically,
  per `docs/RUNTIME_FAILURE_CLUSTERS.md`) and, per this run, the actual
  score-capping cause for `blog_cms`. No deterministic patcher exists at
  all for "an endpoint the architecture plan promised was never
  generated" — every existing fix path is LLM-only
  (`missing_file_service.py::generate_missing_file()`), with no verified
  success-rate evidence. A **detection**-only static check already
  exists (`endpoint_validator.py`); the gap is repair reliability, not
  detection.
- **NOT-NULL-on-PUT (update-path variant of Exp012's gap)**: narrower in
  scope but directly reproducible from this run's own evidence, and a
  much smaller, more mechanical fix (mirror Exp012's create-path
  detection logic onto update/`PUT` handlers specifically — preserve
  existing column values for fields absent from the request instead of
  overwriting with `None`).

Recommend **Exp075 target the NOT-NULL-on-PUT gap first** — it is
smaller, has a directly analogous existing fix to extend
(`preflight.py::_fix_model_schema_notnull_gap`), and this run's own data
already gives a concrete before-state to replay. `MissingEndpoint`
remains the higher-value target long-term but needs a repair mechanism
built from scratch (no existing deterministic path to extend), which is
a larger scope than a single measure-and-fix cycle.

## 5. Is deterministic semantic validation (Exp064-style) still justified after this result?

**Weaker case than before, not eliminated.** Exp073's fix worked — this
run found zero corruptions from the specific bug it targeted, including
on the exact ambiguous shape that caused the original incident. That is
direct evidence the *structural* fix (make the rewrite provably scoped,
rather than detect-and-reject after the fact) is sufficient on its own
for *this* function. Exp073's own `docs/EXP073_SCOPE_FIX.md`
already narrowed the semantic-validation recommendation to "second layer
for the deterministic-patcher family as a whole, not required for this
specific fix" — this run doesn't change that: `run_deterministic_patches()`
still has other `.write_text()` calls outside `write_fix()`'s protection
(`_patch_ownership_fk_attribute_drift`, `_patch_missing_create_update_fields`,
etc.), and this run produced no NEW evidence either confirming or
refuting risk in those specific functions (none of them fired this run).
Recommendation unchanged from Exp073: not urgent, still worth scoping as
a future defense-in-depth pass, not blocking on Exp075.

## Deliverables

- `docs/EXP074_VALIDATION.md` (this file)
- `backend/scripts/exp074_canary.py` (new, measurement-only tooling —
  no `deterministic_patcher.py` changes)
- `backend/benchmark_results/exp074_patcher_invocations.json` (raw
  invocation-level capture)
- `backend/benchmark_results/canary_history.json` — new run appended
  (label `exp074-validation-r1`), same file/format every other canary
  uses
- Observatory: **no code changes needed**, confirmed live —
  `scripts/observatory.py` picked up the new run automatically
  (`Timeline points: 34`, up from 33 pre-run; `Canary: Healthy`),
  same result Exp072 found
- `docs/RUNTIME_KNOWLEDGE_BASE.md`, `docs/RUNTIME_FAILURE_CLUSTERS.md`,
  `docs/RELIABILITY_EVOLUTION.md` — short, cited addenda (not rewrites)
- `experiments.md` — this entry

**Cost: 1 Cerebras canary run**, 3 apps, 3 real generations + repair
loops. Total tokens: 102,708 (todo) + 139,811 (blog_cms) + 122,554
(inventory) = 365,073. Est. cost: $0.0616 + $0.0839 + $0.0735 = **$0.219**.
Per the task's own instruction, **NOT committed**.
