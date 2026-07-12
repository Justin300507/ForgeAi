# Experiment 063 — Pydantic AttributeError Root Cause Investigation

2026-07-12. Pure offline investigation — no generation, no LLM calls, no
fixes, no prompt changes. All evidence below comes from reading the
actual, real artifacts already on disk from Exp056/058/061/062's live
runs: `generated_projects/todo_list_app`, `generated_projects/forge_blog_cms`,
`generated_projects/inventory_manager`, `generated_projects/simple_crm`
(confirmed by file-modification timestamps to be the exact projects those
experiments produced, not stale leftovers — several older same-named
directories exist and were excluded), plus `backend/app/services/deterministic_patcher.py`'s
source (the static auth template), `fix_logs.json` inside each project,
and `backend/app/prompts/runtime_fix_prompt.py`.

## Headline finding

**Two distinct root causes share one symptom shape, not one root cause.**

1. **`todo` and `blog_cms`'s `SignupRequest.username`/`User.name` crashes
   are a REPAIR-INTRODUCED regression**, proven with certainty: the
   deterministically-injected `auth_routes.py` template is byte-provably
   correct (read directly from `deterministic_patcher.py`, both
   role-aware and non-role-aware branches), yet both apps' final,
   on-disk `auth_routes.py` contains the **identical** corrupted line
   `user = _make_user(req.email, req.password, req.username)` where the
   template always generates `req.display_name`. This is not
   coincidence — the SAME exact string appears in both apps.
2. **`inventory`'s `ProductCreate.price` crash is a first-pass
   generation-quality issue**, not repair-introduced — `ProductCreate`
   is a wholly LLM-generated schema (no deterministic template exists
   for arbitrary business entities), and unlike the auth case, the
   repair loop **successfully resolved** it by the final snapshot (no
   `.price` reference remains in `product_routes.py`; the error is
   present in early `validate_project()`/runtime calls and absent from
   the point it's fixed onward).

**What both share, and what makes this the highest-ROI finding: no
validator in ForgeAI — none of the 15 migrated, none of the 8 legacy —
checks whether a route handler's attribute access on a Pydantic request
object (`req.X`) matches a field that object's own class actually
declares.** Confirmed by direct grep across every validator file:
zero matches for anything resembling this check. This is a real,
specific, previously-uncatalogued gap — distinct from
`validate_schema_model_consistency` (which checks Pydantic-schema vs.
SQLAlchemy-model nullable/required consistency, a different pair of
artifacts entirely) and `undefined_symbol_validator` (which checks
whether a *name* is defined anywhere in the file via AST — `req` IS
defined, as a function parameter, so this check correctly finds nothing
wrong; it doesn't reason about `req`'s *type*'s declared attributes at
all).

---

## Field lineage: `todo` (`SignupRequest.username`)

```
Idea ("todo app with auth")
  ↓
Planner (metadata.json "plan") — data_entities_detail defines User
  with standard auth fields; does not mention "username" as a
  dedicated field distinct from email-based identity (see §Evidence)
  ↓
Architecture (metadata.json "architecture.database_schema") — defines
  the User table's columns; the deterministic auth injection doesn't
  consume this for SignupRequest's shape (SignupRequest is a fixed,
  non-architecture-driven template — see below)
  ↓
Backend generation (initial pass) — writes app/models/user.py
  (SQLAlchemy) per Architecture; does NOT write auth_routes.py itself
  in the observed case (see next stage)
  ↓
[DIVERGENCE DOES NOT OCCUR HERE] Deterministic injection
  (_patch_auth_routes, deterministic_patcher.py:2181) — injects the
  known-good, CORRECT template (SignupRequest: email/password/display_name;
  signup() calls _make_user(req.email, req.password, req.display_name))
  IF the file is missing or lacks the `_read_password` sentinel.
  PROVEN CORRECT by direct source read, both template branches.
  ↓
Static validation (validate_project) — validate_frontend_auth_fields
  correctly detects the FRONTEND mismatch ("RegisterPage.jsx POSTs...
  fields ['password','username'] but no 'email'") — this is a REAL,
  correctly-caught issue, but it's about the FRONTEND, not auth_routes.py
  ↓
[<<< DIVERGENCE OCCURS HERE >>>] Repair (LLM-driven fix, patch_file or
  runtime-fix strategy) — evidence: fix_logs.json's captured
  RegisterPage.jsx fix ADDS an email field but LEAVES `username` in
  place (`const [username, setUsername] = React.useState('')`, still
  validated in handleSubmit). A separate, uncaptured-by-fix_logs.json
  fix (very likely the runtime-fix loop, which also calls write_fix but
  isn't logged to fix_logs.json in this project snapshot) then edited
  auth_routes.py's signup() handler to read `req.username` — most
  likely reasoning "the frontend sends username, so the backend should
  read username" — WITHOUT correspondingly adding `username` to the
  `SignupRequest` class in the SAME file. This is a self-consistency
  failure WITHIN one LLM-generated file, not a cross-file issue.
  ↓
Validation (re-run) — no validator catches this (see Headline
  finding) — the file is syntactically valid (write_fix's ast-based
  guard would have caught a syntax error, but this is a semantically
  wrong, syntactically fine attribute access)
  ↓
Runtime — AttributeError: 'SignupRequest' object has no attribute
  'username', on every signup/register attempt. Confirmed present in
  the FINAL on-disk snapshot — never resolved. Stagnation guard
  (Exp057) correctly gives up after the fix doesn't change the
  signature, per Exp058/061's own findings.
```

## Field lineage: `blog_cms` (`SignupRequest.username`, identical shape)

Same lineage as `todo`, converging at the identical divergence point.
**Evidence of shared mechanism, not independent coincidence:**
`generated_projects/forge_blog_cms/app/routes/auth_routes.py` line 96
is **byte-identical** to `todo_list_app`'s: `user = _make_user(req.email,
req.password, req.username)`. The runtime log shows the SAME
`AttributeError: 'SignupRequest' object has no at[tribute 'username']`
recurring 4 times across the run (`benchmark_results/exp062/blog_cms.log`
lines 687/853/1253/1662) — the stagnation guard's own "identical failure
signature" detection would have caught this as unchanged, consistent
with Exp057/058/061's confirmed-working stagnation logic.

## Field lineage: `inventory` (`ProductCreate.price`, different shape)

```
Idea ("inventory management system")
  ↓
