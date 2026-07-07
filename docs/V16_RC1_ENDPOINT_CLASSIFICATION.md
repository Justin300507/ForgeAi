# MissingEndpoint Classification (Priority 3, V16 RC1)

**Date**: 2026-07-07
**Method**: classification only, no fixes. Grepped every canary log
produced by this session (`m2` through `m5`, 6 log files, ~50
occurrences total — a much larger, fully-contextualized sample than
`patterns.json`'s 5-example rolling window) rather than relying on the
rolling sample alone. Cross-referenced flagged occurrences against each
log's own later runtime/journey output to distinguish transient
(self-corrected within the same attempt) from persisting (still broken
at final scoring) failures.

## Headline finding: most raw `MissingEndpoint` occurrences are transient, not persisting

There are two distinct message shapes in the logs, corresponding to two
different validators (`endpoint_validator.py`'s `validate_endpoints` vs.
`validate_frontend_api_calls`):

- **Shape A** — `Missing endpoint {METHOD} {path} (expected in
  app/routes/X.py)`, no suffix. Architect-plan vs. actual-backend-routes
  comparison, emitted **during the mid-run validation loop**, before
  later repair passes run.
- **Shape B** — same, plus `-- called from {file}.jsx but never
  implemented on the backend`. Frontend-call vs. actual-backend-routes
  comparison.

**Shape A is overwhelmingly transient.** Direct proof from
`m4_canary_dictunpack_run.log`: line 144 flags `Missing endpoint POST
/tasks (expected in app/routes/task_routes.py)` for todo early in that
run — and the *same log*, later, shows `Journey PASS — 11 passed / 0
failed` and `Create entity: 200 id=1` / `201 id=1` for that identical app,
four separate times across the run. The endpoint was never actually
missing from the final artifact; the validator caught it mid-repair-loop,
before a later pass (or an already-in-flight fix) resolved it, and the
diagnostic got counted anyway. The same pattern is visible for crm's
`/contacts` endpoints and blog_cms's `/posts` endpoints across every log
that flagged them — none of these recur in final journey/runtime output.

This is the same phenomenon Experiment 022 found for a `ModuleNotFoundError`
occurrence (self-corrected mid-run, not a persisting bug) — now confirmed
to apply much more broadly to Shape A `MissingEndpoint` counts. **A large
fraction of the raw 44-count `MissingEndpoint` total in `patterns.json`
is very likely inflated by transient, already-self-corrected diagnostics,
not a true measure of persisting drift.**

## Shape B — the one confirmed, persisting, reproducible pattern

`GET /articles?author_id=${userId}`, `GET /authors/${userId}`, `POST
/articles` (called from `AuthorDashboardPage.jsx`/`CreatePostPage.jsx`)
against a backend that only implements `/posts` — this **exact same
triplet recurs identically across every single blog_cms canary run this
session** (6 out of 6 logs that reached blog_cms's generation stage),
regardless of which unrelated fix was under test that day. This is not
noise — it's a systematic, reproducible LLM behavior specific to this
app's prompt: the frontend generator consistently invents `Article`/
`Author` as the domain concept, while the backend generator consistently
implements `Post`/uses `current_user` for authorship, with no separate
author-listing endpoint. Confirmed genuinely persisting: it appears in
final validation output and (per Experiment 020's original finding) it's
the actual cause of a real, user-visible blog_cms regression, not a
mid-loop artifact.

## Classification against your five categories

| Category | Evidence found | Verdict |
|---|---|---|
| **Validator bug** | The WS/WEBSOCKET method-label mismatch (found in ADR-003 investigation) — already fixed at the source 2026-06-30, predates this session | Resolved, not a current contributor |
| **Generation omission** | Shape A occurrences (todo's `/tasks`/`/users`, crm's `/contacts`, blog_cms's `/posts`) | **Mostly transient** — self-corrected within the same attempt before final scoring, per the direct proof above. Not a persisting omission in the final artifact for any case checked this session. |
| **Naming mismatch** | blog_cms's Article/Author vs. Post — 6/6 recurrence | **The one confirmed, persisting, reproducible case.** |
| **Planner issue** | No clean evidence isolating a planner-only cause distinct from the naming-mismatch case above (the naming mismatch itself likely originates from the architect/planner stage describing the feature ambiguously enough that frontend and backend generation diverge) | Overlaps with "Naming mismatch" above rather than being a separate cause |
| **Frontend issue** | No case found this session where the frontend called something reasonable and the *backend* was simply wrong; all Shape B cases point to a genuine frontend/backend concept disagreement, not a one-sided frontend defect | Not a distinct observed category this session |

## Important caveat on generalizability

This classification is based on **this session's fixed 3-app canary**
(todo/blog_cms/crm — the same three apps every run, by design, per this
project's canary methodology). `patterns.json`'s full 385-run corpus
spans many different generated app ideas (`bug_tracker`,
`classroom_manager`, `assignments`, etc., per its stored examples). **A
narrow-but-confident finding here** ("in this session, only one naming-
mismatch pattern persists, and it recurs deterministically") **does not
prove the wider historical corpus's 44-count is dominated by the same
single cause** — it may contain many different, one-off naming
mismatches across different app domains, not one repeating pattern. What
this session's evidence *does* establish confidently: (1) the transient/
persisting distinction is real and material — don't trust raw
`MissingEndpoint` counts without checking final-state persistence first,
and (2) when a naming mismatch is real, it's fully deterministic and
reproducible per-app, not a one-time fluke — so if it recurs across many
different apps' `patterns.json` history too, it would likely also be
per-app-deterministic there, not randomly distributed.

## Recommendation (classification only — no fix decision made)

Given a genuinely deterministic solution exists only for the confirmed
persisting pattern (naming mismatch) and even that is a single confirmed
instance rather than a demonstrated broad class across many apps, the
prior recommendation (ADR-003 investigation: don't invest in a general
`EndpointContract`/alias-resolution mechanism yet) is **reinforced, not
weakened**, by this closer look. The one clear next step that doesn't
require guessing: re-run the standard canary with this specific lens
(check final journey/runtime state, not just raw error counts) across a
few more cycles, or — more efficiently — spot-check a handful of
`patterns.json`'s other stored app examples (`bug_tracker`,
`classroom_manager`) the same way this session's logs were checked, to
see whether the transient/persisting split holds up outside blog_cms/
todo/crm before deciding whether `MissingEndpoint` deserves any further
investment at all.