Planner / Architecture — Product entity defined; no deterministic
  template exists for Product (unlike User/auth, which has one)
  ↓
[<<< DIVERGENCE LIKELY OCCURS HERE >>>] Backend generation (initial
  LLM pass) — writes app/schemas/product.py (ProductCreate: category_id,
  sku, name, unit_cost, current_stock, reorder_threshold — NO "price"
  field, confirmed by direct read of the current file) and
  app/routes/product_routes.py referencing `.price` somewhere in a
  handler — the two files disagree on the entity's own field set. This
  is the SAME class of self-consistency issue as the auth case, but
  originating at INITIAL GENERATION rather than repair, per the
  evidence below.
  ↓
Static validation — validate_schema_model_consistency and
  validate_endpoints both fired (native diagnostics, confirmed via
  Exp062's observer) but neither checks route-code-vs-schema attribute
  access — same validation gap as the auth case
  ↓
Runtime — AttributeError: 'ProductCreate' object has no attribute
  'price' (benchmark_results/exp062/inventory.log lines 809, 995)
  ↓
Repair (runtime-fix loop) — SUCCEEDED here, unlike the auth case:
  confirmed via Exp062's observer that this error's underlying
  validate_project() error count dropped and the .price reference is
  ABSENT from the final on-disk product_routes.py. Plausible fix: LLM
  renamed the reference to `unit_cost` (the schema's actual analogous
  field) or removed it — not confirmed further (would require a
  pre-fix snapshot this investigation doesn't have; not pursued, per
  "no fixes" and "stop once the divergence point is known").
```

**Why this case's divergence point is "likely" not "proven" the way
the auth case is:** there is no bug-free static template to compare
`product.py`/`product_routes.py` against (unlike `SignupRequest`, which
has one) — `Product` is an arbitrary, fully LLM-generated business
entity. Without a pre-fix snapshot of `product_routes.py` (overwritten
by the successful repair), this investigation cannot prove with the
same certainty as the auth case that the FIRST version already had the
mismatch vs. an early fix introducing it. The **balance of evidence**
(no equivalent "known-good template got corrupted" mechanism exists for
arbitrary entities like `Product`, and `validate_schema_model_consistency`'s
own diagnostics for this app were about *nullable* mismatches, not this
specific attribute) points to initial generation as the more likely
origin — reported as "likely," not "proven," per this experiment's own
confidence-labeling requirement.

## Field lineage: `crm` (no failure — control case)

```
Idea ("CRM") → Planner → Architecture → Backend generation
  (first-pass output was already good enough)
  ↓
Static validation — passed cleanly (Compilation/Runtime/CRUD all
  passed dimension checks per Exp062)
  ↓
[NO REPAIR PASS EVER RAN] — fix_attempts=0 (confirmed, Exp062)
  ↓
auth_routes.py — confirmed via direct read: line 96 is
  `user = _make_user(req.email, req.password, req.display_name)` —
  IDENTICAL to the pristine deterministic template, byte-for-byte.
  No corruption, because no repair pass ever touched it.
```

---

## Questions, answered

**1. Where does the incorrect field originate?**
Two different answers for two different mechanisms, not one:
- `todo`/`blog_cms` (`SignupRequest.username`): **Repair** — proven, not
  inferred. The deterministic injection and initial generation are both
  exonerated by direct evidence (the static template is provably
  correct; the corruption only exists in the post-repair snapshot).
- `inventory` (`ProductCreate.price`): **Backend generation** (LLM's
  initial pass), likely but not proven with the same certainty — no
  equivalent "known-good baseline" exists for arbitrary entities to
  compare against.

**2. Does the model invent fields, or reference deleted fields?**
**Invents a reference to a field that was never there** — in both
mechanisms, the ATTRIBUTE ACCESS (`req.username`, `.price`) was added/exists
without the corresponding SCHEMA FIELD ever being added. This is not a
"field was deleted" case (nothing shows evidence of `username` or
`price` existing on the schema at an earlier point and later being
removed) — it's the schema and the code that reads it becoming
inconsistent, with the *access* being the wrong side in both observed
cases (confirmed for auth: the template's correct version never had
`username`; the runtime-fix response added a read for a field it never
also defined).

**3. Are models correct while routers are wrong, or vice versa?**
For the auth case: the SQLAlchemy **model** (`User`) is not the issue at
all — `_make_user`'s own logic (lines 2018-2019 of the template)
correctly auto-derives `username` from email IF the model has a
`username` column, entirely independent of `SignupRequest`. The **router
(auth_routes.py's handler code)** is wrong; the **Pydantic request
schema** (`SignupRequest`, defined in the same file) is "correct" in
that it matches the deterministic template exactly — it's the code that
*reads* it that's wrong. For inventory: the **Pydantic schema**
(`ProductCreate`) doesn't have `price`; whether the **router** was wrong
to reference it or the **schema** was wrong to omit it is exactly the
divergence question §Field lineage: inventory couldn't resolve with full
certainty — both are plausible given no pre-fix snapshot survives.

**4. Does repair modify them?**
**Yes, for both — but with opposite outcomes.** Repair (LLM-driven fix)
directly modified `auth_routes.py` in the todo/blog_cms case and made
things WORSE (introduced the persisting bug). Repair also touched
`product_routes.py` in the inventory case and made things BETTER
(resolved the bug, whatever its origin). Repair is not uniformly
harmful — the auth case is the outlier, not the norm, based on this n=2-vs-1
sample.

**5. Does validation detect the mismatch?**
**No — confirmed by direct code inspection, not inference.** Grepped
every validator function across every validator file for anything
checking route-code attribute access against a Pydantic class's declared
fields: zero matches. `validate_schema_model_consistency` checks a
different pair of artifacts (SQLAlchemy model vs. Pydantic schema
nullable/required flags) and would not catch this even in principle —
it never looks at ROUTE code at all.

**6. Does repair fail to repair it?**
**Yes, for the auth case specifically — and the mechanism is now
understood, not just observed.** `write_fix` (per Exp060's own reading
of `fix_writer_service.py`) validates that returned code is
**syntactically** valid (`ast.parse`-style guard, refuses truncated/invalid
writes) but has **no check for semantic self-consistency** — whether
every attribute access in the LLM's own returned file matches a field
declared in that SAME returned file. The runtime-fix prompt
(`runtime_fix_prompt.py`) asks the LLM to "return the ENTIRE corrected
file" and "fix the ROOT CAUSE," giving it the full current file content,
but provides no automated cross-check of the LLM's own internal
consistency before writing the result to disk. This is a real,
structural, previously-uncatalogued gap in the write-time safety net —
distinct from (and not fixed by) Exp054's `_patch_param_order` write-time
validation (which only checks syntax, the same limitation).

**7. Does CRM avoid the problem, and why?**
**Yes — because it needed zero repair attempts, not because of a
different architecture/prompts/schema shape/entity count.** Confirmed
directly: `crm`'s `auth_routes.py` is byte-identical to the pristine
deterministic template; `fix_attempts=0` (Exp062). The repair pathway
that introduces the auth-case corruption in the other two apps simply
never had an opportunity to run against `crm`, because `crm`'s
first-pass generation was already good enough. This reframes "why does
CRM avoid it" from an architecture question to a **reliability
question**: any app whose first pass is clean enough to skip repair
entirely is safe from this specific corruption mechanism; any app that
needs even one repair pass touching auth-adjacent code is at risk.

---

## Confidence

- **`todo`/`blog_cms` auth-case root cause (repair-introduced
  regression, specific mechanism identified)**: **High.** Backed by
  direct source comparison against a provably-correct static template,
  byte-identical corruption across two independent app generations, and
  a structurally sound explanation (no write-time semantic-consistency
  check) for why repair can introduce and then fail to self-correct this
  exact class of bug.
- **`inventory` case root cause (initial-generation mismatch)**:
  **Medium.** The divergence point is well-reasoned and evidence-consistent,
  but not proven with the same certainty as the auth case — no
  surviving pre-fix snapshot, no equivalent "known-good template" to
  compare against for an arbitrary business entity.
- **The shared validation gap (no validator checks route-code attribute
  access against Pydantic schema fields)**: **High.** Directly confirmed
  by exhaustive grep across every validator file in the codebase, not
  inferred.
- **Ranked hypotheses for the underlying "why does the LLM do this" question**
  (not fully resolved — flagged as the natural Exp064 follow-up, not
  pursued further here per "stop once the divergence point is known"):
  1. *(Most likely)* The runtime-fix prompt's framing ("fix the ROOT
     CAUSE," "return the ENTIRE corrected file") combined with a crash
     traceback that shows the frontend sending `username` leads the LLM
     to "reconcile" the backend toward the frontend's (still-imperfect)
     field naming, but the single-shot "return the whole file" format
     has no mechanism forcing the LLM to keep its own class definition
     and handler body mutually consistent.
  2. *(Plausible)* The LLM's training-data prior for "signup form" code
     more commonly uses `username` than `email`-only identity, biasing
     it to reintroduce `username`-shaped code even when explicitly
     shown a `email`/`display_name`-only schema.
  3. *(Less likely, not ruled out)* A caching/replay interaction — both
     `todo` and `blog_cms` runs this session had heavy LLM cache reuse
     (Exp056/058/061/062's own findings); if the SAME cached runtime-fix
     response is being replayed for structurally similar crashes across
     different apps, that would also explain the byte-identical
     corruption without needing a "the LLM tends to do this" behavioral
     claim. **Not distinguished from hypothesis 1 by the evidence
     gathered this cycle** — a genuine open question for Exp064.

## Recommendation for a future cycle (not attempted here — investigation only)

The single highest-leverage fix implied by this investigation is **not**
a new validator alone — it's a **write-time self-consistency check**:
before `write_fix` accepts an LLM's "entire corrected file" response,
verify that every `self.`/parameter-attribute access on a locally-defined
Pydantic/dataclass request type in that SAME file resolves to a field
the class actually declares (a bounded, single-file AST check, not a
cross-project one). This would have caught the auth-case corruption at
the exact moment it was about to be written, using exactly the kind of
write-time validation Exp054 already established as a working pattern
for syntax — extended here to one additional, well-scoped semantic
check. Flagged as the clear Exp064 candidate; **not implemented**, per
this experiment's explicit "no fixes" rule.

## What was NOT done (explicitly out of scope, per instructions)

No fix was implemented. No prompt was changed. No new validator was
written or migrated. The ranked hypotheses in §Confidence for *why* the
LLM produces this pattern were not further disambiguated (would require
either fresh, cache-bypassed generations or direct inspection of the
actual runtime-fix LLM responses in the cache — both explicitly avoided
to keep this cycle at $0 and within "investigation only").
